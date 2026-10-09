"""
声纹嵌入引擎 - Resemblyzer GE2E d-vector 封装

设计要点：
- 预训练模型随 Resemblyzer wheel 内置（pretrained.pt ~17MB），运行时零网络下载
- 安装使用 `pip install Resemblyzer --no-deps`，webrtcvad/librosa/scipy 依赖被刻意绕开：
  - resemblyzer.audio.trim_long_silences（依赖 webrtcvad）→ 用自带能量 VAD 替代
  - resemblyzer.audio.wav_to_mel_spectrogram（依赖 librosa+scipy）→ numpy FFT +
    torchaudio mel filterbank 复刻（torch 已作为 Silero VAD 依赖存在于源码模式）
  - embed_utterance 内部会调用 resemblyzer.audio（触发 librosa import），
    因此这里复刻其分片逻辑，直接调用 encoder.forward，完全不触碰 resemblyzer.audio
- torch 不可用时显式降级（available=False），调用方回退顺序标签
"""
import logging
import struct

import numpy as np

logger = logging.getLogger("memo.diarization")

# 与 resemblyzer.hparams 对齐的音频超参数（GE2E 预训练模型的输入规范）
MEL_WINDOW_LENGTH_MS = 25
MEL_WINDOW_STEP_MS = 10
MEL_N_CHANNELS = 40
SAMPLING_RATE = 16000
AUDIO_NORM_TARGET_DBFS = -30.0
MIN_EMBED_SECONDS = 1.0  # 短于此秒数的语音不足以产生可靠嵌入

_INT16_MAX = (2 ** 15) - 1


def _stub_webrtcvad() -> None:
    """注入桩模块，保证 resemblyzer 包的 import 链可用。

    Resemblyzer 0.1.4 安装用 --no-deps，其 audio.py 顶层会 import 三个缺失依赖：
    - webrtcvad（Windows 无预编译 wheel，需 MSVC 编译）
    - librosa（mel 计算已用 numpy/torchaudio 复刻，不再调用）
    - scipy.ndimage.morphology（binary_dilation，仅 trim_long_silences 用到）
    我们的运行路径完全不调用这些函数，桩只需让 import 成功。
    """
    import sys
    import types

    if "webrtcvad" not in sys.modules:
        try:
            import webrtcvad  # noqa: F401
        except Exception:
            stub = types.ModuleType("webrtcvad")
            stub.Vad = lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("webrtcvad stub: use energy VAD instead")
            )
            sys.modules["webrtcvad"] = stub

    if "librosa" not in sys.modules:
        try:
            import librosa  # noqa: F401
        except Exception:
            sys.modules["librosa"] = types.ModuleType("librosa")

    # scipy 可能部分可用：仅当 scipy.ndimage.morphology 缺失时补链
    # （scipy 1.15+ 已移除该命名空间，此时不能覆盖真实的 scipy.ndimage）
    need_scipy_stub = False
    try:
        import scipy.ndimage.morphology  # noqa: F401
    except Exception:
        need_scipy_stub = True
    if need_scipy_stub and "scipy.ndimage.morphology" not in sys.modules:
        morph_mod = types.ModuleType("scipy.ndimage.morphology")
        morph_mod.binary_dilation = lambda mask, structure=None: mask  # 占位，不会被调用
        if "scipy" in sys.modules and "scipy.ndimage" in sys.modules:
            # 真实 scipy 存在：把 morphology 子模块挂回真实 ndimage
            real_ndimage = sys.modules["scipy.ndimage"]
            real_ndimage.morphology = morph_mod
        else:
            scipy_mod = types.ModuleType("scipy")
            ndimage_mod = types.ModuleType("scipy.ndimage")
            ndimage_mod.morphology = morph_mod
            scipy_mod.ndimage = ndimage_mod
            sys.modules["scipy"] = scipy_mod
            sys.modules["scipy.ndimage"] = ndimage_mod
        sys.modules["scipy.ndimage.morphology"] = morph_mod


