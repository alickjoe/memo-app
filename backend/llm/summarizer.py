"""
LLM 纪要生成模块 - 使用 OpenAI/DeepSeek API 生成会议纪要
"""
import os
import json
import logging
import asyncio
from typing import Optional

import httpx
import urllib3

from llm.prompts import DEFAULT_SYSTEM_PROMPT, EN_SYSTEM_PROMPT

# 禁用 SSL 验证警告（企业网络环境）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("memo.llm")


class LLMSummarizer:
    """云端 LLM 纪要生成器"""

    def __init__(self):
        self.api_key: Optional[str] = None
        self.base_url: str = "https://api.openai.com/v1"
        self.model: str = "gpt-4o-mini"
        self.output_language: str = "en"
        self.call_count: int = 0
        self.estimated_cost: float = 0.0

    async def reload_config(self):
        """重新加载配置"""
        from storage.db import get_db
        db = await get_db()
        cursor = await db.execute("SELECT key, value FROM settings")
        rows = await cursor.fetchall()
        settings = {row[0]: row[1] for row in rows}

        # LLM 专用配置，优先使用 llm_api_key/llm_api_base_url，回退到通用 api_key/api_base_url
        self.api_key = settings.get("llm_api_key") or settings.get("api_key", os.environ.get("MEMO_API_KEY", ""))
        self.base_url = settings.get("llm_api_base_url") or settings.get("api_base_url", os.environ.get("MEMO_API_BASE_URL", "https://api.openai.com/v1"))
        self.model = settings.get("llm_model", "gpt-4o-mini")
        self.output_language = settings.get("llm_output_language", "en")

    async def _ensure_config(self):
        """确保配置已加载"""
        if not self.api_key:
            await self.reload_config()

    async def summarize(self, transcript_text: str) -> dict:
        """生成会议纪要"""
        await self._ensure_config()

        if not self.api_key:
            logger.error("No API key configured for LLM")
            return self._fallback_response()

        try:
            # 长文本分段处理
            if len(transcript_text) > 8000:
                return await self._summarize_long_text(transcript_text)

            return await self._call_llm(transcript_text)

        except Exception as e:
            logger.error(f"LLM error: {e}")
            return self._fallback_response()

    async def _call_llm(self, text: str) -> dict:
        """调用 LLM API"""
        async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": self._get_system_prompt()},
                        {"role": "user", "content": self._get_user_prompt(text)},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
            )

            if response.status_code == 200:
                self.call_count += 1
                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # 估算费用
                usage = data.get("usage", {})
                if usage:
                    # GPT-4o-mini: $0.15/1M input, $0.60/1M output
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    self.estimated_cost += (
                        prompt_tokens * 0.15 / 1_000_000 + completion_tokens * 0.60 / 1_000_000
                    )

                # 解析 JSON 响应
                return self._parse_response(content)
            else:
                logger.error(f"LLM API error: {response.status_code} - {response.text}")
                return self._fallback_response()

    async def _summarize_long_text(self, text: str) -> dict:
        """超长文本分段处理"""
        # 分段：每段约 6000 字符
        chunk_size = 6000
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

        # 限制最多处理 8 段
        if len(chunks) > 8:
            chunks = chunks[:8]

        is_en = self.output_language == "en"

        # 逐段摘要
        summaries = []
        for i, chunk in enumerate(chunks):
            if is_en:
                prompt = f"""Please generate a brief summary for this meeting transcript segment (segment {i+1}/{len(chunks)}):

{chunk}

Summarize the key points of this segment in 2-3 sentences."""
            else:
                prompt = f"""请为以下会议转写片段生成简要摘要（这是第 {i+1}/{len(chunks)} 段）：

{chunk}

请用 2-3 句话概括本片段的要点。"""
            result = await self._call_llm_for_chunk(prompt)
            if result:
                summaries.append(result)

        # 汇总所有摘要
        if summaries:
            combined = "\n\n---\n\n".join(summaries)
            if is_en:
                final_prompt = f"""Below are summaries of each segment of the meeting. Please consolidate them into a complete meeting minutes:

{combined}"""
            else:
                final_prompt = f"""以下是会议各片段的摘要汇总，请整合为完整的会议纪要：

{combined}"""
            return await self._call_llm(final_prompt)

        return self._fallback_response()

    def _get_system_prompt(self) -> str:
        """根据输出语言选择对应的 system prompt"""
        if self.output_language == "en":
            return EN_SYSTEM_PROMPT
        return DEFAULT_SYSTEM_PROMPT

    def _get_user_prompt(self, text: str) -> str:
        """根据输出语言生成对应的 user prompt"""
        if self.output_language == "en":
            return f"Please generate minutes for the following meeting:\n\n{text}"
        return f"请为以下会议生成纪要：\n\n{text}"

    async def _call_llm_for_chunk(self, prompt: str) -> Optional[str]:
        """针对单个片段调用 LLM"""
        try:
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 500,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Chunk LLM error: {e}")
        return None

    def _parse_response(self, content: str) -> dict:
        """解析 LLM 响应为结构化数据"""
        try:
            # 尝试提取 JSON
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    "summary": data.get("summary", ""),
                    "key_points": data.get("key_points", []),
                    "action_items": data.get("action_items", []),
                    "next_steps": data.get("next_steps", ""),
                    "raw_response": content,
                }
        except (json.JSONDecodeError, KeyError):
            pass

        # 回退：返回原始文本作为摘要
        return {
            "summary": content[:500],
            "key_points": [],
            "action_items": [],
            "next_steps": "",
            "raw_response": content,
        }

    def _fallback_response(self) -> dict:
        """LLM 不可用时的回退响应"""
        return {
            "summary": "纪要生成失败，请检查 API 配置后重试。",
            "key_points": [],
            "action_items": [],
            "next_steps": "",
            "raw_response": "",
        }
