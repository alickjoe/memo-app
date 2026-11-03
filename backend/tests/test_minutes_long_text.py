"""
长文本分段摘要 + 单次直连兜底测试（v1.3.5 / v1.3.6）

修复背景：分段调用 max_tokens=500 对推理型模型不够 → content 为空 → 全部分段失败，
长会议纪要生成反复报 "LLM produced no segment summaries for long transcript"。
v1.3.6 追加：单段推理超预算时加倍预算重试一次，只有连续多段救不回来才降级，
避免「个别段落抖动 → 整场会议退化成首尾截断」丢内容。
"""
from llm.summarizer import (
    CHUNK_SUMMARY_MAX_TOKENS,
    CHUNK_SUMMARY_RETRY_MAX_TOKENS,
    TITLE_MAX_TOKENS,
    LLMSummarizer,
)


def _long_text(lines: int = 400) -> str:
    return "\n".join(f"第{i}段：" + "内容内容内容" * 8 for i in range(lines))


async def test_chunk_max_tokens_raised():
    # 500 时代推理型模型分段必然空 content（实测 glm-5.3-flash）
    assert CHUNK_SUMMARY_MAX_TOKENS >= 1500
    # 重试预算必须高于基础预算，否则重试无意义
    assert CHUNK_SUMMARY_RETRY_MAX_TOKENS > CHUNK_SUMMARY_MAX_TOKENS
    # 标题生成同样被 reasoning 吃光预算（finish_reason=length → Untitled Meeting）
    assert TITLE_MAX_TOKENS >= 1500


def test_split_chunks_respects_line_boundaries():
    text = _long_text()
    chunks = LLMSummarizer._split_chunks(text)
    assert 1 < len(chunks) <= 8
    assert all(len(c) <= 6000 for c in chunks)
    # 按行边界切分不丢内容（未触发 8 段截断时拼接等于原文）
    assert "".join(chunks) == text
    # 除最后一段外都应以换行结尾（没有腰斩行）
    assert all(c.endswith("\n") for c in chunks[:-1])


def test_split_chunks_hard_splits_single_long_line():
    text = "a" * 20000  # 无换行
    chunks = LLMSummarizer._split_chunks(text)
    assert all(len(c) <= 6000 for c in chunks)
    assert "".join(chunks) == text


def test_split_chunks_caps_at_eight_chunks():
    text = _long_text(lines=2000)  # 远超 8 段
    chunks = LLMSummarizer._split_chunks(text)
    assert len(chunks) == 8


def test_truncate_for_single_call_keeps_head_and_tail():
    text = "H" * 8000 + "M" * 20000 + "T" * 3000
    truncated = LLMSummarizer._truncate_for_single_call(text)
    assert truncated.startswith("H" * 100)
    assert truncated.endswith("T" * 100)
    assert "已省略" in truncated
    assert len(truncated) < len(text)
    # 短文本原样返回
    assert LLMSummarizer._truncate_for_single_call("short") == "short"


async def test_all_chunks_failing_falls_back_to_single_pass(monkeypatch):
    """全部分段失败 → 降级为截断转写单次直连生成（不再直接报错）"""
    s = LLMSummarizer()
    s.api_key = "test-key"
    s.output_language = "zh"

    async def no_chunk(_prompt):
        return None, "empty"

    calls: list[str] = []

    async def fake_call_llm(prompt):
        calls.append(prompt)
        return {
            "summary": "ok",
            "key_points": [],
            "action_items": [],
            "next_steps": "",
            "raw_response": "",
        }

    monkeypatch.setattr(s, "_call_llm_for_chunk", no_chunk)
    monkeypatch.setattr(s, "_call_llm", fake_call_llm)

    result = await s._summarize_long_text(_long_text())
    assert result["summary"] == "ok"
    assert len(calls) == 1
    # 兜底走的是「截断转写单次直连」（_call_llm 收到的就是截断后的原文），
    # 而不是摘要汇总 prompt
    assert "已省略" in calls[0]
    assert "摘要汇总" not in calls[0]


