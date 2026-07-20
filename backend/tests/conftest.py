"""
共享测试 fixtures: 内存数据库、mock 全局单例、FastAPI TestClient
"""
import os
import sys
import pytest
import aiosqlite
from unittest.mock import AsyncMock, MagicMock, patch

# 确保 backend 在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_configure(config):
    """pytest 启动时注入 mock，防止 soundcard -> numpy C 扩展被重复加载"""
    if "soundcard" not in sys.modules:
        sys.modules["soundcard"] = MagicMock()


@pytest.fixture
def temp_data_dir(tmp_path):
    """临时数据目录，重定向 ~/.memo"""
    data_dir = tmp_path / ".memo"
    data_dir.mkdir()
    with patch.dict(os.environ, {"DATA_DIR": str(data_dir)}):
        yield str(data_dir)


@pytest.fixture
async def test_db(temp_data_dir):
    """内存 SQLite 测试数据库，替代真实 memo.db"""
    import storage.db as db_mod
    old_db = db_mod._db
    db_mod._db = None

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")

    # 创建表结构
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id TEXT PRIMARY KEY,
            title TEXT DEFAULT 'Untitled Meeting',
            audio_path TEXT,
            duration_seconds INTEGER DEFAULT 0,
            created_at DATETIME,
            status TEXT DEFAULT 'processing'
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS transcript_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id TEXT,
            speaker TEXT DEFAULT 'Speaker A',
            start_time REAL,
            end_time REAL,
            text TEXT,
            version INTEGER DEFAULT 1
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS minutes (
            meeting_id TEXT PRIMARY KEY,
            summary TEXT,
            key_points TEXT,
            action_items TEXT,
            next_steps TEXT,
            raw_response TEXT,
            generated_at DATETIME
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    db_mod._db = conn

    yield conn

    await conn.close()
    db_mod._db = old_db


@pytest.fixture
def mock_vad():
    """Mock VoiceActivityDetector"""
    vad = MagicMock()
    vad.detect_with_hysteresis = AsyncMock(return_value='speech')
    vad.detect = AsyncMock(return_value=True)
    vad.is_degraded = False
    vad.append_buffer = lambda buf, chunk: (buf.extend(chunk), buf)[1]
    vad.reset_state = MagicMock()
    vad.reset_buffer = MagicMock()
    vad.threshold = 0.6
    vad.min_consecutive_speech = 3
    vad.hangover_frames = 3
    return vad


@pytest.fixture
def mock_audio_capture():
    """Mock AudioCapture，提供可控的音频数据流"""
    capture = MagicMock()
    capture.is_active = MagicMock(return_value=False)
    capture.read_chunk = AsyncMock(return_value=None)
    capture.start = AsyncMock()
    capture.stop = MagicMock()
    capture.pause = MagicMock()
    capture.resume = MagicMock()
    capture.set_device_switch_callback = MagicMock()
    capture.list_devices = MagicMock(return_value=[])
    capture.scan_devices_for_signal = MagicMock(return_value=[])
    capture.switch_device = MagicMock(return_value=False)
    capture.signal_stats = {
        "loopback_rms": 0.0,
        "mic_rms": 0.0,
    }
    capture.loopback_device_name = "test-loopback"
    capture.input_device_name = "test-mic"
    return capture


@pytest.fixture
def mock_stt_engine():
    """Mock STTEngine - 默认返回有效转写文本"""
    engine = MagicMock()
    engine.transcribe = AsyncMock(return_value="测试转写文本")
    engine.transcribe_with_validation = AsyncMock(return_value="测试转写文本")
    engine.transcribe_file = AsyncMock(return_value=[])
    engine.reload_config = AsyncMock()
    engine.call_count = 0
    engine.estimated_cost = 0.0
    return engine


@pytest.fixture
def mock_diarizer():
    """Mock SpeakerDiarizer"""
    d = MagicMock()
    d.identify = AsyncMock(return_value=None)  # None 触发自动命名
    return d


@pytest.fixture
def mock_summarizer():
    """Mock LLMSummarizer"""
    s = MagicMock()
    s.summarize = AsyncMock(return_value={
        "summary": "测试摘要",
        "key_points": ["要点1", "要点2"],
        "action_items": [],
        "next_steps": "",
        "raw_response": "",
    })
    s.reload_config = AsyncMock()
    s.call_count = 0
    s.estimated_cost = 0.0
    return s


@pytest.fixture
def mock_all_globals(monkeypatch, mock_vad, mock_audio_capture,
                    mock_stt_engine, mock_diarizer, mock_summarizer):
    """一次性注入所有 mock 全局变量到 main 模块"""
    import main
    monkeypatch.setattr(main, "vad", mock_vad)
    monkeypatch.setattr(main, "stt_engine", mock_stt_engine)
    monkeypatch.setattr(main, "audio_capture", mock_audio_capture)
    monkeypatch.setattr(main, "diarizer", mock_diarizer)
    monkeypatch.setattr(main, "summarizer", mock_summarizer)
    monkeypatch.setattr(main, "active_captures", {})
    monkeypatch.setattr(main, "_active_recording_configs", {})
    monkeypatch.setattr(main, "ws_connections", {})
    return {
        "vad": mock_vad,
        "audio_capture": mock_audio_capture,
        "stt_engine": mock_stt_engine,
        "diarizer": mock_diarizer,
        "summarizer": mock_summarizer,
    }


def make_audio_bytes(duration_sec: float, with_signal: bool = True) -> bytes:
    """生成模拟 PCM 音频数据 (16kHz, 16-bit, mono)
    
    Args:
        duration_sec: 时长（秒）
        with_signal: True 生成有信号(正弦波), False 生成静音
    """
    import struct
    import math
    sample_rate = 16000
    num_samples = int(sample_rate * duration_sec)
    data = bytearray()
    for i in range(num_samples):
        if with_signal:
            # 440Hz 正弦波, 振幅 0.3 (int16 范围 ~9830)
            sample = int(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / sample_rate))
        else:
            sample = 0
        data.extend(struct.pack('<h', max(-32768, min(32767, sample))))
    return bytes(data)


def make_empty_chunk(duration_sec: float = 0.1) -> bytes:
    """生成静音 chunk"""
    return make_audio_bytes(duration_sec, with_signal=False)
