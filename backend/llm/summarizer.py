"""
LLM 纪要生成模块 - 使用 OpenAI/DeepSeek API 生成会议纪要
"""
import json
import logging
import os
import re

import httpx
import urllib3

from llm.prompts import (
    DEFAULT_SYSTEM_PROMPT,
    EN_SYSTEM_PROMPT,
    EN_TITLE_GENERATION_PROMPT,
    TITLE_GENERATION_PROMPT,
)

# 禁用 SSL 验证警告（企业网络环境）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("memo.llm")

# 纪要生成输出 token 上限：2000 时长会议的整合纪要 JSON 会被截断成非法 JSON（v1.3.4 修复）
MINUTES_MAX_TOKENS = 4000

# 分段摘要输出上限：500 对推理型模型不够（reasoning 吃光预算 → content 为空），
# 导致长会议全部分段失败（v1.3.5 修复；glm-5.3-flash 经 OpenRouter 实测 500 必空）
CHUNK_SUMMARY_MAX_TOKENS = 2000

# 分段摘要重试上限：推理型模型的 reasoning token 数逐段波动（实测同一会议 75~910），
# 个别段落会超出 2000。单段超预算时用该上限重试一次，避免整场会议退化成截断兜底
# （截断只保留首尾约 1/3 内容，长会议会丢大量议题）
CHUNK_SUMMARY_RETRY_MAX_TOKENS = 4000

# 标题生成输出上限：同样会被 reasoning 吃光（500 → content 空、finish_reason=length），
# 表现是会议标题一直是 Untitled Meeting（v1.3.5 修复）
TITLE_MAX_TOKENS = 2000


