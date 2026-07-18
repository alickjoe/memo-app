"""
语音活动检测 (Voice Activity Detection)
使用 Silero VAD 模型过滤静音段，节省 API 费用
"""
import logging

logger = logging.getLogger("memo.vad")


class VoiceActivityDetector:
    """基于 Silero VAD 的语音活动检测器"""

    def __init__(self, threshold: float = 0.5, min_speech_duration: float = 0.3):
        self.threshold = threshold
        self.min_speech_duration = min_speech_duration
        self._model = None
        self._sample_rate = 16000
        self._speech_buffer = bytearray()
        self._speech_frames = 0
        self._min_speech_frames = int(min_speech_duration * self._sample_rate / 512)

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
                )
                self._model = model
                self._get_speech_timestamps = utils[0]
                logger.info("Silero VAD model loaded")
            except Exception as e:
                logger.warning(f"Failed to load Silero VAD: {e}, falling back to energy-based VAD")

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
        return rms > 300  # 能量阈值（int16 RMS）

    async def detect(self, audio_bytes: bytes) -> bool:
        """检测音频块是否包含语音"""
        if len(audio_bytes) < 320:  # 小于 10ms (16kHz * 2 bytes * 0.01)
            return False

        if self._model is not None:
            try:
                import torch
                import numpy as np

                # 转换为 float32 tensor
                audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                audio_tensor = torch.from_numpy(audio_np)

                # VAD 检测
                speech_prob = self._model(audio_tensor, self._sample_rate).item()
                return speech_prob > self.threshold
            except Exception as e:
                logger.warning(f"Silero VAD failed, falling back to energy-based: {e}")

        return self._energy_based_vad(audio_bytes)

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
