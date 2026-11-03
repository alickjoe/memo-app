"""
长文本分段摘要 + 单次直连兜底测试（v1.3.5）

修复背景：分段调用 max_tokens=500 对推理型模型不够 → content 为空 → 全部分段失败，
长会议纪要生成反复报 "LLM produced no segment summaries for long transcript"。
"""
from llm.summarizer import CHUNK_SUMMARY_MAX_TOKENS, LLMSummarizer


def _long_text(lines: int = 400) -> str:
    return "\n".join(f"第{i}段：" + "内容内容内容" * 8 for i in range(lines))


async def test_chunk_max_tokens_raised():
    # 500 时代推理型模型分段必然空 content
    assert CHUNK_SUMMARY_MAX_TOKENS >= 1000


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
        return None

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
        return None if "第 1 段" in prompt else "片段摘要"

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
