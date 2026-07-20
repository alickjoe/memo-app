"""
Memo - 会议纪要应用后端
FastAPI + WebSocket 服务，支持音频捕获、云端 STT、LLM 纪要生成
"""
import os
import json
import uuid
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from concurrent.futures import ThreadPoolExecutor

from storage.db import get_db, init_db
from audio.capture import AudioCapture
from audio.vad import VoiceActivityDetector
from stt.engine import STTEngine
from diarization.speaker import SpeakerDiarizer
from llm.summarizer import LLMSummarizer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memo-backend")

# 全局状态
active_captures: dict[str, AudioCapture] = {}
active_stt: dict[str, STTEngine] = {}
ws_connections: dict[str, list[WebSocket]] = {}
_retranscribe_executor = ThreadPoolExecutor(max_workers=1)

# 初始化音频捕获器（单例）
audio_capture: Optional[AudioCapture] = None
vad: Optional[VoiceActivityDetector] = None
stt_engine: Optional[STTEngine] = None
diarizer: Optional[SpeakerDiarizer] = None
summarizer: Optional[LLMSummarizer] = None


def get_port() -> int:
    """获取端口，优先从环境变量读取"""
    return int(os.environ.get("BACKEND_PORT", "8765"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global audio_capture, vad, stt_engine, diarizer, summarizer

    await init_db()
    logger.info("Database initialized")

    audio_capture = AudioCapture()
    vad = VoiceActivityDetector()
    await vad._load_model()  # 预加载 Silero VAD 模型
    stt_engine = STTEngine()
    diarizer = SpeakerDiarizer()
    summarizer = LLMSummarizer()

    logger.info("All engines initialized")
    yield

    # 清理
    for capture in active_captures.values():
        await capture.stop()
    logger.info("Backend shutdown complete")


app = FastAPI(title="Memo Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Health ====================

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/api/shutdown")
async def shutdown():
    """优雅关闭"""
    for capture in active_captures.values():
        await capture.stop()
    return {"status": "shutting_down"}


# ==================== Audio Devices ====================

@app.get("/api/audio/devices")
async def list_devices():
    """获取可用音频设备列表"""
    devices = audio_capture.list_devices() if audio_capture else []
    return {"devices": devices}


@app.post("/api/audio/scan-devices")
async def scan_devices(request: dict = None):
    """扫描设备信号状态
    
    Args:
        device_type: "loopback" | "input" | "all" (默认 all)
        duration: 检测时长秒数 (默认 2.0)
    """
    request = request or {}
    device_type = request.get("device_type", "all")
    duration = float(request.get("duration", 2.0))

    if not audio_capture:
        return {"devices": []}

    results = audio_capture.scan_devices_for_signal(device_type, duration)
    return {
        "devices": [
            {
                "device_id": r.device_id,
                "device_name": r.device_name,
                "has_signal": r.has_signal,
                "rms_level": r.rms_level,
                "is_loopback": r.is_loopback,
            }
            for r in results
        ],
    }


@app.get("/api/audio/signal-status")
async def signal_status():
    """获取当前录制设备信号状态"""
    if not audio_capture:
        return {"error": "No audio capture active"}
    return audio_capture.signal_stats


# ==================== Recording ====================

@app.post("/api/record/start")
async def start_recording(request: dict = None):
    """开始录制，使用系统默认音频设备。转写配置在启动时确定，不可中途变更。"""
    meeting_id = str(uuid.uuid4())
    request = request or {}

    # 合并录音配置：请求参数 > settings 默认值
    defaults = await _load_recording_defaults()
    override = request.get("config", {}) or {}
    recording_config = RecordingConfig(
        segmentation_strategy=override.get("segmentation_strategy", defaults.segmentation_strategy),
        max_segment_duration=float(override.get("max_segment_duration", defaults.max_segment_duration)),
        fixed_chunk_duration=float(override.get("fixed_chunk_duration", defaults.fixed_chunk_duration)),
        vad_threshold=float(override.get("vad_threshold", defaults.vad_threshold)),
        vad_silence_frames=int(override.get("vad_silence_frames", defaults.vad_silence_frames)),
        vad_speech_confirm_frames=int(override.get("vad_speech_confirm_frames", defaults.vad_speech_confirm_frames)),
        vad_hangover_frames=int(override.get("vad_hangover_frames", defaults.vad_hangover_frames)),
    )
    _active_recording_configs[meeting_id] = recording_config

    try:
        # 设置设备切换回调：通知前端（手动切换时触发）
        async def on_device_switched(device_type: str, old_name: str, new_name: str):
            msg = {
                "type": "device_switched",
                "device_type": device_type,
                "old_device": old_name,
                "new_device": new_name,
            }
            for ws in ws_connections.get(meeting_id, []):
                try:
                    await ws.send_json(msg)
                except Exception:
                    pass

        audio_capture.set_device_switch_callback(on_device_switched)

        await audio_capture.start(meeting_id)
        active_captures[meeting_id] = audio_capture

        # 创建会议记录
        db = await get_db()
        await db.execute(
            "INSERT INTO meetings (id, title, created_at, status) VALUES (?, ?, ?, ?)",
            (meeting_id, "Untitled Meeting", datetime.now().isoformat(), "recording"),
        )
        await db.commit()

        # 启动音频处理流水线
        asyncio.create_task(process_audio_pipeline(meeting_id))

        return {
            "meeting_id": meeting_id,
            "status": "recording",
            "loopback_device_name": audio_capture.loopback_device_name,
            "input_device_name": audio_capture.input_device_name,
            "config": {
                "segmentation_strategy": recording_config.segmentation_strategy,
                "max_segment_duration": recording_config.max_segment_duration,
                "fixed_chunk_duration": recording_config.fixed_chunk_duration,
                "vad_threshold": recording_config.vad_threshold,
            },
        }

    except Exception as e:
        _active_recording_configs.pop(meeting_id, None)
        logger.error(f"Failed to start recording: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/record/stop")
async def stop_recording():
    """停止录制"""
    audio_capture.stop()
    for mid in list(active_captures.keys()):
        if active_captures[mid] is audio_capture:
            del active_captures[mid]

    return {"status": "stopped"}


@app.post("/api/record/pause")
async def pause_recording():
    """暂停录制"""
    audio_capture.pause()
    return {"status": "paused"}


@app.post("/api/record/resume")
async def resume_recording():
    """恢复录制"""
    audio_capture.resume()
    return {"status": "recording"}


@app.post("/api/record/switch-device")
async def switch_device(request: dict = None):
    """录制中手动切换音频设备
    
    Args:
        device_type: "loopback" | "input"
        device_id: 目标设备 ID，空字符串表示回退系统默认
    """
    request = request or {}
    device_type = request.get("device_type", "")
    device_id = request.get("device_id", "")

    if not audio_capture:
        return {"error": "No active capture"}, 400

    if device_type not in ("loopback", "input"):
        return {"error": f"Invalid device_type: {device_type}"}, 400

    success = audio_capture.switch_device(device_type, device_id)
    if not success:
        return {"error": "Device not found or switch failed"}, 404

    return {
        "status": "switched",
        "device_type": device_type,
        "device_name": audio_capture.loopback_device_name if device_type == "loopback" else audio_capture.input_device_name,
    }


# ==================== Meetings ====================

@app.get("/api/meetings")
async def list_meetings():
    """获取会议列表"""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, title, duration_seconds, created_at, status FROM meetings ORDER BY created_at DESC"
    )
    rows = await cursor.fetchall()
    meetings = [
        {
            "id": row[0],
            "title": row[1],
            "duration_seconds": row[2] or 0,
            "created_at": row[3],
            "status": row[4],
        }
        for row in rows
    ]
    return {"meetings": meetings}


@app.get("/api/meetings/{meeting_id}")
async def get_meeting(meeting_id: str):
    """获取会议详情"""
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, title, audio_path, duration_seconds, created_at, status FROM meetings WHERE id = ?",
        (meeting_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return {"error": "Meeting not found"}, 404

    meeting = {
        "id": row[0],
        "title": row[1],
        "audio_path": row[2],
        "duration_seconds": row[3] or 0,
        "created_at": row[4],
        "status": row[5],
    }

    # 获取纪要
    cursor = await db.execute(
        "SELECT summary, key_points, action_items, next_steps FROM minutes WHERE meeting_id = ?",
        (meeting_id,),
    )
    min_row = await cursor.fetchone()
    minutes = None
    if min_row:
        minutes = {
            "summary": min_row[0] or "",
            "key_points": json.loads(min_row[1]) if min_row[1] else [],
            "action_items": json.loads(min_row[2]) if min_row[2] else [],
            "next_steps": min_row[3] or "",
        }

    # 获取转写记录
    cursor = await db.execute(
        "SELECT speaker, text, start_time, end_time, version FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time",
        (meeting_id,),
    )
    ts_rows = await cursor.fetchall()
    transcripts = [
        {
            "speaker": row[0],
            "text": row[1],
            "start_time": row[2],
            "end_time": row[3],
            "version": row[4] or 1,
        }
        for row in ts_rows
    ]

    # 获取可用版本列表
    cursor = await db.execute(
        "SELECT DISTINCT version FROM transcript_segments WHERE meeting_id = ? ORDER BY version",
        (meeting_id,),
    )
    ver_rows = await cursor.fetchall()
    transcript_versions = [row[0] or 1 for row in ver_rows]

    return {"meeting": meeting, "minutes": minutes, "transcripts": transcripts, "transcript_versions": transcript_versions}


@app.delete("/api/meetings/{meeting_id}")
async def delete_meeting(meeting_id: str):
    """删除会议及其关联数据"""
    db = await get_db()

    # 先获取音频路径
    cursor = await db.execute(
        "SELECT audio_path FROM meetings WHERE id = ?", (meeting_id,)
    )
    row = await cursor.fetchone()
    audio_path = row[0] if row else None

    await db.execute("DELETE FROM transcript_segments WHERE meeting_id = ?", (meeting_id,))
    await db.execute("DELETE FROM minutes WHERE meeting_id = ?", (meeting_id,))
    await db.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
    await db.commit()

    # 删除磁盘上的音频文件
    if audio_path and os.path.exists(audio_path):
        try:
            os.remove(audio_path)
            logger.info(f"Deleted audio file: {audio_path}")
        except Exception as e:
            logger.warning(f"Failed to delete audio file {audio_path}: {e}")

    return {"status": "deleted"}


@app.post("/api/meetings/{meeting_id}/regenerate")
async def regenerate_minutes(meeting_id: str):
    """重新生成会议纪要"""
    db = await get_db()

    # 获取所有转写文本
    cursor = await db.execute(
        "SELECT speaker, text FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time",
        (meeting_id,),
    )
    rows = await cursor.fetchall()
    transcript_text = "\n".join([f"[{row[0]}]: {row[1]}" for row in rows])

    if not transcript_text:
        return {"error": "No transcript found"}, 404

    # 重新生成
    asyncio.create_task(generate_minutes(meeting_id, transcript_text))
    return {"status": "regenerating"}


@app.post("/api/meetings/{meeting_id}/retranscribe")
async def retranscribe_meeting(meeting_id: str):
    """重新转写完整音频文件（离线高质量模式）
    
    短音频（< 25MB）：整段发送，按句子拆分
    长音频（>= 25MB）：重叠滑动窗口分段
    结果存入新版本，不覆盖原有转写
    """
    db = await get_db()

    # 获取会议信息
    cursor = await db.execute(
        "SELECT audio_path, status FROM meetings WHERE id = ?",
        (meeting_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return {"error": "Meeting not found"}, 404

    audio_path = row[0]

    # 如果数据库没有 audio_path，尝试从 recordings 目录按 meeting_id 查找
    recordings_dir = os.path.join(os.path.expanduser("~"), ".memo", "recordings")
    if not audio_path:
        guessed_path = os.path.join(recordings_dir, f"{meeting_id}.wav")
        if os.path.exists(guessed_path):
            audio_path = guessed_path
            # 更新数据库中的 audio_path
            await db.execute(
                "UPDATE meetings SET audio_path = ? WHERE id = ?",
                (audio_path, meeting_id),
            )
            await db.commit()
            print(f"[RETRANSCRIBE] Fixed missing audio_path: {audio_path}", flush=True)
        else:
            # 检查录音目录中是否有匹配的文件
            import glob as glob_mod
            pattern = os.path.join(recordings_dir, f"{meeting_id}.*")
            matches = glob_mod.glob(pattern)
            if matches:
                audio_path = matches[0]
                await db.execute(
                    "UPDATE meetings SET audio_path = ? WHERE id = ?",
                    (audio_path, meeting_id),
                )
                await db.commit()
                print(f"[RETRANSCRIBE] Fixed missing audio_path (glob): {audio_path}", flush=True)
            else:
                return {"error": f"No audio file associated with this meeting (expected at {guessed_path})"}, 400

    if not os.path.exists(audio_path):
        return {"error": f"Audio file not found: {audio_path}"}, 404

    # 确定新版本号
    cursor = await db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM transcript_segments WHERE meeting_id = ?",
        (meeting_id,),
    )
    max_ver_row = await cursor.fetchone()
    new_version = (max_ver_row[0] or 0) + 1

    # 标记为 processing
    await db.execute(
        "UPDATE meetings SET status = ? WHERE id = ?",
        ("processing", meeting_id),
    )
    await db.commit()

    print(f"[RETRANSCRIBE] BEFORE executor.submit: meeting={meeting_id}", flush=True)

    # 使用独立线程池执行重转写（完全绕过 asyncio 事件循环）
    future = _retranscribe_executor.submit(_run_retranscribe_sync, meeting_id, audio_path, new_version)
    future.add_done_callback(lambda f: (
        logger.info("Retranscribe thread completed: meeting=%s version=%d", meeting_id, new_version)
        if not f.exception() else
        logger.error("Retranscribe thread crashed: meeting=%s error=%s", meeting_id, f.exception())
    ))
    print(f"[RETRANSCRIBE] AFTER executor.submit: meeting={meeting_id}", flush=True)

    return {
        "status": "processing",
        "version": new_version,
        "message": f"Re-transcription started (version {new_version})",
    }


# ==================== Audio Import ====================

@app.post("/api/import/audio")
async def import_audio(request: dict):
    """导入音频文件"""
    file_path = request.get("file_path")
    if not file_path or not os.path.exists(file_path):
        return {"error": "File not found"}, 404

    meeting_id = str(uuid.uuid4())
    db = await get_db()
    await db.execute(
        "INSERT INTO meetings (id, title, audio_path, created_at, status) VALUES (?, ?, ?, ?, ?)",
        (meeting_id, f"Imported: {os.path.basename(file_path)}", file_path, datetime.now().isoformat(), "processing"),
    )
    await db.commit()

    # 启动离线处理
    asyncio.create_task(process_imported_audio(meeting_id, file_path))

    return {"meeting_id": meeting_id, "status": "processing"}


# ==================== Settings ====================

@app.get("/api/settings")
async def get_settings():
    """获取设置"""
    db = await get_db()
    cursor = await db.execute("SELECT key, value FROM settings")
    rows = await cursor.fetchall()
    return {row[0]: row[1] for row in rows}


@app.put("/api/settings")
async def update_settings(settings: dict):
    """更新设置"""
    db = await get_db()
    for key, value in settings.items():
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
    await db.commit()

    # 重新加载 STT 和 LLM 配置
    if stt_engine:
        await stt_engine.reload_config()
    if summarizer:
        await summarizer.reload_config()

    return {"status": "saved"}


# ==================== System / Torch ====================

@app.get("/api/system/backend-mode")
async def get_backend_mode():
    """返回当前后端运行模式"""
    import sys
    is_frozen = getattr(sys, 'frozen', False)
    return {"mode": "frozen" if is_frozen else "source"}


@app.get("/api/system/torch-status")
async def get_torch_status():
    """检测 PyTorch 是否可用"""
    import sys
    is_frozen = getattr(sys, 'frozen', False)
    vad_engine = "energy"
    vad_error: Optional[str] = None
    if vad is not None:
        if vad.is_degraded:
            vad_engine = "energy"
            vad_error = vad.last_error
        else:
            vad_engine = "silero"
    try:
        import torch
        version = torch.__version__
        cuda_available = torch.cuda.is_available()
        return {
            "available": True,
            "version": version,
            "cuda_available": cuda_available,
            "backend_mode": "frozen" if is_frozen else "source",
            "vad_engine": vad_engine,
            "vad_error": vad_error,
        }
    except ImportError:
        return {
            "available": False,
            "version": None,
            "cuda_available": False,
            "backend_mode": "frozen" if is_frozen else "source",
            "vad_engine": vad_engine,
            "vad_error": vad_error,
        }


@app.post("/api/system/install-torch")
async def install_torch():
    """安装 PyTorch（仅在源码模式下可用）"""
    import sys
    import subprocess

    if getattr(sys, 'frozen', False):
        return {
            "success": False,
            "error": "Cannot install packages in frozen mode. Please install Python and run from source.",
        }

    try:
        # 使用 CPU 版 torch + torchaudio（Silero VAD 需要 torchaudio）
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "torch", "torchaudio",
             "--index-url", "https://download.pytorch.org/whl/cpu"],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode == 0:
            logger.info("PyTorch installed successfully")
            return {"success": True, "message": "PyTorch installed. Restart backend to enable Silero VAD."}
        else:
            logger.error(f"PyTorch installation failed: {result.stderr}")
            return {"success": False, "error": result.stderr[-500:]}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Installation timed out (10 minutes)"}
    except Exception as e:
        logger.error(f"PyTorch installation error: {e}")
        return {"success": False, "error": str(e)}


# ==================== Usage Stats ====================

@app.get("/api/usage/stats")
async def usage_stats():
    """获取 API 用量统计"""
    return {
        "stt_calls": stt_engine.call_count if stt_engine else 0,
        "llm_calls": summarizer.call_count if summarizer else 0,
        "estimated_cost": f"${(stt_engine.estimated_cost if stt_engine else 0) + (summarizer.estimated_cost if summarizer else 0):.4f}",
    }


# ==================== WebSocket ====================

@app.websocket("/ws/transcript/{meeting_id}")
async def ws_transcript(websocket: WebSocket, meeting_id: str):
    """实时转写 WebSocket"""
    await websocket.accept()

    if meeting_id not in ws_connections:
        ws_connections[meeting_id] = []
    ws_connections[meeting_id].append(websocket)

    try:
        while True:
            await websocket.receive_text()  # 保持连接
    except WebSocketDisconnect:
        ws_connections[meeting_id].remove(websocket)


@app.websocket("/ws/minutes/{meeting_id}")
async def ws_minutes(websocket: WebSocket, meeting_id: str):
    """纪要生成进度 WebSocket"""
    await websocket.accept()

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass


# ==================== Recording Config ====================

@dataclass
class RecordingConfig:
    """录音/转写配置，可按会议级别覆盖"""
    segmentation_strategy: str = "hybrid"  # "vad" | "fixed" | "hybrid"
    max_segment_duration: float = 15.0     # 最大分段时长 (秒)
    fixed_chunk_duration: float = 30.0     # 固定分段时长 (秒)，fixed 策略使用
    vad_threshold: float = 0.6             # VAD 语音概率阈值
    vad_silence_frames: int = 8            # 连续静音帧数触发断句
    vad_speech_confirm_frames: int = 3     # 连续语音帧确认 speech start
    vad_hangover_frames: int = 3           # 语音结束后拖尾帧数

    # 内部派生值（非配置项）
    min_speech_bytes: int = field(default=32000)    # 2s 最小语音段
    context_bytes: int = field(default=8000)          # 0.5s 上下文前导


# 全局录音配置（存储当前会议的配置）
_active_recording_configs: dict[str, RecordingConfig] = {}

# 录音默认配置的 settings key 前缀
RECORDING_CONFIG_KEYS = [
    "recording_segmentation_strategy",
    "recording_max_segment_duration",
    "recording_fixed_chunk_duration",
    "recording_vad_threshold",
    "recording_vad_silence_frames",
    "recording_vad_speech_confirm_frames",
    "recording_vad_hangover_frames",
]


async def _load_recording_defaults() -> RecordingConfig:
    """从 settings 表加载录音默认配置"""
    config = RecordingConfig()
    db = await get_db()
    cursor = await db.execute("SELECT key, value FROM settings")
    rows = await cursor.fetchall()
    s = {row[0]: row[1] for row in rows}

    if s.get("recording_segmentation_strategy"):
        config.segmentation_strategy = s["recording_segmentation_strategy"]
    if s.get("recording_max_segment_duration"):
        config.max_segment_duration = float(s["recording_max_segment_duration"])
    if s.get("recording_fixed_chunk_duration"):
        config.fixed_chunk_duration = float(s["recording_fixed_chunk_duration"])
    if s.get("recording_vad_threshold"):
        config.vad_threshold = float(s["recording_vad_threshold"])
    if s.get("recording_vad_silence_frames"):
        config.vad_silence_frames = int(s["recording_vad_silence_frames"])
    if s.get("recording_vad_speech_confirm_frames"):
        config.vad_speech_confirm_frames = int(s["recording_vad_speech_confirm_frames"])
    if s.get("recording_vad_hangover_frames"):
        config.vad_hangover_frames = int(s["recording_vad_hangover_frames"])
    return config


def _merge_config(request_config: Optional[dict]) -> RecordingConfig:
    """合并请求配置到 RecordingConfig"""
    config = RecordingConfig()
    if request_config:
        for key in ["segmentation_strategy", "max_segment_duration", "fixed_chunk_duration",
                     "vad_threshold", "vad_silence_frames", "vad_speech_confirm_frames",
                     "vad_hangover_frames"]:
            if key in request_config and request_config[key] is not None:
                setattr(config, key, request_config[key])
    return config


# ==================== Audio Processing Pipeline ====================


async def _transcribe_segment(
    meeting_id: str, db, segment_bytes: bytes, seg_duration: float,
    segment_start: float, segment_end: float,
    prev_segment_tail: bytearray, speaker_idx: int,
    segment_count: int, reason: str,
):
    """处理单个语音段：说话人识别 + STT 转写 + 存储 + 推送
    
    Returns:
        (new_segment_count, new_discarded_count, next_context_tail, new_speaker_idx)
    """
    speaker_label = await diarizer.identify(segment_bytes)
    if not speaker_label:
        speaker_idx += 1
        speaker_label = f"Speaker {chr(65 + (speaker_idx % 26))}"

    asr_input = bytes(prev_segment_tail) + segment_bytes
    text = await stt_engine.transcribe_with_validation(asr_input)

    discarded = 0
    if text:
        segment_count += 1
        await db.execute(
            "INSERT INTO transcript_segments (meeting_id, speaker, start_time, end_time, text) VALUES (?, ?, ?, ?, ?)",
            (meeting_id, speaker_label, segment_start, segment_end, text),
        )
        await db.commit()

        segment_data = {
            "type": "transcript",
            "segment": {
                "speaker": speaker_label,
                "text": text,
                "start_time": segment_start,
                "end_time": segment_end,
            },
        }
        for ws in ws_connections.get(meeting_id, []):
            try:
                await ws.send_json(segment_data)
            except Exception:
                pass

        logger.info(
            "Pipeline: segment #%d transcribed: dur=%.1fs, text_len=%d, "
            "speaker=%s, reason=%s",
            segment_count, seg_duration, len(text),
            speaker_label, reason,
        )
    else:
        discarded += 1
        logger.info(
            "Pipeline: segment discarded by validation: dur=%.1fs, reason=%s",
            seg_duration, reason,
        )

    tail_len = min(RecordingConfig.context_bytes, len(segment_bytes))
    next_tail = bytearray(segment_bytes[-tail_len:])

    return segment_count, discarded, next_tail, speaker_idx


async def process_audio_pipeline(meeting_id: str):
    """实时音频处理流水线（多策略分段 + 滞回 VAD）"""
    # 获取该会议的配置
    config = _active_recording_configs.get(meeting_id)
    if config is None:
        config = await _load_recording_defaults()
        _active_recording_configs[meeting_id] = config

    strategy = config.segmentation_strategy
    # VAD 降级时自动切换到 fixed 策略
    vad_degraded = vad.is_degraded if vad else False
    if strategy in ("vad", "hybrid") and vad_degraded:
        logger.warning(
            "VAD is degraded, switching from '%s' to 'fixed' strategy for meeting %s",
            strategy, meeting_id,
        )
        strategy = "fixed"
        _active_recording_configs[meeting_id].segmentation_strategy = "fixed"

    logger.info(
        "Starting audio pipeline for meeting %s (strategy=%s, VAD degraded=%s, %s=%.1fs)",
        meeting_id, strategy, vad_degraded,
        "fixed_chunk" if strategy == "fixed" else "max_seg",
        config.fixed_chunk_duration if strategy == "fixed" else config.max_segment_duration,
    )

    # 更新 VAD 参数
    if vad:
        vad.threshold = config.vad_threshold
        vad.min_consecutive_speech = config.vad_speech_confirm_frames
        vad.hangover_frames = config.vad_hangover_frames

    db = await get_db()
    speech_buffer = bytearray()
    audio_offset = 0.0
    segment_start_offset = 0.0
    speaker_idx = 0
    chunk_read_count = 0
    timeout_count = 0
    silence_count = 0
    segment_count = 0
    discarded_count = 0
    prev_segment_tail = bytearray()
    # fixed 策略：跟踪累积时长
    fixed_accumulated = 0.0

    try:
        while audio_capture and audio_capture.is_active():
            audio_chunk = await audio_capture.read_chunk(timeout=1.0)
            if not audio_chunk:
                timeout_count += 1
                if timeout_count % 5 == 1:
                    logger.info("Pipeline: read_chunk timeout #%d", timeout_count)
                continue

            chunk_read_count += 1
            chunk_duration = len(audio_chunk) / (16000 * 2)

            if strategy == "fixed":
                # === 固定时长策略：忽略 VAD，按时间切分 ===
                if len(speech_buffer) == 0:
                    segment_start_offset = audio_offset
                speech_buffer.extend(audio_chunk)
                fixed_accumulated += chunk_duration

                if fixed_accumulated >= config.fixed_chunk_duration:
                    segment_bytes = bytes(speech_buffer)
                    seg_duration = len(segment_bytes) / (16000 * 2)
                    segment_count, new_discarded, prev_segment_tail, speaker_idx = \
                        await _transcribe_segment(
                            meeting_id, db, segment_bytes, seg_duration,
                            segment_start_offset, audio_offset + chunk_duration,
                            prev_segment_tail, speaker_idx, segment_count, "fixed_chunk",
                        )
                    discarded_count += new_discarded
                    speech_buffer = bytearray()
                    fixed_accumulated = 0.0
            else:
                # === VAD / Hybrid 策略 ===
                vad_state = await vad.detect_with_hysteresis(audio_chunk)

                if vad_state in ('speech', 'hangover', 'pending'):
                    silence_count = 0
                    if len(speech_buffer) == 0:
                        segment_start_offset = audio_offset
                        logger.info("Pipeline: speech start at offset=%.1fs", audio_offset)
                    speech_buffer = vad.append_buffer(speech_buffer, audio_chunk)

                elif vad_state == 'silence':
                    if len(speech_buffer) > 0:
                        silence_count += 1

                # 强制最大时长检查
                # Hybrid: max_duration 和生产级静音检测共同保护
                # VAD: 仅靠静音检测，不设短时限截断（300s 硬上限防内存溢出）
                if len(speech_buffer) > 0:
                    buffer_duration = len(speech_buffer) / (16000 * 2)
                    should_segment = False
                    reason = ""

                    if silence_count >= config.vad_silence_frames:
                        should_segment = True
                        reason = "long_pause"
                    elif strategy == "hybrid" and buffer_duration >= config.max_segment_duration:
                        should_segment = True
                        reason = "max_duration"
                    elif strategy == "vad" and buffer_duration >= 300.0:
                        # 纯 VAD 安全网：仅防极端情况内存溢出
                        should_segment = True
                        reason = "vad_safety_max"

                    if should_segment:
                        segment_bytes = bytes(speech_buffer)
                        seg_duration = len(segment_bytes) / (16000 * 2)

                        if len(segment_bytes) >= config.min_speech_bytes:
                            segment_start = segment_start_offset
                            segment_end = audio_offset + chunk_duration

                            segment_count, new_discarded, prev_segment_tail, speaker_idx = \
                                await _transcribe_segment(
                                    meeting_id, db, segment_bytes, seg_duration,
                                    segment_start, segment_end,
                                    prev_segment_tail, speaker_idx, segment_count, reason,
                                )
                            discarded_count += new_discarded
                        else:
                            logger.debug(
                                "Pipeline: segment too short (%.1fs < 2.0s), skipped",
                                seg_duration,
                            )
                            tail_len = min(config.context_bytes, len(segment_bytes))
                            prev_segment_tail = bytearray(segment_bytes[-tail_len:])

                        speech_buffer = bytearray()
                        silence_count = 0
                        vad.reset_state()

            audio_offset += chunk_duration

            if chunk_read_count % 100 == 1:
                buffer_dur = len(speech_buffer) / (16000 * 2) if speech_buffer else 0
                logger.info(
                    "Pipeline: chunk #%d, offset=%.1fs, buffer=%.1fs, segments=%d, "
                    "discarded=%d, silence=%d",
                    chunk_read_count, audio_offset, buffer_dur,
                    segment_count, discarded_count, silence_count,
                )

    except Exception as e:
        logger.error(f"Pipeline error: {e}")

    finally:
        # 处理剩余缓冲区
        if len(speech_buffer) >= config.min_speech_bytes:
            asr_input = bytes(prev_segment_tail) + bytes(speech_buffer)
            text = await stt_engine.transcribe_with_validation(asr_input)
            if text:
                seg_dur = len(speech_buffer) / (16000 * 2)
                segment_count += 1
                await db.execute(
                    "INSERT INTO transcript_segments (meeting_id, speaker, start_time, end_time, text) VALUES (?, ?, ?, ?, ?)",
                    (meeting_id, f"Speaker {chr(65 + ((speaker_idx + 1) % 26))}", segment_start_offset, audio_offset, text),
                )
                await db.commit()
                logger.info("Pipeline: final segment transcribed: dur=%.1fs", seg_dur)

        # 清理会议配置
        _active_recording_configs.pop(meeting_id, None)

        duration = int(audio_offset)
        await db.execute(
            "UPDATE meetings SET duration_seconds = ?, status = ? WHERE id = ?",
            (duration, "processing", meeting_id),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT speaker, text FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time",
            (meeting_id,),
        )
        rows = await cursor.fetchall()
        transcript_text = "\n".join([f"[{row[0]}]: {row[1]}" for row in rows])

        if transcript_text:
            await generate_minutes(meeting_id, transcript_text)

        logger.info(
            "Pipeline completed for meeting %s: %d chunks, %d timeouts, "
            "%d segments transcribed, %d discarded, %ds duration",
            meeting_id, chunk_read_count, timeout_count,
            segment_count, discarded_count, int(audio_offset),
        )


def _run_retranscribe_sync(meeting_id: str, audio_path: str, version: int):
    """线程中运行的同步包装器，创建新事件循环执行 async _do_retranscribe"""
    print(f"[RETRANSCRIBE] sync wrapper started: meeting={meeting_id} version={version}", flush=True)
    try:
        asyncio.run(_do_retranscribe(meeting_id, audio_path, version))
        print(f"[RETRANSCRIBE] sync wrapper completed: meeting={meeting_id}", flush=True)
    except Exception as e:
        print(f"[RETRANSCRIBE] sync wrapper CRASHED: {e}", flush=True)
        import traceback
        traceback.print_exc()


async def _do_retranscribe(meeting_id: str, audio_path: str, version: int):
    """后台执行重新转写"""
    logger.info("Retranscribe task STARTED: meeting=%s version=%d audio=%s", meeting_id, version, audio_path)
    print(f"[RETRANSCRIBE] _do_retranscribe executing: meeting={meeting_id} version={version}", flush=True)
    db = await get_db()

    try:
        segments = await stt_engine.transcribe_file(audio_path)

        if not segments:
            logger.warning("Retranscribe produced no segments for meeting %s", meeting_id)
            await db.execute(
                "UPDATE meetings SET status = ? WHERE id = ?",
                ("error", meeting_id),
            )
            await db.commit()
            return

        # 存入新版本的转写段
        speaker_idx = 0
        for seg in segments:
            speaker_idx += 1
            speaker_label = f"Speaker {chr(65 + (speaker_idx % 26))}"
            await db.execute(
                "INSERT INTO transcript_segments (meeting_id, speaker, start_time, end_time, text, version) VALUES (?, ?, ?, ?, ?, ?)",
                (meeting_id, speaker_label, seg["start_time"], seg["end_time"], seg["text"], version),
            )
        await db.commit()

        logger.info(
            "Retranscribe stored %d segments (version %d) for meeting %s",
            len(segments), version, meeting_id,
        )

        # 用新版本转写生成纪要
        transcript_text = "\n".join(
            [f"[Speaker]: {seg['text']}" for seg in segments]
        )
        await generate_minutes(meeting_id, transcript_text)

        # 更新状态
        await db.execute(
            "UPDATE meetings SET status = ? WHERE id = ?",
            ("done", meeting_id),
        )
        await db.commit()

        # 推送完成通知
        for ws in ws_connections.get(meeting_id, []):
            try:
                await ws.send_json({
                    "type": "retranscribe_done",
                    "version": version,
                    "segments": len(segments),
                })
            except Exception:
                pass

    except Exception as e:
        logger.exception("Retranscribe error for meeting %s: %s", meeting_id, e)
        await db.execute(
            "UPDATE meetings SET status = ? WHERE id = ?",
            ("error", meeting_id),
        )
        await db.commit()


async def process_imported_audio(meeting_id: str, file_path: str):
    """处理导入的音频文件"""
    logger.info(f"Processing imported audio: {file_path}")

    import wave

    try:
        # 读取音频文件并转码为 16kHz mono
        # 简化处理：直接使用 ffmpeg 或 pydub 转码
        # 此处假设已转码为 16kHz 16bit mono WAV

        with wave.open(file_path, 'rb') as wf:
            frames = wf.readframes(wf.getnframes())

        # 分块处理
        chunk_size = 16000 * 10 * 2  # 10秒 chunks (16kHz * 2 bytes)
        offset = 0

        for i in range(0, len(frames), chunk_size):
            chunk = frames[i:i + chunk_size]
            if len(chunk) < 32000:  # 小于2秒跳过
                continue

            text = await stt_engine.transcribe_with_validation(chunk)
            if text:
                db = await get_db()
                await db.execute(
                    "INSERT INTO transcript_segments (meeting_id, speaker, start_time, end_time, text) VALUES (?, ?, ?, ?, ?)",
                    (meeting_id, "Speaker A", offset, offset + len(chunk) / 32000.0, text),
                )
                await db.commit()

            offset += len(chunk) / 32000.0

        # 生成纪要
        db = await get_db()
        cursor = await db.execute(
            "SELECT text FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time",
            (meeting_id,),
        )
        rows = await cursor.fetchall()
        transcript_text = "\n".join([row[0] for row in rows])

        if transcript_text:
            await generate_minutes(meeting_id, transcript_text)

        await db.execute(
            "UPDATE meetings SET status = ?, duration_seconds = ? WHERE id = ?",
            ("done", int(offset), meeting_id),
        )
        await db.commit()

    except Exception as e:
        logger.error(f"Import processing error: {e}")
        db = await get_db()
        await db.execute(
            "UPDATE meetings SET status = ? WHERE id = ?",
            ("error", meeting_id),
        )
        await db.commit()


async def generate_minutes(meeting_id: str, transcript_text: str):
    """生成会议纪要"""
    logger.info(f"Generating minutes for meeting {meeting_id}")
    db = await get_db()

    try:
        minutes_data = await summarizer.summarize(transcript_text)

        # 存储纪要
        await db.execute(
            """INSERT OR REPLACE INTO minutes (meeting_id, summary, key_points, action_items, next_steps, raw_response, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                meeting_id,
                minutes_data.get("summary", ""),
                json.dumps(minutes_data.get("key_points", []), ensure_ascii=False),
                json.dumps(minutes_data.get("action_items", []), ensure_ascii=False),
                minutes_data.get("next_steps", ""),
                minutes_data.get("raw_response", ""),
                datetime.now().isoformat(),
            ),
        )
        await db.execute(
            "UPDATE meetings SET status = ? WHERE id = ?",
            ("done", meeting_id),
        )
        await db.commit()

        # 推送进度
        for ws in ws_connections.get(meeting_id, []):
            try:
                await ws.send_json({"type": "minutes_ready", "minutes": minutes_data})
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Minutes generation error: {e}")
        await db.execute(
            "UPDATE meetings SET status = ? WHERE id = ?",
            ("error", meeting_id),
        )
        await db.commit()


# ==================== Entry Point ====================

if __name__ == "__main__":
    import uvicorn
    port = get_port()
    logger.info(f"Starting Memo backend on port {port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
