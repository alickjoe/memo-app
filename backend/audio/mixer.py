"""
音频混音器 - 系统音频和麦克风的混合处理
"""
import numpy as np


class AudioMixer:
    """双通道音频混音器"""

    def __init__(self, system_volume: float = 0.6, mic_volume: float = 0.4):
        self.system_volume = max(0.0, min(1.0, system_volume))
        self.mic_volume = max(0.0, min(1.0, mic_volume))

    def set_system_volume(self, volume: float):
        """设置系统音频音量 (0.0 - 1.0)"""
        self.system_volume = max(0.0, min(1.0, volume))

    def set_mic_volume(self, volume: float):
        """设置麦克风音量 (0.0 - 1.0)"""
        self.mic_volume = max(0.0, min(1.0, volume))

    def mix(self, system_audio: np.ndarray, mic_audio: np.ndarray) -> np.ndarray:
        """混合两路音频"""
        # 确保长度一致
        min_len = min(len(system_audio), len(mic_audio))
        system_audio = system_audio[:min_len]
        mic_audio = mic_audio[:min_len]

        # 加权混合
        mixed = system_audio * self.system_volume + mic_audio * self.mic_volume

        # 限幅，防止削波
        mixed = np.clip(mixed, -1.0, 1.0)

        return mixed.astype(np.float32)
