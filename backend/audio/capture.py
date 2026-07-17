"""
WASAPI loopback 音频捕获模块
支持系统音频（loopback）和麦克风输入双通道捕获
"""
import asyncio
import logging
import threading
import wave
import os
from typing import Optional
from dataclasses import dataclass

import soundcard as sc
import numpy as np

logger = logging.getLogger("memo.audio")


@dataclass
class AudioDevice:
    id: str
    name: str
    is_loopback: bool
    channels: int
    sample_rate: int


class AudioCapture:
    """Windows 音频捕获器"""

    def __init__(self, sample_rate: int = 16000, chunk_duration: float = 0.1):
        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * chunk_duration)
        self._is_active = False
        self._is_paused = False
        self._meeting_id: Optional[str] = None

        self._loopback_mic: Optional[sc.Microphone] = None
        self._input_mic: Optional[sc.Microphone] = None
        self._thread: Optional[threading.Thread] = None

        # 音频缓冲区
        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._buffer_event = asyncio.Event()

        # 输出文件
        self._wave_file: Optional[wave.Wave_write] = None

    def list_devices(self) -> list[dict]:
        """列出所有音频设备"""
        devices = []
        try:
            mics = sc.all_microphones(include_loopback=True)
            for mic in mics:
                devices.append({
                    "id": mic.id,
                    "name": mic.name,
                    "channels": mic.channels,
                    "is_loopback": "loopback" in mic.name.lower(),
                })
        except Exception as e:
            logger.warning(f"Failed to list devices: {e}")
        return devices

    async def start(self, meeting_id: str):
        """开始录制"""
        self._meeting_id = meeting_id
        self._is_active = True
        self._is_paused = False
        self._buffer = bytearray()

        # 查找 loopback 设备（系统音频）
        try:
            mics = sc.all_microphones(include_loopback=True)
            loopback_mics = [m for m in mics if "loopback" in m.name.lower()]

            if loopback_mics:
                self._loopback_mic = loopback_mics[0]
                logger.info(f"Using loopback device: {self._loopback_mic.name}")
            else:
                # 回退方案：使用默认扬声器
                speakers = sc.all_speakers()
                if speakers:
                    self._loopback_mic = sc.get_microphone(speakers[0].id, include_loopback=True)
                    logger.info(f"Using speaker loopback: {speakers[0].name}")
        except Exception as e:
            logger.warning(f"Loopback device not available: {e}")

        # 获取默认麦克风
        try:
            default_mic = sc.default_microphone()
            self._input_mic = default_mic
            logger.info(f"Using microphone: {default_mic.name}")
        except Exception as e:
            logger.warning(f"Microphone not available: {e}")

        # 创建输出文件
        data_dir = os.path.join(os.environ.get("DATA_DIR", os.path.expanduser("~/.memo")), "recordings")
        os.makedirs(data_dir, exist_ok=True)
        audio_path = os.path.join(data_dir, f"{meeting_id}.wav")
        self._wave_file = wave.open(audio_path, 'wb')
        self._wave_file.setnchannels(1)
        self._wave_file.setsampwidth(2)  # 16-bit
        self._wave_file.setframerate(self.sample_rate)

        # 启动录制线程
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        logger.info(f"Recording started for meeting {meeting_id}")

    def stop(self):
        """停止录制"""
        self._is_active = False
        if self._wave_file:
            self._wave_file.close()
            self._wave_file = None
        logger.info("Recording stopped")

    def pause(self):
        """暂停录制"""
        self._is_paused = True

    def resume(self):
        """恢复录制"""
        self._is_paused = False

    def is_active(self) -> bool:
        return self._is_active

    async def read_chunk(self, timeout: float = 1.0) -> Optional[bytes]:
        """读取一个音频块"""
        try:
            await asyncio.wait_for(self._wait_for_data(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

        with self._buffer_lock:
            if len(self._buffer) >= self.chunk_size * 2:
                chunk = bytes(self._buffer[:self.chunk_size * 2])
                del self._buffer[:self.chunk_size * 2]
                self._buffer_event.clear()
                return chunk
            elif len(self._buffer) > 0:
                chunk = bytes(self._buffer)
                self._buffer.clear()
                self._buffer_event.clear()
                return chunk

        return None

    async def _wait_for_data(self):
        """等待音频数据"""
        while len(self._buffer) < self.chunk_size * 2 and self._is_active:
            await asyncio.sleep(0.05)

    def _capture_loop(self):
        """录制线程主循环"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            with self._loopback_mic.recorder(samplerate=self.sample_rate) as loopback_rec, \
                 self._input_mic.recorder(samplerate=self.sample_rate) as input_rec:

                while self._is_active:
                    if self._is_paused:
                        loopback_rec.flush()
                        input_rec.flush()
                        continue

                    try:
                        # 读取系统音频
                        loopback_data = loopback_rec.record(numframes=self.chunk_size)
                        loopback_data = loopback_data.mean(axis=1) if loopback_data.ndim > 1 else loopback_data

                        # 读取麦克风
                        mic_data = input_rec.record(numframes=self.chunk_size)
                        mic_data = mic_data.mean(axis=1) if mic_data.ndim > 1 else mic_data

                        # 混合两个通道（简单相加）
                        mixed = (loopback_data * 0.6 + mic_data * 0.4).astype(np.float32)

                        # 限幅
                        mixed = np.clip(mixed, -1.0, 1.0)

                        # 转换为 16-bit PCM
                        pcm_data = (mixed * 32767).astype(np.int16).tobytes()

                        # 写入缓冲区和文件
                        with self._buffer_lock:
                            self._buffer.extend(pcm_data)

                        if self._wave_file:
                            self._wave_file.writeframes(pcm_data)

                    except Exception as e:
                        logger.error(f"Capture error: {e}")
                        continue

        except Exception as e:
            logger.error(f"Capture loop error: {e}")
        finally:
            loop.close()
