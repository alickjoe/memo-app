"""
AudioCapture 设备回退/降级采集测试
====================================

覆盖场景：
- 单路设备打不开（如蓝牙 HFP 端点非 float 混音格式触发 soundcard AssertionError）
  时自动回退到下一个可用设备，另一路不受影响；
- 整路（loopback/input）全部不可用时降级为单通道采集，不再整线阵亡；
- 设备切换回调的通知与去重；
- sc_pcm_patch 在非 Windows 平台为安全 no-op，且格式构造字节布局正确。
"""
import asyncio
import struct
import time

import audio.capture as cap_mod
import numpy as np
import pytest
from audio import sc_pcm_patch
from audio.capture import AudioCapture

# ==================== soundcard 假实现 ====================

class FakeRecorder:
    """模拟 soundcard recorder 上下文管理器"""

    def __init__(self, mic: "FakeMic"):
        self.mic = mic

    def __enter__(self):
        self.mic.entered += 1
        return self

    def __exit__(self, *args):
        self.mic.exited += 1
        return False

    def record(self, numframes=None):
        n = numframes or 1600
        self.mic.record_calls += 1
        return np.full((n, 1), 0.5, dtype=np.float32)

    def flush(self):
        pass


class FakeMic:
    def __init__(self, mic_id: str, name: str, *, is_loopback: bool = False,
                 fail_open: bool = False):
        self.id = mic_id
        self.name = name
        self.is_loopback = is_loopback
        self.fail_open = fail_open  # True 模拟 soundcard 非 float 格式 AssertionError
        self.entered = 0
        self.exited = 0
        self.record_calls = 0

    def recorder(self, samplerate=None, **kwargs):
        if self.fail_open:
            raise AssertionError()  # 与 soundcard mediafoundation 空消息断言一致
        return FakeRecorder(self)


class FakeSpeaker:
    def __init__(self, speaker_id: str, name: str):
        self.id = speaker_id
        self.name = name


class FakeSoundcard:
    def __init__(self, mics, speakers):
        self._mics = mics
        self._speakers = speakers

    def all_speakers(self):
        return list(self._speakers)

    def all_microphones(self, include_loopback=False):
        if include_loopback:
            return [m for m in self._mics if m.is_loopback]
        return [m for m in self._mics if not m.is_loopback]

    def default_microphone(self):
        real = [m for m in self._mics if not m.is_loopback]
        return real[0] if real else None

    def get_microphone(self, mic_id, include_loopback=False):
        for m in self._mics:
            if m.id == mic_id:
                return m
        return None


def _make_fake_sc(*, loopback_ok=True, input_fail_default=False):
    """构造典型设备拓扑：蓝牙耳机 loopback + 扬声器 loopback + Lenovo(坏) + 阵列(好)"""
    speakers = [
        FakeSpeaker("bt-hp", "Headphones (BT)"),
        FakeSpeaker("realtek-spk", "Speakers (Realtek)"),
    ]
    mics = [
        FakeMic("bt-hp", "Headphones (BT)", is_loopback=True, fail_open=not loopback_ok),
        FakeMic("realtek-spk", "Speakers (Realtek)", is_loopback=True),
        FakeMic("lenovo", "Microphone (Lenovo)", fail_open=input_fail_default),
        FakeMic("array", "Microphone Array (Realtek)"),
    ]
    return FakeSoundcard(mics, speakers), mics