async def test_partial_chunk_failure_still_consolidates(monkeypatch):
    """部分分段失败 → 用成功的摘要继续整合，不触发单次兜底"""
    s = LLMSummarizer()
    s.api_key = "test-key"
    s.output_language = "zh"

    async def flaky_chunk(prompt):
        return (None, "empty") if "第 1 段" in prompt else ("片段摘要", None)

    calls: list[str] = []

    async def fake_call_llm(prompt):
        calls.append(prompt)
        return {
            "summary": "ok",
            "key_points": [],
            "action_items": [],
            "next_steps": "",
            "raw_response": "",
        }

    monkeypatch.setattr(s, "_call_llm_for_chunk", flaky_chunk)
    monkeypatch.setattr(s, "_call_llm", fake_call_llm)

    result = await s._summarize_long_text(_long_text())
    assert result["summary"] == "ok"
    assert len(calls) == 1
    # 走的是摘要汇总路径，且包含成功片段的摘要
    assert "摘要汇总" in calls[0]
    assert "片段摘要" in calls[0]


async def test_reasoning_budget_retries_with_larger_budget(monkeypatch):
    """单段推理超预算 → 用更大预算重试一次成功，不走截断兜底（v1.3.6 关键行为）"""
    s = LLMSummarizer()
    s.api_key = "test-key"
    s.output_language = "zh"

    budgets: list[int] = []

    async def flaky_budget(_prompt, max_tokens=None):
        effective = max_tokens or CHUNK_SUMMARY_MAX_TOKENS
        budgets.append(effective)
        if effective == CHUNK_SUMMARY_MAX_TOKENS:
            return None, "reasoning_budget"
        return "片段摘要", None

    calls: list[str] = []

    async def fake_call_llm(prompt):
        calls.append(prompt)
        return {
            "summary": "ok",
            "key_points": [],
            "action_items": [],
            "next_steps": "",
            "raw_response": "",
        }

    monkeypatch.setattr(s, "_call_llm_for_chunk", flaky_budget)
    monkeypatch.setattr(s, "_call_llm", fake_call_llm)

    result = await s._summarize_long_text(_long_text())
    assert result["summary"] == "ok"
    # 每段都是「基础预算失败 → 大预算成功」，且没有退化成截断兜底
    assert budgets[0] == CHUNK_SUMMARY_MAX_TOKENS
    assert budgets[1] == CHUNK_SUMMARY_RETRY_MAX_TOKENS
    assert "摘要汇总" in calls[0]
    assert "已省略" not in calls[0]


async def test_reasoning_budget_aborts_after_consecutive_failures(monkeypatch):
    """连续两段即使加倍预算也失败 → 判定系统性预算不足，放弃分段转单次兜底"""
    s = LLMSummarizer()
    s.api_key = "test-key"
    s.output_language = "zh"

    chunk_calls: list[int] = []

    async def reasoning_exhausted(_prompt, max_tokens=None):
        chunk_calls.append(max_tokens or CHUNK_SUMMARY_MAX_TOKENS)
        return None, "reasoning_budget"

    calls: list[str] = []

    async def fake_call_llm(prompt):
        calls.append(prompt)
        return {
            "summary": "ok",
            "key_points": [],
            "action_items": [],
            "next_steps": "",
            "raw_response": "",
        }

    monkeypatch.setattr(s, "_call_llm_for_chunk", reasoning_exhausted)
    monkeypatch.setattr(s, "_call_llm", fake_call_llm)

    result = await s._summarize_long_text(_long_text())
    assert result["summary"] == "ok"
    # 第 1 段：基础 + 重试；第 2 段：基础 + 重试 → 共 4 次后放弃，不再跑第 3 段
    assert chunk_calls == [
        CHUNK_SUMMARY_MAX_TOKENS,
        CHUNK_SUMMARY_RETRY_MAX_TOKENS,
        CHUNK_SUMMARY_MAX_TOKENS,
        CHUNK_SUMMARY_RETRY_MAX_TOKENS,
    ]
    assert len(calls) == 1
    # 兜底路径：截断转写单次直连
    assert "已省略" in calls[0]
