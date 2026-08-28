"""
soundcard WASAPI PCM 兼容补丁
=============================

背景：Windows 上部分音频端点（蓝牙 HFP 通话端点、OEM 增强音频驱动的虚拟端点等）
的共享模式混音格式不是 IEEE float 的 WAVEFORMATEXTENSIBLE，而是 16-bit PCM 等。
soundcard 的 mediafoundation 后端在 ``_AudioClient.__init__`` 中用一组硬编码
assert 强校验驱动返回的混音格式，遇到这类设备直接抛出无消息 ``AssertionError``，
导致设备完全无法打开（上游 issue #93，截至 0.4.6 仍未修复，维护者明确不修）。

补丁原理：soundcard 初始化流时本来就传了
``AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY``，
音频引擎本就支持对任意 float 格式的自动转换。因此：

- 驱动返回 float 扩展格式 → 走上游原逻辑（行为与未打补丁时完全一致，零回归）；
- 其他格式（PCM / 非扩展）→ 不再 assert，改为自行构造标准 float32
  WAVEFORMATEXTENSIBLE（40 字节，按 Windows 真实布局写入）传给 ``Initialize``。

安全边界：
- 仅在 win32 且 soundcard.mediafoundation 可导入时生效，幂等；
- 其余平台（Linux CI / macOS）为 no-op，对测试注入的 MagicMock soundcard 也安全。

注意：float 检测沿用上游"经验值"字段读取方式（上游 assert 对 SubFormat 的读取
偏移与标准布局存在历史差异，保持一致读取才能保证原路径判定与上游完全相同）。
"""
from __future__ import annotations

import collections
import logging
import struct
import sys

logger = logging.getLogger("memo.audio.scpatch")

_applied = False
_mf_module = None  # soundcard.mediafoundation 模块引用，apply() 成功后设置

# KSDATAFORMAT_SUBTYPE_IEEE_FLOAT = {00000003-0000-0010-8000-00AA00389B71}
_IEEE_FLOAT_GUID = struct.pack(
    "<IHH8s",
    0x00000003, 0x0000, 0x0010,
    bytes([0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71]),
)

# 标准 dwChannelMask（mmreg.h），按声道数取用；未知声道数时留 0 交给引擎处理
_STANDARD_CHANNEL_MASKS = {1: 0x4, 2: 0x3, 4: 0x33, 6: 0x3F, 8: 0x63F}

# 上游 assert 接受的 float 扩展格式的"经验值"特征（SubFormat 读取偏移与声明
# 布局有历史差异，此处刻意与上游 assert 逐字段保持一致，见模块 docstring）
_FLOAT_EXTENSIBLE_SIGNATURE = (0x100000, 0x0080, 0xAA00, (0, 56, 155, 113))


def build_float_extensible(samplerate: int, channels: int) -> bytes:
    """构造 40 字节 float32 WAVEFORMATEXTENSIBLE（标准 Windows 布局）"""
    mask = _STANDARD_CHANNEL_MASKS.get(channels, 0)
    return struct.pack(
        "<HHIIHHHHI16s",
        0xFFFE,                            # wFormatTag = WAVE_FORMAT_EXTENSIBLE
        channels,                          # nChannels
        int(samplerate),                   # nSamplesPerSec
        int(samplerate) * channels * 4,    # nAvgBytesPerSec
        channels * 4,                      # nBlockAlign
        32,                                # wBitsPerSample
        22,                                # cbSize
        32,                                # Samples.wValidBitsPerSample
        mask,                              # dwChannelMask
        _IEEE_FLOAT_GUID,                  # SubFormat
    )


def _is_float_extensible(mix) -> bool:
    """判断驱动混音格式是否为上游 assert 所接受的 float32 扩展格式

    读取方式与上游 assert 完全一致，保证原路径判定与未打补丁时相同。
    """
    try:
        return (
            int(mix.Format.wFormatTag) == 0xFFFE
            and int(mix.Format.cbSize) == 22
            and (
                int(mix.SubFormat.Data1),
                int(mix.SubFormat.Data2),
                int(mix.SubFormat.Data3),
                tuple(int(x) for x in mix.SubFormat.Data4[0:4]),
            ) == _FLOAT_EXTENSIBLE_SIGNATURE
        )
    except Exception:
        return False