def _wait_buffer(capture: AudioCapture, min_bytes: int = 100, timeout: float = 5.0) -> bool:
    """轮询等待采集缓冲区出现数据"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with capture._buffer_lock:
            if len(capture._buffer) >= min_bytes:
                return True
        time.sleep(0.05)
    return False


# ==================== 候选与打开逻辑 ====================

def test_device_candidates_order_current_first(monkeypatch):
    fake, _ = _make_fake_sc()
    capture = AudioCapture()
    capture._loopback_mic = fake.all_microphones(include_loopback=True)[1]
    monkeypatch.setattr(cap_mod, "sc", fake)

    candidates = capture._device_candidates("loopback")
    assert candidates[0].id == "realtek-spk"          # 当前选择优先
    assert [c.id for c in candidates] == ["realtek-spk", "bt-hp"]  # 去重、仅 loopback

    input_candidates = capture._device_candidates("input")
    assert [c.id for c in input_candidates] == ["lenovo", "array"]  # 仅真实麦克风


def test_open_channel_falls_back_to_next_device(monkeypatch):
    fake, mics = _make_fake_sc(input_fail_default=True)
    capture = AudioCapture()
    capture._input_mic = mics[2]  # 默认选中的 Lenovo（打不开）
    monkeypatch.setattr(cap_mod, "sc", fake)

    recorder, mic = capture._open_channel("input")
    assert recorder is not None
    assert mic.id == "array"  # 回退到可用的阵列麦克风


def test_open_channel_all_failed_returns_none(monkeypatch):
    fake, mics = _make_fake_sc(loopback_ok=True, input_fail_default=True)
    for m in mics:
        m.fail_open = True
    capture = AudioCapture()
    monkeypatch.setattr(cap_mod, "sc", fake)

    assert capture._open_channel("input") == (None, None)
    assert capture._open_channel("loopback") == (None, None)


# ==================== 采集线程端到端 ====================

async def test_capture_auto_fallback_and_mixing(temp_data_dir, monkeypatch):
    """默认麦克风打不开时自动回退，采集继续产出数据，另一路不受影响"""
    fake, mics = _make_fake_sc(input_fail_default=True)
    monkeypatch.setattr(cap_mod, "sc", fake)

    capture = AudioCapture()
    await capture.start("meeting-fallback")
    try:
        assert _wait_buffer(capture), "capture buffer stayed empty"

        # 输入设备自动回退到可用的阵列麦克风
        assert capture._input_mic.id == "array"
        # loopback 未受影响
        assert capture._loopback_mic.id == "bt-hp"
        assert mics[0].entered >= 1
        assert mics[3].entered >= 1
        # Lenovo 端点尝试打开但失败
        assert mics[2].entered == 0
    finally:
        capture.stop()
        capture._thread.join(timeout=3.0)


async def test_capture_degraded_loopback_only(temp_data_dir, monkeypatch):
    """loopback 全部打不开时降级为仅麦克风采集，并通知前端 loopback=none"""
    fake, mics = _make_fake_sc()
    for m in mics[:2]:
        m.fail_open = True  # 两个 loopback 设备都打不开
    monkeypatch.setattr(cap_mod, "sc", fake)

    capture = AudioCapture()
    await capture.start("meeting-degraded")
    try:
        assert _wait_buffer(capture), "degraded capture produced no data"

        assert capture._input_mic.id == "lenovo"  # 麦克风路正常
        assert capture._last_notified.get("loopback") == "none"
        assert capture._loopback_reader_thread is None  # 未启动 loopback 读取线程
    finally:
        capture.stop()
        capture._thread.join(timeout=3.0)


async def test_capture_recovers_when_device_appears(temp_data_dir, monkeypatch):
    """全部设备打不开后，设备恢复可用时采集线程自动恢复（如蓝牙 HFP 端点出现）"""
    fake, mics = _make_fake_sc()
    for m in mics:
        m.fail_open = True
    monkeypatch.setattr(cap_mod, "sc", fake)

    capture = AudioCapture()
    await capture.start("meeting-recover")
    try:
        await asyncio.sleep(1.2)  # 覆盖至少 2 次重试（0.5s 间隔）
        with capture._buffer_lock:
            assert len(capture._buffer) == 0  # 期间无数据
        assert capture._thread.is_alive()  # 线程仍在重试

        # 设备恢复可用（例如蓝牙 HFP 端点在通话建立后出现）
        for m in mics:
            m.fail_open = False
        assert _wait_buffer(capture), "capture did not recover after devices became available"
    finally:
        capture.stop()
        capture._thread.join(timeout=3.0)


# ==================== 设备切换回调 ====================

def test_fire_device_switched_dedupe_and_manual_override(monkeypatch):
    """自动通知按目标设备去重；手动切换强制通知"""
    capture = AudioCapture()
    capture._callback_loop = type("L", (), {"is_running": lambda self: True})()

    async def on_switched(*args):
        pass

    capture._on_device_switched = on_switched

    fired = []

    def fake_schedule(coro, loop):
        fired.append(coro.__name__)

    monkeypatch.setattr(cap_mod.asyncio, "run_coroutine_threadsafe", fake_schedule)

    capture._fire_device_switched("input", "a", "b")
    capture._fire_device_switched("input", "b", "b")  # 重复目标，去重
    assert len(fired) == 1

    capture._fire_device_switched("input", "b", "b", only_on_change=False)  # 手动切换
    assert len(fired) == 2


async def test_manual_switch_same_device_still_notifies(temp_data_dir, monkeypatch):
    fake, _ = _make_fake_sc()
    monkeypatch.setattr(cap_mod, "sc", fake)

    capture = AudioCapture()
    await capture.start("meeting-switch")
    try:
        assert _wait_buffer(capture)
        notified = []
        capture._last_notified = {}
        capture._callback_loop = type("L", (), {"is_running": lambda self: True})()

        async def on_switched(*args):
            pass

        capture._on_device_switched = on_switched
        monkeypatch.setattr(
            cap_mod.asyncio, "run_coroutine_threadsafe",
            lambda coro, loop: notified.append(coro.__name__))

        # 切到当前相同设备也要通知（用户主动操作）
        assert capture.switch_device("loopback", "bt-hp") is True
        assert notified  # switch_device 立即触发
        # 采集不中断
        await asyncio.sleep(0.5)
        assert capture._thread.is_alive()
    finally:
        capture.stop()
        capture._thread.join(timeout=3.0)


def test_set_callback_captures_running_loop():
    """在事件循环内设置回调时捕获 loop，供采集线程线程安全调度"""
    capture = AudioCapture()

    async def noop():
        pass

    async def run():
        capture.set_device_switch_callback(lambda *a: noop)
        return capture._callback_loop

    loop_ref = asyncio.run(run())
    assert loop_ref is not None
    # 非事件循环上下文调用则不记录 loop
    capture2 = AudioCapture()
    capture2.set_device_switch_callback(lambda *a: None)
    assert capture2._callback_loop is None


# ==================== sc_pcm_patch ====================

def test_sc_pcm_patch_noop_on_non_windows():
    """非 Windows 平台 apply() 安全跳过（CI/Linux 与 MagicMock soundcard 共存）"""
    assert sc_pcm_patch.apply() in (True, False)  # 不抛异常
    if sc_pcm_patch.sys.platform != "win32":
        assert sc_pcm_patch.apply() is False


def test_build_float_extensible_layout():
    """自构造的 float32 WAVEFORMATEXTENSIBLE 字节布局符合 Windows 定义"""
    blob = sc_pcm_patch.build_float_extensible(16000, 2)
    assert len(blob) == 40

    (wFormatTag, nChannels, nSamplesPerSec, nAvgBytesPerSec, nBlockAlign,
     wBitsPerSample, cbSize, wValidBits, dwChannelMask, guid) = struct.unpack(
        "<HHIIHHHHI16s", blob)
    assert wFormatTag == 0xFFFE          # WAVE_FORMAT_EXTENSIBLE
    assert nChannels == 2
    assert nSamplesPerSec == 16000
    assert nAvgBytesPerSec == 16000 * 2 * 4
    assert nBlockAlign == 2 * 4
    assert wBitsPerSample == 32
    assert cbSize == 22
    assert wValidBits == 32
    assert dwChannelMask == 0x3          # 立体声 FL|FR
    # KSDATAFORMAT_SUBTYPE_IEEE_FLOAT {00000003-0000-0010-8000-00AA00389B71}
    d1, d2, d3 = struct.unpack("<IHH", guid[:8])
    assert (d1, d2, d3) == (3, 0, 0x10)
    assert guid[8:] == bytes([0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71])


def test_is_float_extensible_signature():
    """float 检测与上游 assert 的经验值特征保持一致"""
    class Mix:
        class Format:
            wFormatTag = 0xFFFE
            cbSize = 22
        class SubFormat:
            Data1 = 0x100000
            Data2 = 0x0080
            Data3 = 0xAA00
            Data4 = (0, 56, 155, 113, 1, 2, 3, 4)
    assert sc_pcm_patch._is_float_extensible(Mix()) is True

    class PcmTag:
        class Format:
            wFormatTag = 0x0001  # WAVE_FORMAT_PCM
            cbSize = 0
        class SubFormat:
            Data1 = 0
            Data2 = 0
            Data3 = 0
            Data4 = (0,) * 8
    assert sc_pcm_patch._is_float_extensible(PcmTag()) is False


@pytest.mark.parametrize("channels,mask", [(1, 0x4), (2, 0x3), (4, 0x33), (3, 0)])
def test_channel_mask_lookup(channels, mask):
    blob = sc_pcm_patch.build_float_extensible(48000, channels)
    assert blob[20:24] == struct.pack("<I", mask)
