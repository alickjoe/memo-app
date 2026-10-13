"""
纪要 JSON 解析健壮化测试（v1.3.4）：截断抢救解析、解析失败报错、LLM 异常不再伪装 done
"""
import json

import pytest
from llm.summarizer import MINUTES_MAX_TOKENS, LLMSummarizer


def _minutes_json() -> str:
    data = {
        "summary": "讨论了发布计划调整与 AI 生成文档 POC 可行性。",
        "key_points": ["发布计划调整到 45 周", "AI 生成文档 POC 可行"],
        "action_items": ["张三：确认部署窗口"],
        "next_steps": "下周同步具体时间",
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def test_minutes_max_tokens_raised():
    # 2000 时代长会议纪要被截断 → 至少提到 4000
    assert MINUTES_MAX_TOKENS >= 4000


def test_parse_plain_json():
    result = LLMSummarizer()._parse_response(_minutes_json())
    assert result["summary"].startswith("讨论了")
    assert result["key_points"] == ["发布计划调整到 45 周", "AI 生成文档 POC 可行"]
    assert result["action_items"] == ["张三：确认部署窗口"]
    assert result["next_steps"] == "下周同步具体时间"


def test_parse_fenced_json():
    fenced = "```json\n" + _minutes_json() + "\n```"
    result = LLMSummarizer()._parse_response(fenced)
    assert result["key_points"] == ["发布计划调整到 45 周", "AI 生成文档 POC 可行"]


def test_parse_json_with_surrounding_text():
    wrapped = "好的，以下是会议纪要：\n" + _minutes_json() + "\n希望对你有帮助。"
    result = LLMSummarizer()._parse_response(wrapped)
    assert result["summary"].startswith("讨论了")


def test_parse_null_list_fields_normalized():
    data = {"summary": "s", "key_points": None, "action_items": None, "next_steps": None}
    result = LLMSummarizer()._parse_response(json.dumps(data, ensure_ascii=False))
    assert result["key_points"] == []
    assert result["action_items"] == []
    assert result["next_steps"] == ""


def test_parse_truncated_mid_string_salvages_generated_fields():
    """max_tokens 截断在字符串值中间 → 抢救出 summary / key_points，已生成部分不丢"""
    full = "```json\n" + _minutes_json() + "\n```"
    cut = full.index('"张三')
    truncated = full[: cut + 5]  # "张三：
    result = LLMSummarizer()._parse_response(truncated)
    assert result["summary"].startswith("讨论了")
    assert result["key_points"] == ["发布计划调整到 45 周", "AI 生成文档 POC 可行"]
    assert isinstance(result["action_items"], list)


def test_parse_truncated_after_array_open_salvages_with_empty_field():
    """截断在 "action_items": [ 之后（无收尾大括号）→ 缺失字段为空、已有字段完整"""
    full = "```json\n" + _minutes_json() + "\n```"
    cut = full.index('"action_items": [') + len('"action_items": [')
    truncated = full[:cut]
    result = LLMSummarizer()._parse_response(truncated)
    assert result["summary"].startswith("讨论了")
    assert result["key_points"] == ["发布计划调整到 45 周", "AI 生成文档 POC 可行"]
    assert result["action_items"] == []
    assert result["next_steps"] == ""


def test_salvage_trailing_comma_close():
    """截断在元素逗号后 → 闭合时剥掉尾逗号"""
    truncated = '{"summary": "s", "key_points": ["a", "b",'
    salvaged = LLMSummarizer._salvage_truncated_json(truncated)
    assert salvaged is not None
    data = json.loads(salvaged)
    assert data["key_points"] == ["a", "b"]


def test_parse_unparseable_raises():
    with pytest.raises(ValueError):
        LLMSummarizer()._parse_response("抱歉，我无法根据这段内容生成会议纪要。")


def test_parse_pseudo_json_raises():
    with pytest.raises(ValueError):
        LLMSummarizer()._parse_response("{ summary: 没有引号的伪 JSON }")


async def test_summarize_raises_without_api_key(monkeypatch):
    s = LLMSummarizer()
    s.api_key = ""

    async def fake_reload():
        pass

    monkeypatch.setattr(s, "reload_config", fake_reload)
    with pytest.raises(ValueError):
        await s.summarize("transcript text")


async def test_summarize_propagates_parse_error(monkeypatch):
    """解析失败必须向上抛（generate_minutes 落库 error），不能再吞成回退文案"""
    s = LLMSummarizer()
    s.api_key = "test-key"

    async def fake_call_llm(text):
        return s._parse_response("这不是 JSON 输出")

    monkeypatch.setattr(s, "_call_llm", fake_call_llm)
    with pytest.raises(ValueError):
        await s.summarize("transcript text")
