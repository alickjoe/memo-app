"""
云端 STT 引擎 - 使用 OpenAI Whisper API 进行语音转写
"""
import os
import io
import logging
import re
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

        # STT 专用配置，stt_api_base_url 为空时默认使用 OpenAI（不回退到通用 api_base_url）
        self.api_key = settings.get("stt_api_key") or os.environ.get("MEMO_API_KEY", "")
        self.base_url = settings.get("stt_api_base_url") or os.environ.get("MEMO_STT_API_BASE_URL", "https://api.openai.com/v1")
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

            # 调用语音转写 API
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                data = {"model": self.model}
                # language 为 auto 或空时不传，由 API 自动检测
                if self.language and self.language != "auto":
                    data["language"] = self.language

                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": ("audio.wav", wav_buffer, "audio/wav")},
                    data=data,
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

    async def transcribe_with_validation(self, audio_bytes: bytes) -> Optional[str]:
        """转写音频片段并进行输出校验，过滤乱码和无效结果

        校验规则：
        1. 不为空或纯标点符号
        2. 有效字符（中文 + 英文 + 数字）占比 >= 30%
        3. 输出长度与音频时长匹配（不超过 20 字/秒，防止幻觉）
        """
        text = await self.transcribe(audio_bytes)
        if not text:
            return None

        text = text.strip()
        if not text:
            return None

        # 校验1: 不为纯标点符号
        punctuation_only_pattern = re.compile(
            r'^[\s\u3000-\u303f\uff00-\uffef，。！？、；：""''…—～·　\-\.,!?;:"\'()\[\]{}<>]+$'
        )
        if punctuation_only_pattern.match(text):
            logger.debug("Validation rejected: punctuation-only output: %s", text[:80])
            return None

        # 校验2: 有效字符占比检查
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf')
        english_chars = sum(1 for c in text if c.isascii() and c.isalpha())
        digit_chars = sum(1 for c in text if c.isascii() and c.isdigit())
        meaningful_chars = chinese_chars + english_chars + digit_chars
        total_chars = len(text)

        if total_chars > 0 and meaningful_chars / total_chars < 0.3:
            logger.debug(
                "Validation rejected: low meaningful char ratio (%.1f%%), "
                "zh=%d en=%d digits=%d total=%d, text=%s",
                meaningful_chars / total_chars * 100,
                chinese_chars, english_chars, digit_chars, total_chars,
                text[:80],
            )
            return None

        # 校验3: 长度一致性
        duration_seconds = len(audio_bytes) / (16000 * 2)
        word_count = len(text)
        if duration_seconds > 0 and word_count / duration_seconds > 20:
            logger.debug(
                "Validation rejected: too many chars (%.1f chars/s, dur=%.1fs, len=%d)",
                word_count / duration_seconds, duration_seconds, word_count,
            )
            return None

        return text

    # 文件级转写常量
    MAX_WHOLE_FILE_BYTES = 25 * 1024 * 1024  # 25MB - 整段发送上限
    WINDOW_DURATION = 20.0   # 滑动窗口 20s
    WINDOW_STEP = 10.0       # 步长 10s (50% 重叠)

    async def transcribe_file(self, file_path: str) -> list[dict]:
        """离线转写完整音频文件

        短文件（< 25MB）：整段发送后按句子拆分
        长文件（>= 25MB）：重叠滑动窗口分段发送

        Returns:
            list of {start_time, end_time, text}
        """
        import wave

        file_size = os.path.getsize(file_path)
        logger.info(
            "transcribe_file: %s (%.1f MB), mode=%s",
            file_path, file_size / (1024 * 1024),
            "whole" if file_size < self.MAX_WHOLE_FILE_BYTES else "sliding",
        )

        with wave.open(file_path, 'rb') as wf:
            total_frames = wf.readframes(wf.getnframes())
            sample_rate = wf.getframerate()
            bytes_per_sec = sample_rate * 2  # 16-bit mono

        if file_size < self.MAX_WHOLE_FILE_BYTES:
            return await self._transcribe_whole(total_frames, bytes_per_sec)
        else:
            return await self._transcribe_sliding(total_frames, bytes_per_sec)

    async def _transcribe_whole(self, audio_bytes: bytes, bytes_per_sec: int) -> list[dict]:
        """整段转写后按句子拆分"""
        text = await self.transcribe_with_validation(audio_bytes)
        if not text:
            return []

        total_duration = len(audio_bytes) / bytes_per_sec
        sentences = self._split_sentences(text)
        if not sentences:
            return []

        # 按比例分配时间戳
        results = []
        total_chars = sum(len(s) for s in sentences)
        char_pos = 0
        for sentence in sentences:
            if total_chars == 0:
                break
            ratio = len(sentence) / total_chars
            seg_duration = total_duration * ratio
            start = char_pos / max(total_chars, 1) * total_duration
            end = start + seg_duration
            results.append({
                "start_time": round(start, 2),
                "end_time": round(end, 2),
                "text": sentence.strip(),
            })
            char_pos += len(sentence)

        logger.info("Whole-file: %d sentences from %.1fs audio", len(results), total_duration)
        return results

    async def _transcribe_sliding(self, audio_bytes: bytes, bytes_per_sec: int) -> list[dict]:
        """重叠滑动窗口转写"""
        window_bytes = int(self.WINDOW_DURATION * bytes_per_sec)
        step_bytes = int(self.WINDOW_STEP * bytes_per_sec)
        total_duration = len(audio_bytes) / bytes_per_sec
        total_windows = max(1, (len(audio_bytes) - window_bytes) // step_bytes + 1)

        results = []
        for i in range(total_windows):
            start_byte = i * step_bytes
            end_byte = min(start_byte + window_bytes, len(audio_bytes))
            chunk = audio_bytes[start_byte:end_byte]

            # 最后一块太小时跳过
            if len(chunk) < step_bytes and i > 0:
                break

            start_time = start_byte / bytes_per_sec
            end_time = end_byte / bytes_per_sec

            text = await self.transcribe_with_validation(chunk)
            if text:
                results.append({
                    "start_time": round(start_time, 2),
                    "end_time": round(end_time, 2),
                    "text": text.strip(),
                })
                logger.debug(
                    "Sliding window %d/%d: %.1f-%.1fs, %d chars",
                    i + 1, total_windows, start_time, end_time, len(text),
                )

            # 限速：避免 API 限流
            if i < total_windows - 1:
                await asyncio.sleep(0.5)

        logger.info(
            "Sliding-window: %d/%d windows produced text, %.1fs total",
            len(results), total_windows, total_duration,
        )
        return results

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """按标点符号拆分句子"""
        parts = re.split(r'(?<=[。！？\n])\s*', text)
        return [p.strip() for p in parts if p.strip()]
