"""
共享测试辅助函数（非 fixture，可被测试文件直接导入）
"""
import struct
import math


def make_audio_bytes(duration_sec: float, with_signal: bool = True) -> bytes:
    """生成模拟 PCM 音频数据 (16kHz, 16-bit, mono)

    Args:
        duration_sec: 时长（秒）
        with_signal: True 生成有信号(正弦波), False 生成静音
    """
    sample_rate = 16000
    num_samples = int(sample_rate * duration_sec)
    data = bytearray()
    for i in range(num_samples):
        if with_signal:
            sample = int(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / sample_rate))
        else:
            sample = 0
        data.extend(struct.pack('<h', max(-32768, min(32767, sample))))
    return bytes(data)


def make_empty_chunk(duration_sec: float = 0.1) -> bytes:
    """生成静音 chunk"""
    return make_audio_bytes(duration_sec, with_signal=False)