class VoiceprintEngine:
    """声纹嵌入引擎：PCM16 mono 16kHz → 256 维 L2 归一化 d-vector"""

    def __init__(self):
        self._encoder = None
        self._load_error: str | None = None
        self._mel_fbanks: np.ndarray | None = None

    # ---------- 状态 ----------

    @property
    def available(self) -> bool:
        """声纹引擎是否可用（torch + 预训练模型加载成功）"""
        return self._encoder is not None

    @property
    def load_error(self) -> str | None:
        """最后一次加载失败的错误描述（available=False 时非空）"""
        return self._load_error

    # ---------- 加载 ----------

    def load(self) -> bool:
        """懒加载声纹模型。失败不抛异常，仅置 available=False。"""
        if self._encoder is not None:
            return True
        if self._load_error is not None:
            return False
        try:
            import torch
            torch.set_num_threads(1)
            torch.set_grad_enabled(False)

            _stub_webrtcvad()
            from resemblyzer.voice_encoder import VoiceEncoder

            self._encoder = VoiceEncoder(device="cpu", verbose=False)
            self._mel_fbanks = self._build_mel_fbanks()
            logger.info("Voiceprint engine loaded (GE2E d-vector, cpu)")
            return True
        except Exception as e:
            self._load_error = str(e)
            logger.warning("Voiceprint engine unavailable: %s", e)
            return False

    # ---------- 安装自愈 ----------

    def install(self) -> tuple[bool, str]:
        """pip 补装 Resemblyzer 并重试加载（源码模式自愈；frozen 模式拒绝）。

        场景：torch 早已装好（Silero VAD 正常）的用户升级到带声纹功能的版本时，
        electron 的 installTorch 流程不会触发，Resemblyzer 无人安装——
        启动自检与设置页手动按钮都走这里。

        Returns:
            (成功与否, 说明信息)
        """
        import subprocess
        import sys

        if self._encoder is not None:
            return True, "already loaded"
        if getattr(sys, "frozen", False):
            return False, "frozen mode: voiceprint engine not bundled (by design)"
        if not self._torch_available():
            # torch 缺失时装了 resemblyzer 也没用（--no-deps 不会带上 torch）
            return False, "torch not installed; install PyTorch first (source mode required)"

        base_cmd = [sys.executable, "-m", "pip", "install", "--no-deps",
                    "--upgrade", "Resemblyzer==0.1.4"]
        try:
            proc = subprocess.run(base_cmd, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                # 系统 Python 无写权限时回退 --user
                proc = subprocess.run(base_cmd + ["--user"],
                                      capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()[-500:]
                logger.warning("Resemblyzer install failed: %s", detail)
                return False, detail
        except subprocess.TimeoutExpired:
            return False, "pip install timed out (5 minutes)"
        except Exception as e:
            return False, str(e)

        logger.info("Resemblyzer installed, reloading voiceprint engine")
        self._load_error = None  # 清除失败标记，允许重试加载
        if self.load():
            return True, "installed and loaded"
        return False, self._load_error or "installed but load failed"

    @staticmethod
    def _torch_available() -> bool:
        try:
            import torch  # noqa: F401
            return True
        except ImportError:
            return False



    # ---------- 嵌入 ----------

    def embed(self, pcm16_bytes: bytes) -> np.ndarray | None:
        """PCM16 mono 16kHz bytes → float32[256]（L2 归一化），失败返回 None"""
        if self._encoder is None and not self.load():
            return None

        wav = self._pcm16_to_float(pcm16_bytes)
        if wav is None or len(wav) < int(SAMPLING_RATE * MIN_EMBED_SECONDS):
            return None

        wav = self._trim_silence(wav)
        if len(wav) < int(SAMPLING_RATE * MIN_EMBED_SECONDS):
            return None

        wav = self._normalize_volume(wav)

        try:
            import torch
            return self._encode(torch, wav)
        except Exception as e:
            logger.warning("Voiceprint embed failed: %s", e)
            return None

    def embed_batch(self, windows: list[np.ndarray]) -> tuple[np.ndarray, list[int]] | None:
        """批量嵌入多个 float32 波形窗（会后重聚类专用，整批共享一次 mel 配置）。

        Returns:
            (嵌入矩阵 (n_valid, 256) L2 归一化, 有效窗索引列表)；
            引擎不可用或全部失败返回 None。无效窗（过短）被剔除，不在结果中。
        """
        if not windows:
            return None
        if self._encoder is None and not self.load():
            return None

        try:
            import torch
            # 每窗先做与 embed() 一致的预处理（裁剪+归一化）
            processed = []
            for wav in windows:
                wav = self._trim_silence(wav)
                if len(wav) < int(SAMPLING_RATE * MIN_EMBED_SECONDS):
                    processed.append(None)
                    continue
                processed.append(self._normalize_volume(wav))
            valid_idx = [i for i, w in enumerate(processed) if w is not None]
            if not valid_idx:
                return None

            all_mels, window_mel_slices = [], []
            for i in valid_idx:
                wav_slices, mel_slices = self._encoder.compute_partial_slices(
                    len(processed[i]), rate=1.3, min_coverage=0.75,
                )
                max_wave_length = wav_slices[-1].stop
                if max_wave_length >= len(processed[i]):
                    processed[i] = np.pad(
                        processed[i], (0, max_wave_length - len(processed[i])), "constant",
                    )
                mel = self._wav_to_mel(processed[i])
                all_mels.extend(mel[s] for s in mel_slices)
                window_mel_slices.append(len(mel_slices))

            # 分块前向（每块 128 个 partial，控制 CPU 内存）
            embeds = []
            for start in range(0, len(all_mels), 128):
                chunk = torch.from_numpy(np.stack(all_mels[start:start + 128]))
                with torch.no_grad():
                    partial = self._encoder(chunk).cpu().numpy()
                embeds.append(partial)
            partial_embeds = np.concatenate(embeds, axis=0)

            # 按 window 聚合 partial 均值并 L2 归一化
            result = np.zeros((len(valid_idx), partial_embeds.shape[1]), dtype=np.float32)
            offset = 0
            for row in range(len(valid_idx)):
                n = window_mel_slices[row]
                rows = partial_embeds[offset:offset + n]
                raw = rows.mean(axis=0)
                result[row] = raw / np.linalg.norm(raw, 2)
                offset += n
            return result, valid_idx
        except Exception as e:
            logger.warning("Voiceprint embed_batch failed: %s", e)
            return None

    def _encode(self, torch, wav: np.ndarray) -> np.ndarray:
        """复刻 VoiceEncoder.embed_utterance 主体（避开 resemblyzer.audio import）"""
        encoder = self._encoder
        # 与 embed_utterance 默认参数一致：1.6s partials，1.3/s 覆盖率
        wav_slices, mel_slices = encoder.compute_partial_slices(
            len(wav), rate=1.3, min_coverage=0.75,
        )
        max_wave_length = wav_slices[-1].stop
        if max_wave_length >= len(wav):
            wav = np.pad(wav, (0, max_wave_length - len(wav)), "constant")

        mel = self._wav_to_mel(wav)  # (n_frames, n_mels)
        mels = np.array([mel[s] for s in mel_slices])
        mels_t = torch.from_numpy(mels).to(encoder.device)
        # grad 模式是线程局部的（load() 中的全局关闭对 to_thread 工作线程无效），
        # 必须显式 no_grad 包裹，否则输出带 grad_fn 无法转 numpy
        with torch.no_grad():
            partial_embeds = encoder(mels_t).cpu().numpy()

        raw_embed = np.mean(partial_embeds, axis=0)
        return (raw_embed / np.linalg.norm(raw_embed, 2)).astype(np.float32)

    # ---------- 音频预处理（不依赖 librosa/scipy/webrtcvad） ----------

    def _pcm16_to_float(self, pcm16_bytes: bytes) -> np.ndarray | None:
        """PCM16 little-endian bytes → float32 [-1, 1]"""
        n = len(pcm16_bytes) // 2
        if n == 0:
            return None
        samples = struct.unpack(f"<{n}h", pcm16_bytes[: n * 2])
        return np.array(samples, dtype=np.float32) / _INT16_MAX

    @staticmethod
    def _trim_silence(wav: np.ndarray, sil_ms: int = 100, win_ms: int = 30) -> np.ndarray:
        """能量法裁剪首尾静音（替代 trim_long_silences；只裁两端，不裁中间停顿）"""
        win = int(SAMPLING_RATE * win_ms / 1000)
        if len(wav) <= win:
            return wav
        rms = np.sqrt(np.convolve(wav ** 2, np.ones(win) / win, mode="valid"))
        # 阈值取全段 RMS 峰值的 -40dB，且至少超过一个极小底噪
        thr = max(rms.max() * 10 ** (-40 / 20), 1e-4)
        voiced = np.nonzero(rms > thr)[0]
        if len(voiced) == 0:
            return wav
        pad = int(SAMPLING_RATE * sil_ms / 1000)
        start = max(0, voiced[0] - pad)
        end = min(len(rms), voiced[-1] + pad + win)
        return wav[start:end]

    @staticmethod
    def _normalize_volume(wav: np.ndarray) -> np.ndarray:
        """音量归一化到 -30dBFS（复刻 resemblyzer normalize_volume，increase_only）"""
        rms = np.sqrt(np.mean((wav * _INT16_MAX) ** 2))
        if rms == 0:
            return wav
        wave_dbfs = 20 * np.log10(rms / _INT16_MAX)
        dbfs_change = AUDIO_NORM_TARGET_DBFS - wave_dbfs
        if dbfs_change < 0:  # increase_only=True：音量高于目标时不衰减
            return wav
        return wav * (10 ** (dbfs_change / 20))

    def _build_mel_fbanks(self) -> np.ndarray:
        """slaney 规范 mel filterbank（librosa 默认参数），via torchaudio"""
        import torchaudio

        n_fft = int(SAMPLING_RATE * MEL_WINDOW_LENGTH_MS / 1000)
        fbanks = torchaudio.functional.melscale_fbanks(
            n_freqs=n_fft // 2 + 1,
            f_min=0.0,
            f_max=SAMPLING_RATE / 2,
            n_mels=MEL_N_CHANNELS,
            sample_rate=SAMPLING_RATE,
            norm="slaney",
            mel_scale="slaney",
        )
        return fbanks.numpy().astype(np.float32)

    def _wav_to_mel(self, wav: np.ndarray) -> np.ndarray:
        """功率 mel 谱（复刻 librosa.feature.melspectrogram 默认参数）→ (frames, n_mels)"""
        n_fft = int(SAMPLING_RATE * MEL_WINDOW_LENGTH_MS / 1000)   # 400
        hop = int(SAMPLING_RATE * MEL_WINDOW_STEP_MS / 1000)       # 160

        # librosa center=True：两侧 pad n_fft//2
        padded = np.pad(wav, (n_fft // 2, n_fft // 2), mode="constant")
        frames = np.lib.stride_tricks.sliding_window_view(padded, n_fft)[::hop]
        # 周期 hann 窗（等价 scipy.signal.get_window('hann', n_fft, fftbins=True)）
        window = np.hanning(n_fft + 1)[:-1].astype(np.float32)
        power_spec = np.abs(np.fft.rfft(frames * window, n=n_fft)) ** 2

        mel = power_spec @ self._mel_fbanks
        return mel.astype(np.float32)  # (n_frames, n_mels)
