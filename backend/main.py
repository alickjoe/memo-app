"""
Memo - 会议纪要应用后端
FastAPI + WebSocket 服务，支持音频捕获、云端 STT、LLM 纪要生成
"""
import os
import sys
import json
import uuid
import asyncio
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from storage.db import get_db, init_db
from storage.models import Meeting, TranscriptSegment, Minutes, AppSettings
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


# ==================== Recording ====================

@app.post("/api/record/start")
async def start_recording(request: dict = None):
    """开始录制，可指定音频设备"""
    meeting_id = str(uuid.uuid4())
    loopback_device_id = request.get("loopback_device_id") if request else None
    input_device_id = request.get("input_device_id") if request else None

    try:
        await audio_capture.start(
            meeting_id,
            loopback_device_id=loopback_device_id,
            input_device_id=input_device_id,
        )
        active_captures[meeting_id] = audio_capture

        # 创建会议记录
        db = await get_db()
        await db.execute(
            "INSERT INTO meetings (id, title, created_at, status) VALUES (?, ?, ?, ?)",
            (meeting_id, "未命名会议", datetime.now().isoformat(), "recording"),
        )
        await db.commit()

        # 启动音频处理流水线
        asyncio.create_task(process_audio_pipeline(meeting_id))

        return {"meeting_id": meeting_id, "status": "recording"}

    except Exception as e:
        logger.error(f"Failed to start recording: {e}")
        return {"error": str(e)}, 500


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
        "SELECT speaker, text, start_time, end_time FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time",
        (meeting_id,),
    )
    ts_rows = await cursor.fetchall()
    transcripts = [
        {
            "speaker": row[0],
            "text": row[1],
            "start_time": row[2],
            "end_time": row[3],
        }
        for row in ts_rows
    ]

    return {"meeting": meeting, "minutes": minutes, "transcripts": transcripts}


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
        (meeting_id, f"导入: {os.path.basename(file_path)}", file_path, datetime.now().isoformat(), "processing"),
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


# ==================== Audio Processing Pipeline ====================

async def process_audio_pipeline(meeting_id: str):
    """实时音频处理流水线"""
    logger.info(f"Starting audio pipeline for meeting {meeting_id}")

    db = await get_db()
    speech_buffer = bytearray()
    audio_offset = 0.0
    speaker_idx = 0
    chunk_read_count = 0
    timeout_count = 0
    speech_chunks = 0
    silence_chunks = 0
    silence_count = 0       # 连续静音计数，需 >=5 才结束语音段
    MIN_SPEECH_BYTES = 8000  # 最小语音段 0.5s (16kHz * 2bytes * 0.5)

    try:
        while audio_capture and audio_capture.is_active():
            # 获取音频块
            audio_chunk = await audio_capture.read_chunk(timeout=1.0)
            if not audio_chunk:
                timeout_count += 1
                if timeout_count % 5 == 1:
                    logger.info("Pipeline: read_chunk timeout #%d", timeout_count)
                continue

            chunk_read_count += 1
            if chunk_read_count % 20 == 1:
                logger.info(
                    "Pipeline: got chunk #%d (%d bytes), speech=%d silence=%d, offset=%.1fs",
                    chunk_read_count, len(audio_chunk), speech_chunks, silence_chunks, audio_offset,
                )

            # VAD 检测
            is_speech = await vad.detect(audio_chunk)
            if is_speech:
                speech_chunks += 1
                silence_count = 0  # 恢复语音，重置静音计数
                if len(speech_buffer) == 0:
                    logger.info("Pipeline: speech start detected at offset=%.1fs", audio_offset)
                speech_buffer = vad.append_buffer(speech_buffer, audio_chunk)
            elif len(speech_buffer) > 0:
                silence_count += 1
                silence_chunks += 1
                # 需连续 5 个静音 chunk（0.5s）才认为语音段结束
                if silence_count >= 5:
                    segment_start = audio_offset - len(speech_buffer) / 16000.0
                    segment_end = audio_offset

                    # 最小语音时长检查：不足 0.5s 丢弃
                    if len(speech_buffer) >= MIN_SPEECH_BYTES:
                        # 说话人识别
                        speaker_label = await diarizer.identify(speech_buffer)
                        if not speaker_label:
                            speaker_idx += 1
                            speaker_label = f"Speaker {chr(65 + (speaker_idx % 26))}"

                        # STT 转写
                        text = await stt_engine.transcribe(speech_buffer)

                        if text:
                            # 存储转写结果
                            await db.execute(
                                "INSERT INTO transcript_segments (meeting_id, speaker, start_time, end_time, text) VALUES (?, ?, ?, ?, ?)",
                                (meeting_id, speaker_label, segment_start, segment_end, text),
                            )
                            await db.commit()

                            # 推送到前端
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
                    else:
                        logger.debug("Pipeline: speech segment too short (%d bytes), skipped", len(speech_buffer))

                    speech_buffer = bytearray()
                    silence_count = 0
            else:
                silence_chunks += 1

            audio_offset += len(audio_chunk) / 16000.0

    except Exception as e:
        logger.error(f"Pipeline error: {e}")

    finally:
        # 录制结束，生成纪要
        duration = int(audio_offset)
        await db.execute(
            "UPDATE meetings SET duration_seconds = ?, status = ? WHERE id = ?",
            (duration, "processing", meeting_id),
        )
        await db.commit()

        # 获取完整转写文本
        cursor = await db.execute(
            "SELECT speaker, text FROM transcript_segments WHERE meeting_id = ? ORDER BY start_time",
            (meeting_id,),
        )
        rows = await cursor.fetchall()
        transcript_text = "\n".join([f"[{row[0]}]: {row[1]}" for row in rows])

        if transcript_text:
            await generate_minutes(meeting_id, transcript_text)

        logger.info(
            "Pipeline completed for meeting %s: %d chunks read, %d timeouts, %d seconds",
            meeting_id, chunk_read_count, timeout_count, int(audio_offset),
        )


async def process_imported_audio(meeting_id: str, file_path: str):
    """处理导入的音频文件"""
    logger.info(f"Processing imported audio: {file_path}")

    import wave
    import io

    try:
        # 读取音频文件并转码为 16kHz mono
        # 简化处理：直接使用 ffmpeg 或 pydub 转码
        # 此处假设已转码为 16kHz 16bit mono WAV

        with wave.open(file_path, 'rb') as wf:
            frames = wf.readframes(wf.getnframes())

        # 分块处理
        chunk_size = 16000 * 5 * 2  # 5秒 chunks (16kHz * 2 bytes)
        offset = 0

        for i in range(0, len(frames), chunk_size):
            chunk = frames[i:i + chunk_size]
            if len(chunk) < 16000:  # 小于1秒跳过
                continue

            text = await stt_engine.transcribe(chunk)
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
