"""
数据模型定义
"""
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Meeting:
    id: str
    title: str = "未命名会议"
    audio_path: Optional[str] = None
    duration_seconds: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "recording"  # recording / processing / done / error


@dataclass
class TranscriptSegment:
    meeting_id: str
    speaker: str = "Speaker A"
    start_time: float = 0.0
    end_time: float = 0.0
    text: str = ""
    id: Optional[int] = None


@dataclass
class Minutes:
    meeting_id: str
    summary: str = ""
    key_points: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    next_steps: str = ""
    raw_response: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class AppSettings:
    api_key: str = ""
    api_base_url: str = "https://api.openai.com/v1"
    stt_model: str = "whisper-1"
    stt_language: str = "zh"
    stt_api_key: str = ""
    stt_api_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_output_language: str = "en"
