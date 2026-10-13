# Spec：纪要 JSON 解析健壮化（v1.3.4）

状态：已确认（2026-10-13，用户选定"完整方案"+ 发 v1.3.4）

## 现象

长会议（如 208 段转写）生成纪要后，"摘要"章节直接显示 ```` ```json ```` 原文，
关键讨论点 / 行动项 / 下一步全空；短会议一切正常。

## 根因

| # | 问题 | 位置 |
|---|------|------|
| 1 | 纪要生成 `max_tokens=2000` 对长会议整合纪要不够 → 输出被截断（finish_reason=length）→ JSON 不完整 | `backend/llm/summarizer.py` `_call_llm` |
| 2 | 解析失败**静默回退**：把原始响应前 500 字塞进 summary，其余字段全空 → 前端呈现"原始 JSON + 空章节" | `_parse_response` 回退分支 |
| 3 | `summarize()` 把一切异常（网络错 / 无 API key）吞成 `_fallback_response()` 固定文案 → `generate_minutes` 拿到"成功"结果 → 会议错误地落 `done` + 假纪要，不走 v1.3.3 的失败横幅 | `summarize()` try/except |

短会议 JSON 在 2000 token 内 → 解析正常，解释了"有的会议没问题"。

## 修复方案（后端 only，前端零改动）

1. **防截断**：纪要生成 `max_tokens` 提为模块常量 `MINUTES_MAX_TOKENS = 4000`
   （兼容主流模型输出上限），并读取 `finish_reason`，`length` 时记 warning 日志。
2. **截断抢救解析** `_salvage_truncated_json`：栈式扫描闭合未完的字符串 / 括号，
   失败则回退到上一个逗号边界重试（最多 8 轮），尽力恢复已生成的
   summary / key_points 部分字段——截断不再等于"全空 + 原始 JSON"。
3. **解析失败改报错**：抢救也失败 → `raise ValueError(原始响应前 200 字)` →
   `generate_minutes` 既有 except 路径 → `status=error` + `error_message` +
   前端红色横幅 + 重新生成按钮（v1.3.3 基建直接复用）。
4. **LLM 异常不再伪装 done**：`summarize()` 网络 / 配置异常直接向上抛
   （删除 `_fallback_response`），统一走 error 横幅语义。

## 测试

`backend/tests/test_minutes_parse.py`：

- `_parse_response`：裸 JSON / ```json 围栏 / 前后带废话 → 正常解析
- 截断在字符串值中间 → 抢救出 summary + key_points + 部分字段
- 截断在数组开头（`"action_items": [`）→ 抢救出已有字段、缺失字段为空
- 完全无法解析（无 JSON / 伪 JSON）→ raise
- `summarize()` 无 API key → raise；解析错误向上传播
- `MINUTES_MAX_TOKENS >= 4000` 断言

回归：pytest 全套、vitest 47（前端零改动）、`npm run precommit`。
