"""
说话人分离模块 - 基于能量和时段的简单聚类方案
"""
import logging
import numpy as np

logger = logging.getLogger("memo.diarization")


class SpeakerDiarizer:
    """简化的说话人分离器"""

    MAX_SPEAKERS = 4   # 最大说话人数上限
    MATCH_THRESHOLD = 2.0  # 匹配阈值，越大越宽松

    def __init__(self):
        self._speaker_embeddings: dict[str, np.ndarray] = {}
        self._next_speaker_id = 0

    async def identify(self, audio_bytes: bytes) -> str | None:
        """识别说话人"""
        try:
            # 提取简单的音频特征（能量 + 过零率）
            features = self._extract_features(audio_bytes)

            # 查找最相似的已有说话人
            best_match = None
            best_score = float('inf')

            for speaker_id, embedding in self._speaker_embeddings.items():
                score = np.linalg.norm(features - embedding)
                if score < best_score and score < self.MATCH_THRESHOLD:
                    best_score = score
                    best_match = speaker_id

            if best_match:
                # 更新已有说话人的特征（移动平均）
                self._speaker_embeddings[best_match] = (
                    self._speaker_embeddings[best_match] * 0.7 + features * 0.3
                )
                return best_match
            else:
                # 达到上限后不再创建新说话人，强制归入最相似的
                if len(self._speaker_embeddings) >= self.MAX_SPEAKERS:
                    best_any = min(
                        self._speaker_embeddings.keys(),
                        key=lambda sid: np.linalg.norm(self._speaker_embeddings[sid] - features),
                    )
                    self._speaker_embeddings[best_any] = (
                        self._speaker_embeddings[best_any] * 0.7 + features * 0.3
                    )
                    return best_any

                # 新说话人
                self._next_speaker_id += 1
                speaker_label = f"Speaker {chr(65 + (self._next_speaker_id - 1) % 26)}"
                self._speaker_embeddings[speaker_label] = features
                return None  # 返回 None 由调用方分配标签

        except Exception as e:
            logger.warning(f"Diarization failed: {e}")
            return None

    def _extract_features(self, audio_bytes: bytes) -> np.ndarray:
        """提取音频特征向量"""
        import struct

        if len(audio_bytes) < 2:
            return np.zeros(4)

        samples = struct.unpack(f'{len(audio_bytes) // 2}h', audio_bytes)
        if len(samples) == 0:
            return np.zeros(4)

        samples_np = np.array(samples, dtype=np.float32) / 32768.0

        # 特征: [RMS能量, 过零率, 频谱质心近似, 峰度]
        rms = np.sqrt(np.mean(samples_np ** 2))
        zcr = np.sum(np.abs(np.diff(np.sign(samples_np)))) / (2 * len(samples_np))
        spectral_centroid = np.sum(np.abs(np.diff(samples_np))) / len(samples_np)
        kurtosis = np.mean((samples_np - np.mean(samples_np)) ** 4) / (np.std(samples_np) ** 4 + 1e-10)

        return np.array([rms, zcr, spectral_centroid, kurtosis])

    def reset(self):
        """重置说话人记录"""
        self._speaker_embeddings.clear()
        self._next_speaker_id = 0
