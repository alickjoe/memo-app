"""
WASAPI loopback 音频捕获模块
支持系统音频（loopback）和麦克风输入双通道捕获
支持录制中手动切换设备
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import queue
import threading
import time
import warnings
import wave
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import soundcard as sc

from audio.sc_pcm_patch import apply as _apply_sc_pcm_patch

# soundcard 在 loopback 录制时偶发内部缓冲区溢出，属于已知问题，不影响音频质量
warnings.filterwarnings("ignore", message="data discontinuity in recording")

logger = logging.getLogger("memo.audio")

# Windows: 修复蓝牙 HFP / 部分 OEM 驱动端点（非 float 混音格式）无法打开的问题
# 非 Windows 平台为安全 no-op
_apply_sc_pcm_patch()

# 信号检测阈值（用于 _test_device_signal 判断设备是否有信号）
SIGNAL_THRESHOLD_RMS = 0.001


@dataclass
class AudioDevice:
    id: str
    name: str
    is_loopback: bool
    channels: int
    sample_rate: int


@dataclass
class DeviceSignalInfo:
    """设备信号检测结果"""
    device_id: str
    device_name: str
    has_signal: bool
    rms_level: float
    is_loopback: bool


class AudioCapture:
    """Windows 音频捕获器，支持录制中手动切换设备"""

    def __init__(self, sample_rate: int = 16000, chunk_duration: float = 0.1):
        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * chunk_duration)
        self.chunk_duration = chunk_duration
        self._is_active = False
        self._is_paused = False
        self._meeting_id: str | None = None

        self._loopback_mic: sc.Microphone | None = None
        self._input_mic: sc.Microphone | None = None
        self._thread: threading.Thread | None = None

        # 设备信息缓存
        self._loopback_device_id: str | None = None
        self._input_device_id: str | None = None

        # 音频缓冲区
        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._buffer_event = asyncio.Event()

        # 输出文件
        self._wave_file: wave.Wave_write | None = None

        # loopback 专用读取线程 + 有界队列（替代逐 chunk 线程+超时+锁方案）
        self._loopback_queue: queue.Queue = queue.Queue(maxsize=10)
        self._loopback_reader_thread: threading.Thread | None = None
        self._loopback_reader_active = False

        # 信号监控（仅显示用，不做自动切换）
        self._current_loopback_rms = 0.0
        self._current_mic_rms = 0.0

        # 手动设备切换标志：capture loop 检测到后重建 recorder
        self._pending_recorder_restart = False

        # 设备切换回调: async callable(device_type: str, old_name: str, new_name: str)
        self._on_device_switched: Callable | None = None
        self._callback_loop: asyncio.AbstractEventLoop | None = None
        # 去重用：device_type -> 最近一次通知前端的目标设备名
        self._last_notified: dict[str, str] = {}

    def set_device_switch_callback(self, callback: Callable | None):
        """设置设备切换回调（异步函数），用于通知前端

        需在事件循环线程内调用（如 FastAPI 端点中），以便后续从采集线程
        线程安全地调度回调。
        """
        self._on_device_switched = callback
        try:
            self._callback_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._callback_loop = None
        self._last_notified = {}

    def _fire_device_switched(self, device_type: str, old_name: str, new_name: str,
                              only_on_change: bool = True):
        """线程安全地触发设备切换回调（仅用于前端展示通知）"""
        if only_on_change and self._last_notified.get(device_type) == new_name:
            return
        self._last_notified[device_type] = new_name

        callback = self._on_device_switched
        loop = self._callback_loop
        if callback is None or loop is None or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                callback(device_type, old_name, new_name), loop)
        except Exception:
            pass

    def _device_candidates(self, device_type: str) -> list:
        """按优先级返回可尝试的候选设备：当前选择优先，然后是其余可用设备"""
        candidates: list = []
        try:
            current = self._loopback_mic if device_type == "loopback" else self._input_mic
            if current is not None:
                candidates.append(current)

            if device_type == "loopback":
                speaker_ids = {s.id for s in sc.all_speakers()}
                devices = [m for m in sc.all_microphones(include_loopback=True)
                           if m.id in speaker_ids]
            else:
                devices = sc.all_microphones(include_loopback=False)

            for device in devices:
                if all(device.id != c.id for c in candidates):
                    candidates.append(device)
        except Exception as e:
            logger.warning("List %s device candidates failed: %s", device_type, e)
        return candidates

    def _open_channel(self, device_type: str) -> tuple:
        """依次尝试打开候选设备（loopback 或 input）

        单个设备打不开（如蓝牙 HFP 端点非 float 混音格式）不拖垮另一路，
        自动回退到下一个可用设备。

        Returns:
            (recorder, mic)：打开成功；recorder 尚未进入上下文，由调用方管理
            (None, None)：所有候选设备均无法打开
        """
        for mic in self._device_candidates(device_type):
            try:
                recorder = mic.recorder(samplerate=self.sample_rate)
                return recorder, mic
            except Exception as e:
                logger.error(
                    "Open %s device '%s' failed: %s: %s",
                    device_type, getattr(mic, "name", "?"), type(e).__name__, e,
                )
        return None, None

    @property
    def loopback_device_name(self) -> str:
        return self._loopback_mic.name if self._loopback_mic else "none"

    @property
    def input_device_name(self) -> str:
        return self._input_mic.name if self._input_mic else "none"

    @property
    def signal_stats(self) -> dict:
        """当前信号统计（仅显示用）"""
        return {
            "loopback_rms": round(self._current_loopback_rms, 6),
            "mic_rms": round(self._current_mic_rms, 6),
        }

    def scan_devices_for_signal(self, device_type: str = "loopback", duration: float = 2.0) -> list[DeviceSignalInfo]:
        """扫描所有设备，检测哪些有信号
        
        Args:
            device_type: "loopback" | "input" | "all"
            duration: 每个设备检测时长（秒）
        
        Returns:
            按 RMS 降序排列的设备信号列表
        """
        results = []
        try:
            if device_type in ("loopback", "all"):
                speaker_ids = {s.id for s in sc.all_speakers()}
                loopback_mics = [m for m in sc.all_microphones(include_loopback=True)
                                if m.id in speaker_ids]
                for mic in loopback_mics:
                    info = self._test_device_signal(mic, duration)
                    results.append(info)

            if device_type in ("input", "all"):
                input_mics = sc.all_microphones(include_loopback=False)
                for mic in input_mics:
                    info = self._test_device_signal(mic, duration)
                    results.append(info)
        except Exception as e:
            logger.warning(f"Device scan failed: {e}")

        # 按 RMS 降序
        results.sort(key=lambda x: x.rms_level, reverse=True)
        return results

    def _test_device_signal(self, mic: sc.Microphone, duration: float) -> DeviceSignalInfo:
        """测试单个设备是否有信号"""
        try:
            num_frames = int(self.sample_rate * duration)
            with mic.recorder(samplerate=self.sample_rate) as rec:
                data = rec.record(numframes=num_frames)
                if data.ndim > 1:
                    data = data.mean(axis=1)
                rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
                has_signal = rms > SIGNAL_THRESHOLD_RMS
                speaker_ids = {s.id for s in sc.all_speakers()}
                return DeviceSignalInfo(
                    device_id=mic.id,
                    device_name=mic.name,
                    has_signal=has_signal,
                    rms_level=round(rms, 6),
                    is_loopback=mic.id in speaker_ids,
                )
        except Exception as e:
            logger.debug(f"Test device {mic.name} failed: {e}")
            speaker_ids = {s.id for s in sc.all_speakers()}
            return DeviceSignalInfo(
                device_id=mic.id,
                device_name=mic.name,
                has_signal=False,
                rms_level=0.0,
                is_loopback=mic.id in speaker_ids,
            )

    def list_devices(self) -> list[dict]:
        """列出所有音频设备"""
        devices = []
        try:
            speaker_ids = {s.id for s in sc.all_speakers()}
            mics = sc.all_microphones(include_loopback=True)
            for mic in mics:
                devices.append({
                    "id": mic.id,
                    "name": mic.name,
                    "channels": mic.channels,
                    "is_loopback": mic.id in speaker_ids,
                })
        except Exception as e:
            logger.warning(f"Failed to list devices: {e}")
        return devices

    async def start(
        self,
        meeting_id: str,
        loopback_device_id: str | None = None,
        input_device_id: str | None = None,
    ):
        """开始录制
        
        Args:
            meeting_id: 会议ID
            loopback_device_id: 指定系统音频捕获设备ID，None 则使用系统默认
            input_device_id: 指定麦克风设备ID，None 则使用系统默认
        """
        self._meeting_id = meeting_id
        self._is_active = True
        self._is_paused = False
        self._buffer = bytearray()
        self._current_loopback_rms = 0.0
        self._current_mic_rms = 0.0
        self._pending_recorder_restart = False

        # 保存请求的设备 ID
        self._loopback_device_id = loopback_device_id
        self._input_device_id = input_device_id

        # 查找 loopback 设备（系统音频）
        try:
            mics = sc.all_microphones(include_loopback=True)

            if loopback_device_id:
                # 按指定ID查找
                for mic in mics:
                    if mic.id == loopback_device_id:
                        self._loopback_mic = mic
                        logger.info(f"Using specified loopback device: {mic.name}")
                        break
                if not self._loopback_mic:
                    logger.warning(f"Specified loopback device {loopback_device_id} not found, falling back to default")

            if not self._loopback_mic:
                # 使用默认 loopback 设备
                speaker_ids = {s.id for s in sc.all_speakers()}
                loopback_mics = [m for m in mics if m.id in speaker_ids]
                if loopback_mics:
                    self._loopback_mic = loopback_mics[0]
                    logger.info(f"Using default loopback device: {self._loopback_mic.name}")
                else:
                    # 回退方案：使用默认扬声器
                    speakers = sc.all_speakers()
                    if speakers:
                        self._loopback_mic = sc.get_microphone(speakers[0].id, include_loopback=True)
                        logger.info(f"Using speaker loopback: {speakers[0].name}")
        except Exception as e:
            logger.warning(f"Loopback device not available: {e}")

        # 获取麦克风
        try:
            if input_device_id:
                # 按指定ID查找
                input_mics = sc.all_microphones(include_loopback=False)
                for mic in input_mics:
                    if mic.id == input_device_id:
                        self._input_mic = mic
                        logger.info(f"Using specified microphone: {mic.name}")
                        break
                if not self._input_mic:
                    logger.warning(f"Specified microphone {input_device_id} not found, falling back to default")

            if not self._input_mic:
                # 使用系统默认麦克风
                default_mic = sc.default_microphone()
                self._input_mic = default_mic
                logger.info(f"Using default microphone: {default_mic.name}")
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
        self._loopback_reader_active = False
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

    async def read_chunk(self, timeout: float = 1.0) -> bytes | None:
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
        """录制线程主循环
        
        外层 while 支持手动切换设备后重新打开 recorder，转写不中断。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        chunk_count = 0
        loopback_timeouts = 0

        try:
            restart_errors = 0  # 连续重启失败计数
            while self._is_active:
                try:
                    # 两路设备独立打开：单路失败自动回退候选设备，不拖垮另一路
                    loopback_rec, loopback_mic = self._open_channel("loopback")
                    input_rec, input_mic = self._open_channel("input")

                    if loopback_rec is None and input_rec is None:
                        raise RuntimeError(
                            "no audio device could be opened (loopback & input both failed)"
                        )

                    # 设备与初始选择不一致（自动回退）时通知前端
                    old_loopback = self._loopback_mic.name if self._loopback_mic else "none"
                    old_input = self._input_mic.name if self._input_mic else "none"
                    if loopback_mic is not None:
                        if self._loopback_mic is None or loopback_mic.id != self._loopback_mic.id:
                            logger.info("Loopback device auto-fallback: %s -> %s",
                                        old_loopback, loopback_mic.name)
                            self._fire_device_switched("loopback", old_loopback, loopback_mic.name)
                        self._loopback_mic = loopback_mic
                    else:
                        logger.warning("Capture degraded: no loopback device available, "
                                       "capturing microphone only")
                        self._fire_device_switched("loopback", old_loopback, "none")
                    if input_mic is not None:
                        if self._input_mic is None or input_mic.id != self._input_mic.id:
                            logger.info("Input device auto-fallback: %s -> %s",
                                        old_input, input_mic.name)
                            self._fire_device_switched("input", old_input, input_mic.name)
                        self._input_mic = input_mic
                    else:
                        logger.warning("Capture degraded: no input device available, "
                                       "capturing loopback only")
                        self._fire_device_switched("input", old_input, "none")

                    restart_errors = 0  # 至少一路设备成功打开，重置失败计数

                    with contextlib.ExitStack() as stack:
                        if loopback_rec is not None:
                            loopback_rec = stack.enter_context(loopback_rec)
                        if input_rec is not None:
                            input_rec = stack.enter_context(input_rec)

                        logger.info(
                            "Capture loop started, loopback=%s, input=%s",
                            loopback_mic.name if loopback_mic else "none",
                            input_mic.name if input_mic else "none",
                        )

                        # 启动专用 loopback 读取线程
                        self._loopback_reader_active = loopback_rec is not None
                        self._loopback_queue = queue.Queue(maxsize=10)
                        if loopback_rec is not None:
                            self._loopback_reader_thread = threading.Thread(
                                target=self._loopback_reader_loop,
                                args=(loopback_rec,),
                                daemon=True,
                            )
                            self._loopback_reader_thread.start()

                        while self._is_active:
                            if self._is_paused:
                                if loopback_rec is not None:
                                    loopback_rec.flush()
                                if input_rec is not None:
                                    input_rec.flush()
                                time.sleep(0.1)
                                continue

                            try:
                                # 先读取麦克风（不会阻塞）
                                if input_rec is not None:
                                    mic_data = input_rec.record(numframes=self.chunk_size)
                                    mic_data = mic_data.mean(axis=1) if mic_data.ndim > 1 else mic_data
                                else:
                                    mic_data = np.zeros(self.chunk_size, dtype=np.float32)

                                # 读取系统音频（非阻塞，从专用读取线程的队列获取）
                                try:
                                    loopback_data = self._loopback_queue.get_nowait()
                                except queue.Empty:
                                    loopback_data = np.zeros(self.chunk_size, dtype=np.float32)
                                loopback_has_data = bool(np.any(loopback_data))
                                if not loopback_has_data:
                                    loopback_timeouts += 1
                                loopback_data = loopback_data.mean(axis=1) if loopback_data.ndim > 1 else loopback_data

                                # 混合两个通道：两路都有时混合，仅一路时全量使用该路
                                if loopback_has_data and input_rec is not None:
                                    mixed = (loopback_data * 0.6 + mic_data * 0.4).astype(np.float32)
                                elif loopback_has_data:
                                    mixed = loopback_data.astype(np.float32)
                                else:
                                    mixed = mic_data.astype(np.float32)

                                # 每 100 块报告一次信号强度
                                if chunk_count % 100 == 0:
                                    mic_rms = float(np.sqrt(np.mean(mic_data.astype(np.float32) ** 2)))
                                    mixed_rms = float(np.sqrt(np.mean(mixed ** 2)))
                                    logger.info(
                                        "Signal level: mic_rms=%.4f mixed_rms=%.4f, loopback=%s",
                                        mic_rms, mixed_rms, "yes" if loopback_has_data else "timeout",
                                    )

                                # 更新 RMS 信号（仅显示用，不做自动切换）
                                self._current_mic_rms = float(np.sqrt(np.mean(mic_data.astype(np.float32) ** 2)))
                                if loopback_has_data:
                                    self._current_loopback_rms = float(np.sqrt(np.mean(loopback_data.astype(np.float32) ** 2)))
                                else:
                                    self._current_loopback_rms = 0.0

                                # 手动切换设备检测：API 调用 switch_device 后设置此标志
                                if self._pending_recorder_restart:
                                    self._pending_recorder_restart = False
                                    logger.info("Recorder restart triggered by manual device switch")
                                    break  # 跳出内层循环，外层 while 会重新打开 recorder

                                # 限幅
                                mixed = np.clip(mixed, -1.0, 1.0)

                                # 转换为 16-bit PCM
                                pcm_data = (mixed * 32767).astype(np.int16).tobytes()

                                # 写入缓冲区和文件
                                with self._buffer_lock:
                                    self._buffer.extend(pcm_data)

                                if self._wave_file:
                                    self._wave_file.writeframes(pcm_data)

                                chunk_count += 1
                                if chunk_count % 50 == 1:
                                    logger.info(
                                        "Capture chunk #%d, buffer_size=%d bytes",
                                        chunk_count, len(self._buffer),
                                    )

                            except Exception as e:
                                logger.error(f"Capture error: {e}")
                                time.sleep(0.05)
                                continue

                        # 停止 loopback 读取线程（在 recorder 关闭前）
                        self._loopback_reader_active = False
                        if self._loopback_reader_thread:
                            self._loopback_reader_thread.join(timeout=2.0)
                            self._loopback_reader_thread = None
                        while not self._loopback_queue.empty():
                            try:
                                self._loopback_queue.get_nowait()
                            except queue.Empty:
                                break

                except Exception as e:
                    restart_errors += 1
                    logger.error(f"Capture loop restart error (#{restart_errors}): {type(e).__name__}: {e}")

                    # 连续失败超过 3 次，加大间隔避免日志刷屏
                    # （蓝牙 HFP 端点在通话建立后才会出现，保留重试可自动恢复）
                    wait = 0.5 if restart_errors <= 3 else 5.0
                    time.sleep(wait)
                    # 继续外层 while，重试打开 recorder

        except Exception as e:
            logger.error(f"Capture loop fatal error: {type(e).__name__}: {e}")
        finally:
            loop.close()
            logger.info(
                "Capture loop finished: %d chunks, %d loopback timeouts",
                chunk_count, loopback_timeouts,
            )

    def switch_device(self, device_type: str, device_id: str) -> bool:
        """手动切换到指定设备（录制中可调用）
        
        切换后 recorder 将自动重建，转写不中断。
        
        Args:
            device_type: "loopback" 或 "input"
            device_id: 目标设备 ID，空字符串表示回退到系统默认
        
        Returns:
            True 如果成功找到并切换设备
        """
        if device_type not in ("loopback", "input"):
            logger.warning(f"switch_device: invalid device_type={device_type}")
            return False

        try:
            if device_type == "loopback":
                old_name = self._loopback_mic.name if self._loopback_mic else "none"
            else:
                old_name = self._input_mic.name if self._input_mic else "none"

            new_mic = None

            if device_id:
                # 按 ID 查找指定设备
                include_lb = (device_type == "loopback")
                for m in sc.all_microphones(include_loopback=include_lb):
                    if m.id == device_id:
                        new_mic = m
                        break
                if not new_mic:
                    logger.warning(f"switch_device: device_id={device_id} not found")
                    return False
            else:
                # 空 ID → 回退系统默认
                if device_type == "loopback":
                    speaker_ids = {s.id for s in sc.all_speakers()}
                    mics = [m for m in sc.all_microphones(include_loopback=True)
                            if m.id in speaker_ids]
                    if mics:
                        new_mic = mics[0]
                    else:
                        speakers = sc.all_speakers()
                        if speakers:
                            new_mic = sc.get_microphone(speakers[0].id, include_loopback=True)
                else:
                    new_mic = sc.default_microphone()

            if not new_mic:
                logger.warning(f"switch_device: no device available for {device_type}")
                return False

            # 更新设备引用
            if device_type == "loopback":
                self._loopback_mic = new_mic
            else:
                self._input_mic = new_mic

            logger.info(
                "Manual switch %s: %s -> %s",
                device_type, old_name, new_mic.name,
            )

            # 触发回调通知前端（手动切换总是通知，即便目标与当前设备相同）
            self._fire_device_switched(device_type, old_name, new_mic.name, only_on_change=False)

            # 通知 capture loop 重建 recorder
            self._pending_recorder_restart = True
            return True

        except Exception as e:
            logger.error(f"switch_device error: {e}")
            return False

    def _loopback_reader_loop(self, recorder):
        """专用 loopback 读取线程：持续调用 record() 并存入队列

        当系统无音频播放时 record() 阻塞，不影响主采集循环（队列空 → 静音）。
        当音频播放时数据通过队列流向主循环，无锁竞争、无线程创建开销。
        """
        while self._loopback_reader_active:
            try:
                data = recorder.record(numframes=self.chunk_size)
                try:
                    self._loopback_queue.put_nowait(data)
                except queue.Full:
                    # 队列满：丢弃最旧数据后放入（控制延迟）
                    try:
                        self._loopback_queue.get_nowait()
                        self._loopback_queue.put_nowait(data)
                    except queue.Empty:
                        pass
            except Exception:
                break
