"""
语音活动检测 (Voice Activity Detection)
使用 Silero VAD 模型过滤静音段，节省 API 费用

改进版：
- 连续语音确认帧：需连续多帧检测到语音才确认 speech start，防瞬时噪音
- 拖尾帧 (hangover)：语音结束后延续几帧才确认结束，防切句尾
- 降级标记：Silero VAD 加载失败时显式标记，上游可据此调整策略
"""
import logging

logger = logging.getLogger("memo.vad")


class VoiceActivityDetector:
    """基于 Silero VAD 的语音活动检测器"""

    def __init__(
        self,
        threshold: float = 0.6,
        min_speech_duration: float = 0.3,
        min_consecutive_speech: int = 3,
        hangover_frames: int = 3,
    ):
        self.threshold = threshold
        self.min_speech_duration = min_speech_duration
        self.min_consecutive_speech = min_consecutive_speech  # 连续语音确认帧数
        self.hangover_frames = hangover_frames  # 语音结束后拖尾帧数
        self._model = None
        self._sample_rate = 16000
        self._speech_buffer = bytearray()
        self._speech_frames = 0
        self._min_speech_frames = int(min_speech_duration * self._sample_rate / 512)

        # 状态追踪
        self._consecutive_speech_count = 0
        self._consecutive_silence_count = 0
        self._is_in_speech = False
        self._model_degraded = False  # 降级标记：True 表示 Silero 加载失败，使用能量回退

    @property
    def is_degraded(self) -> bool:
        """是否处于降级模式（使用能量 VAD 而非 Silero）"""
        return self._model_degraded

    async def _load_model(self):
        """延迟加载 VAD 模型"""
        if self._model is None:
            try:
                import torch
                torch.set_num_threads(1)

                # 尝试使用 silero-vad
                model, utils = torch.hub.load(
                    repo_or_dir='snakers4/silero-vad',
                    model='silero_vad',
                    force_reload=False,
                    trust_repo=True,
                )
                self._model = model
                self._get_speech_timestamps = utils[0]
                self._model_degraded = False
                logger.info("Silero VAD model loaded")
            except Exception as e:
                self._model_degraded = True
                logger.warning(
                    "Failed to load Silero VAD: %s, falling back to energy-based VAD. "
                    "Energy-based VAD is less accurate and may produce false positives. "
                    "Consider installing PyTorch and silero-vad for better accuracy.",
                    e,
                )

    def _energy_based_vad(self, audio_bytes: bytes) -> bool:
        """基于能量的简单 VAD 回退方案"""
        import struct
        import math

        if len(audio_bytes) < 2:
            return False

        # 计算 RMS 能量
        samples = struct.unpack(f'{len(audio_bytes) // 2}h', audio_bytes)
        if len(samples) == 0:
            return False

        rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        return rms > 500  # 能量阈值（int16 RMS），提高以减少噪音误判

    async def detect(self, audio_bytes: bytes) -> bool:
        """检测音频块是否包含语音（原始逐帧判断）
        
        Silero VAD 要求输入恰好 512 样本 (32ms @ 16kHz) 或 256 样本 (32ms @ 8kHz)。
        调用方可能传入不同长度的 chunk，此处自动切分为 512-sample 窗口分批检测。
        """
        if len(audio_bytes) < 320:  # 小于 10ms (16kHz * 2 bytes * 0.01)
            return False

        if self._model is not None:
            try:
                import torch
                import numpy as np

                # 转换为 float32 array
                audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                total_samples = len(audio_np)

                # Silero VAD at 16kHz expects exactly 512 samples per inference
                vad_window = 512
                max_prob = 0.0

                for start in range(0, total_samples, vad_window):
                    chunk = audio_np[start:start + vad_window]
                    if len(chunk) < vad_window:
                        # 最后一帧不足 512 样本，补零
                        chunk = np.pad(chunk, (0, vad_window - len(chunk)), mode='constant')
                    audio_tensor = torch.from_numpy(chunk)
                    speech_prob = self._model(audio_tensor, self._sample_rate).item()
                    max_prob = max(max_prob, speech_prob)

                return max_prob > self.threshold
            except Exception as e:
                logger.warning(f"Silero VAD failed, falling back to energy-based: {e}")

        return self._energy_based_vad(audio_bytes)

    async def detect_with_hysteresis(self, audio_bytes: bytes) -> str:
        """带滞回的状态式语音检测
        
        返回值：
        - 'speech': 确认为语音帧
        - 'silence': 确认为静音帧
        - 'hangover': 拖尾帧（刚结束语音，仍在延续中）
        - 'pending': 尚未确认（语音开始前的候选帧）
        """
        raw_is_speech = await self.detect(audio_bytes)

        if raw_is_speech:
            self._consecutive_silence_count = 0
            self._consecutive_speech_count += 1

            if self._is_in_speech:
                return 'speech'
            elif self._consecutive_speech_count >= self.min_consecutive_speech:
                self._is_in_speech = True
                return 'speech'
            else:
                return 'pending'
        else:
            self._consecutive_speech_count = 0

            if not self._is_in_speech:
                return 'silence'

            self._consecutive_silence_count += 1
            if self._consecutive_silence_count <= self.hangover_frames:
                return 'hangover'
            else:
                self._is_in_speech = False
                self._consecutive_silence_count = 0
                return 'silence'

    def reset_state(self):
        """重置滞回状态（每次语音段结束后调用）"""
        self._consecutive_speech_count = 0
        self._consecutive_silence_count = 0
        self._is_in_speech = False

    def append_buffer(self, existing_buffer: bytearray, new_data: bytes) -> bytearray:
        """添加到语音缓冲区"""
        existing_buffer.extend(new_data)
        self._speech_frames += 1
        return existing_buffer

    def is_speech_complete(self) -> bool:
        """判断当前语音段是否足够长"""
        return self._speech_frames >= self._min_speech_frames

    def reset_buffer(self):
        """重置缓冲区"""
        self._speech_buffer = bytearray()
        self._speech_frames = 0