class LLMSummarizer:
    """云端 LLM 纪要生成器"""

    def __init__(self):
        self.api_key: str | None = None
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
        """生成会议纪要。

        失败一律抛错：由 generate_minutes 统一落库 status=error + error_message，
        前端展示失败横幅并提供"重新生成"。不再静默回退成假纪要——旧回退会让会议
        错误地标记为 done，摘要显示英文占位文案。
        """
        await self._ensure_config()

        if not self.api_key:
            logger.error("No API key configured for LLM")
            raise ValueError("LLM API key not configured")

        # 长文本分段处理
        if len(transcript_text) > 8000:
            return await self._summarize_long_text(transcript_text)

        return await self._call_llm(transcript_text)

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
                    "max_tokens": MINUTES_MAX_TOKENS,
                },
            )

            if response.status_code == 200:
                self.call_count += 1
                data = response.json()
                try:
                    choice = data["choices"][0]
                    content = choice.get("message", {}).get("content")
                    finish_reason = choice.get("finish_reason")
                except (KeyError, IndexError, TypeError):
                    raise ValueError(f"LLM returned unexpected response structure: {str(data)[:200]}")

                # 防御：OpenRouter 偶发返回 HTTP 200 但 content 为 None
                if not content:
                    raise ValueError("LLM returned empty content")

                # 截断检测：输出被 API 掐断，JSON 大概率不完整（交给抢救解析兜底）
                if finish_reason == "length":
                    logger.warning(
                        "LLM minutes output hit max_tokens=%s (finish_reason=length), salvaging partial JSON",
                        MINUTES_MAX_TOKENS,
                    )

                # 估算费用
                usage = data.get("usage", {})
                if usage:
                    # GPT-4o-mini: $0.15/1M input, $0.60/1M output
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    self.estimated_cost += (
                        prompt_tokens * 0.15 / 1_000_000 + completion_tokens * 0.60 / 1_000_000
                    )

                # 解析 JSON 响应（解析失败抛错，不再静默回退成假纪要）
                return self._parse_response(content)
            else:
                logger.error(f"LLM API error: {response.status_code} - {response.text[:300]}")
                raise ValueError(f"LLM API error {response.status_code}: {response.text[:200]}")

    async def _summarize_long_text(self, text: str) -> dict:
        """超长文本分段处理：逐段摘要后整合；全部分段失败时降级为单次直连生成"""
        chunks = self._split_chunks(text)
        is_en = self.output_language == "en"

        # 逐段摘要。个别分段推理超预算时先加预算重试一次，
        # 只有「连续多段都救不回来」才放弃分段（说明是系统性预算不足，重试无意义）
        summaries: list[str] = []
        failed_chunks = 0
        consecutive_failures = 0
        for i, chunk in enumerate(chunks):
            if is_en:
                prompt = f"""Please generate a brief summary for this meeting transcript segment (segment {i+1}/{len(chunks)}):

{chunk}

Summarize the key points of this segment in 2-3 sentences."""
            else:
                prompt = f"""请为以下会议转写片段生成简要摘要（这是第 {i+1}/{len(chunks)} 段）：

{chunk}

请用 2-3 句话概括本片段的要点。"""
            result, failure = await self._call_llm_for_chunk(prompt)
            if not result and failure == "reasoning_budget":
                logger.warning(
                    "Chunk %s/%s exhausted max_tokens=%s on reasoning; retrying at max_tokens=%s",
                    i + 1,
                    len(chunks),
                    CHUNK_SUMMARY_MAX_TOKENS,
                    CHUNK_SUMMARY_RETRY_MAX_TOKENS,
                )
                result, failure = await self._call_llm_for_chunk(
                    prompt, CHUNK_SUMMARY_RETRY_MAX_TOKENS
                )
                if result:
                    logger.info(
                        "Chunk %s/%s recovered on retry (max_tokens=%s)",
                        i + 1,
                        len(chunks),
                        CHUNK_SUMMARY_RETRY_MAX_TOKENS,
                    )
            if result:
                summaries.append(result)
                consecutive_failures = 0
                continue

            failed_chunks += 1
            consecutive_failures += 1
            logger.warning("Chunk %s/%s summary failed (%s), continuing", i + 1, len(chunks), failure)
            if consecutive_failures >= 2:
                logger.warning(
                    "%s consecutive chunks failed even after budget retry; abandoning chunking "
                    "for single-pass fallback at chunk %s/%s",
                    consecutive_failures,
                    i + 1,
                    len(chunks),
                )
                break

        # 用已成功的摘要整合（部分分段失败时仍能覆盖大部分会议内容，优于截断兜底）
        if summaries:
            if failed_chunks:
                logger.warning(
                    "Consolidating %s/%s chunk summaries (%s chunks failed); coverage may be partial",
                    len(summaries),
                    len(chunks),
                    failed_chunks,
                )
            combined = "\n\n---\n\n".join(summaries)
            if is_en:
                final_prompt = f"""Below are summaries of each segment of the meeting. Please consolidate them into a complete meeting minutes:

{combined}"""
            else:
                final_prompt = f"""以下是会议各片段的摘要汇总，请整合为完整的会议纪要：

{combined}"""
            return await self._call_llm(final_prompt)

        # 全部分段失败：降级为「截断转写后单次直连生成」，避免纪要直接失败
        logger.warning(
            "Falling back to single-pass generation on truncated transcript (%s chunks, %s summaries)",
            len(chunks),
            len(summaries),
        )
        return await self._call_llm(self._truncate_for_single_call(text))

    @staticmethod
    def _split_chunks(text: str, chunk_size: int = 6000, max_chunks: int = 8) -> list[str]:
        """按行边界切分转写，避免把句子/发言腰斩；超长单行兜底硬切。

        超过 max_chunks 的尾部会丢弃并告警（受模型上下文与耗时限制）。
        """
        chunks: list[str] = []
        current = ""
        for line in text.splitlines(keepends=True):
            if current and len(current) + len(line) > chunk_size:
                chunks.append(current)
                current = line
            else:
                current += line
        if current:
            chunks.append(current)

        # 超长单行（或极长无换行文本）兜底硬切
        out: list[str] = []
        for chunk in chunks:
            if len(chunk) <= chunk_size:
                out.append(chunk)
            else:
                out.extend(chunk[i:i + chunk_size] for i in range(0, len(chunk), chunk_size))

        if len(out) > max_chunks:
            logger.warning(
                "Transcript too long: using first %s chunks, %s trailing chunks omitted",
                max_chunks,
                len(out) - max_chunks,
            )
            out = out[:max_chunks]
        return out

    @staticmethod
    def _truncate_for_single_call(text: str, head: int = 8000, tail: int = 3000) -> str:
        """单次直连兜底的转写截断：保留开头（会议背景/议程）与结尾（结论/行动项）"""
        if len(text) <= head + tail:
            return text
        omitted = len(text) - head - tail
        return f"{text[:head]}\n\n...（中间 {omitted} 字符已省略）...\n\n{text[-tail:]}"

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

    async def _call_llm_for_chunk(
        self, prompt: str, max_tokens: int | None = None
    ) -> tuple[str | None, str | None]:
        """针对单个片段调用 LLM。

        max_tokens 可覆盖，用于推理预算耗尽后的加预算重试；
        缺省值在调用时读取模块常量（不写成默认参数，避免定义时求值导致运行期改动无效）。

        返回 (content, failure_kind)：
        - 成功：(文本, None)
        - 推理预算耗尽（reasoning 吃光 max_tokens）：(None, 'reasoning_budget')
        - 其他失败：(None, 'http_error' | 'empty' | 'exception')
        """
        if max_tokens is None:
            max_tokens = CHUNK_SUMMARY_MAX_TOKENS
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
                        "max_tokens": max_tokens,
                    },
                )
                if response.status_code != 200:
                    logger.warning(
                        "Chunk LLM API error: %s - %s", response.status_code, response.text[:300]
                    )
                    return None, "http_error"
                data = response.json()
                try:
                    choice = data["choices"][0]
                    content = choice.get("message", {}).get("content")
                    finish_reason = choice.get("finish_reason")
                except (KeyError, IndexError, TypeError):
                    logger.warning("Chunk LLM unexpected response structure: %s", str(data)[:200])
                    return None, "empty"
                if content:
                    return content, None
                if finish_reason == "length":
                    logger.warning(
                        "Chunk LLM returned empty content (finish_reason=length) — "
                        "推理型模型耗尽了 max_tokens=%s 预算",
                        max_tokens,
                    )
                    return None, "reasoning_budget"
                logger.warning("Chunk LLM returned empty content (finish_reason=%s)", finish_reason)
                return None, "empty"
        except Exception as e:
            logger.error(f"Chunk LLM error: {e}")
        return None, "exception"

    @staticmethod
    def _strip_trailing_commas(text: str) -> str:
        """移除尾随逗号（常见 LLM 输出问题）"""
        return re.sub(r',\s*([}\]])', r'\1', text)

    @staticmethod
    def _close_truncated_json(text: str) -> str:
        """闭合截断 JSON：栈式扫描，补齐未完的字符串引号与未关闭的括号"""
        stack: list[str] = []
        in_string = False
        escaped = False
        for ch in text:
            if in_string:
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in '{[':
                stack.append(ch)
            elif ch in '}]' and stack:
                stack.pop()

        out = text.rstrip()
        if in_string:
            # 字符串值被拦腰截断：去掉悬空转义符后补引号
            out = out.removesuffix('\\')
            out += '"'
        while stack:
            out += '}' if stack.pop() == '{' else ']'
        return out

    @classmethod
    def _salvage_truncated_json(cls, json_str: str) -> str | None:
        """截断 JSON 抢救：闭合结构后尝试解析，失败则回退到上一个逗号边界重试。

        尽力恢复已生成的字段（如 summary / 部分 key_points）；完全无法闭合时返回 None。
        """
        text = json_str
        for _ in range(8):
            closed = cls._close_truncated_json(text)
            stripped = cls._strip_trailing_commas(closed)
            try:
                json.loads(stripped)
                return stripped
            except (json.JSONDecodeError, ValueError):
                idx = text.rfind(',')
                if idx <= 0:
                    return None
                text = text[:idx]
        return None

    def _parse_response(self, content: str) -> dict:
        """解析 LLM 响应为结构化数据。

        解析失败抛错（由 generate_minutes 落库 error + 前端失败横幅），
        不再把原始文本静默塞进 summary 伪装成功。
        """
        if not content or not isinstance(content, str):
            raise ValueError("LLM returned empty minutes content")

        # 剥离 markdown 代码块标记
        cleaned = content.strip()
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)

        # 提取 JSON 主体；无收尾大括号说明被截断，截取 { 起交给抢救解析
        json_match = re.search(r'\{[\s\S]*\}', cleaned)
        if json_match:
            json_str = json_match.group()
        else:
            brace = cleaned.find('{')
            json_str = cleaned[brace:] if brace != -1 else None

        last_error: Exception | None = None
        if json_str:
            candidates = [json_str]
            salvaged = self._salvage_truncated_json(json_str)
            if salvaged and salvaged not in candidates:
                candidates.append(salvaged)
            for candidate in candidates:
                try:
                    data = json.loads(self._strip_trailing_commas(candidate))
                except (json.JSONDecodeError, ValueError) as e:
                    last_error = e
                    continue
                return {
                    "summary": data.get("summary", "") or "",
                    "key_points": data.get("key_points", []) or [],
                    "action_items": data.get("action_items", []) or [],
                    "next_steps": data.get("next_steps", "") or "",
                    "raw_response": content,
                }
            logger.warning(f"JSON parse failed even after salvage: {last_error}")

        snippet = content.strip()[:200]
        raise ValueError(f"LLM minutes output is not parseable JSON (truncated or malformed): {snippet}")

    async def generate_title(self, summary: str) -> str | None:
        """根据摘要生成会议标题"""
        await self._ensure_config()
        if not self.api_key:
            return None

        try:
            if self.output_language == "en":
                user_prompt = EN_TITLE_GENERATION_PROMPT.format(summary=summary[:500])
            else:
                user_prompt = TITLE_GENERATION_PROMPT.format(summary=summary[:500])

            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": TITLE_MAX_TOKENS,
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    if not content:
                        logger.warning(f"Title generation returned empty content (finish_reason={data['choices'][0].get('finish_reason')})")
                        return None
                    title = content.strip()
                    # 清理可能的引号和多余空白
                    title = title.strip('"\'""\u201c\u201d').strip()
                    if title:
                        return title
                else:
                    logger.error(f"Title generation API error: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Title generation error: {e}")
        return None