def _patched_audio_client_init(self, ptr, samplerate, channels, blocksize,
                               isloopback, exclusive_mode=False):
    """替换 soundcard.mediafoundation._AudioClient.__init__

    与上游实现保持一致，仅替换混音格式校验/构造部分，其余逐行等价。
    """
    mf = _mf_module
    _ffi, _com, _ole32 = mf._ffi, mf._com, mf._ole32

    self._ptr = ptr

    if isinstance(channels, int):
        self.channelmap = list(range(channels))
    elif isinstance(channels, collections.abc.Iterable):
        self.channelmap = channels
    else:
        raise TypeError('channels must be iterable or integer')

    if list(range(len(set(self.channelmap)))) != sorted(set(self.channelmap)):
        raise TypeError('Due to limitations of WASAPI, channel maps on Windows '
                        'must be a combination of `range(0, x)`.')

    if blocksize is None:
        blocksize = self.deviceperiod[0] * samplerate

    ppMixFormat = _ffi.new('WAVEFORMATEXTENSIBLE**')
    hr = self._ptr[0][0].lpVtbl.GetMixFormat(self._ptr[0], ppMixFormat)
    _com.check_error(hr)

    mix = ppMixFormat[0][0]
    num_channels = len(set(self.channelmap))

    if _is_float_extensible(mix):
        # —— 上游原路径：在驱动结构体上原地改写（float 设备行为不变）——
        mix.Format.nChannels = num_channels
        mix.Format.nSamplesPerSec = int(samplerate)
        mix.Format.nAvgBytesPerSec = int(samplerate) * num_channels * 4
        mix.Format.nBlockAlign = num_channels * 4
        mix.Format.wBitsPerSample = 32
        mix.Samples = {"wValidBitsPerSample": 32}
        format_ptr = ppMixFormat[0]
    else:
        # —— PCM / 非 float 设备：自构造 float32 扩展格式，交给引擎自动转换 ——
        logger.info(
            "soundcard patch: device mix format is not float32-extensible "
            "(wFormatTag=%s, cbSize=%s); using built-in float32 format",
            int(mix.Format.wFormatTag), int(mix.Format.cbSize),
        )
        blob = build_float_extensible(samplerate, num_channels)
        buf = _ffi.new('char[]', blob)
        format_ptr = _ffi.cast('WAVEFORMATEXTENSIBLE *', buf)

    if exclusive_mode:
        sharemode = _ole32.AUDCLNT_SHAREMODE_EXCLUSIVE
    else:
        sharemode = _ole32.AUDCLNT_SHAREMODE_SHARED
    #             resample   | remix      | better-SRC | nopersist
    streamflags = 0x00100000 | 0x80000000 | 0x08000000 | 0x00080000
    if isloopback:
        streamflags |= 0x00020000  # loopback
    bufferduration = int(blocksize / samplerate * 10000000)  # hecto-nanoseconds
    hr = self._ptr[0][0].lpVtbl.Initialize(
        self._ptr[0], sharemode, streamflags, bufferduration, 0, format_ptr, _ffi.NULL)
    _com.check_error(hr)
    _ole32.CoTaskMemFree(ppMixFormat[0])

    # save samplerate for later
    self.samplerate = samplerate
    # placeholder for the last time we had audio input available
    self._idle_start_time = None


def apply() -> bool:
    """安装补丁；成功返回 True。

    幂等；非 Windows 平台或 soundcard 不可用时安全跳过（返回 False）。
    """
    global _applied, _mf_module
    if _applied:
        return True
    if sys.platform != "win32":
        logger.debug("sc_pcm_patch: skip (platform=%s)", sys.platform)
        return False

    try:
        import soundcard.mediafoundation as mf
        if not all(hasattr(mf, name) for name in ("_ffi", "_com", "_ole32", "_AudioClient")):
            logger.warning("sc_pcm_patch: soundcard internals not found, skip")
            return False
    except Exception as e:
        logger.warning("sc_pcm_patch: soundcard.mediafoundation unavailable (%s), skip", e)
        return False

    mf._AudioClient.__init__ = _patched_audio_client_init
    _mf_module = mf
    _applied = True
    logger.info("sc_pcm_patch: soundcard PCM mix-format patch applied")
    return True
