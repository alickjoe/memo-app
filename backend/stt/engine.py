"""
云端 STT 引擎 - 使用 OpenAI Whisper API 进行语音转写
"""
import os
import io
import logging
import asyncio
from typing import Optional

import httpx
import urllib3

# 禁用 SSL 验证警告（企业网络环境）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("memo.stt")


class STTEngine:
    """云端语音转写引擎"""

    def __init__(self):
        self.api_key: Optional[str] = None
        self.base_url: str = "https://api.openai.com/v1"
        self.model: str = "whisper-1"
        self.language: str = "zh"
        self.call_count: int = 0
        self.estimated_cost: float = 0.0

    async def reload_config(self):
        """重新加载配置"""
        from storage.db import get_db
        db = await get_db()
        cursor = await db.execute("SELECT key, value FROM settings")
        rows = await cursor.fetchall()
        settings = {row[0]: row[1] for row in rows}

        self.api_key = settings.get("api_key", os.environ.get("MEMO_API_KEY", ""))
        self.base_url = settings.get("api_base_url", os.environ.get("MEMO_API_BASE_URL", "https://api.openai.com/v1"))
        self.model = settings.get("stt_model", "whisper-1")
        self.language = settings.get("stt_language", "zh")

    async def _ensure_config(self):
        """确保配置已加载"""
        if not self.api_key:
            await self.reload_config()

    async def transcribe(self, audio_bytes: bytes) -> Optional[str]:
        """转写音频片段"""
        await self._ensure_config()

        if not self.api_key:
            logger.error("No API key configured for STT")
            return None

        if len(audio_bytes) < 1600:  # 小于 50ms 跳过
            return None

        try:
            # 创建临时 WAV 文件在内存中
            wav_buffer = io.BytesIO()
            import wave
            with wave.open(wav_buffer, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_bytes)

            wav_buffer.seek(0)

            # 调用 OpenAI Whisper API
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": ("audio.wav", wav_buffer, "audio/wav")},
                    data={
                        "model": self.model,
                        "language": self.language,
                        "response_format": "json",
                    },
                )

                if response.status_code == 200:
                    self.call_count += 1
                    # Whisper API 费用: $0.006 / 分钟
                    duration_minutes = len(audio_bytes) / (16000 * 2 * 60)
                    self.estimated_cost += duration_minutes * 0.006

                    result = response.json()
                    text = result.get("text", "").strip()
                    if text:
                        logger.debug(f"Transcribed: {text[:80]}...")
                        return text
                else:
                    logger.error(f"STT API error: {response.status_code} - {response.text}")

        except Exception as e:
            logger.error(f"STT error: {e}")

        return None
