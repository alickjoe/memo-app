# Spec：长会议纪要生成分段失败修复（v1.3.5）

状态：已确认（2026-11-03，用户选定"完整方案"+ 发 v1.3.5）

## 现象

长会议（如 286 段 / 25 分钟）生成纪要**反复**失败，红色横幅：

```
会议纪要生成失败：LLM produced no segment summaries for long transcript
```

## 根因

转写 >8000 字符走 `_summarize_long_text`：按 6000 字符切段、逐段调 LLM 生成摘要
（`max_tokens=500`、单条 user 消息、无 system prompt），**全部分段都拿不到内容**时抛错。

分段拿不到内容的候选原因（按可能性）：

1. **推理型模型 / 网关的 reasoning token 吃光 500 预算** → HTTP 200 但
   `message.content = null`（finish_reason=length）。代码里原有防御注释
   "OpenRouter 偶发返回 HTTP 200 但 content 为 None"，主路径 `max_tokens=4000`
   所以短会议正常、分段仅 500 → 长会议必翻车。
2. 网关返回 200 但 content 为空字符串（同样被判失败）。
3. 分段请求非 200（限流/参数）——旧代码非 200 静默 `return None`，**无任何日志**，
   无法从日志确认。

## 修复方案（后端 only）

1. **预算**：新增 `CHUNK_SUMMARY_MAX_TOKENS = 1200`（分段摘要上限，原 500）
2. **诊断日志**：`_call_llm_for_chunk` 非 200 记录 status + body 片段；content 为空
   记录 finish_reason 并提示推理型模型可能耗尽预算
3. **降级兜底**：全部分段失败时**不再直接抛错**，改为「截断转写后单次直连生成」
   （`_truncate_for_single_call`：头 8000 + 尾 3000 字符，中间标注省略）；兜底
   仍失败才抛错
4. **切分改进**：`_split_chunks` 按转写行边界切分（不再腰斩句子/发言），超长单行
   兜底硬切；>8 段时告警并截断（受模型上下文限制）
5. 部分分段成功时沿用成功摘要继续整合（已有行为，补日志）

## 测试

`backend/tests/test_minutes_long_text.py`（7 例）：

- 分段预算 ≥1000 断言；分段按行边界拼接等于原文、每段 ≤6000、最多 8 段
- 超长无换行文本硬切；`_truncate_for_single_call` 头尾保留 + 省略标记
- 全部分段失败 → 单次直连兜底被调用（截断标记存在、不走摘要汇总）
- 部分分段失败 → 用成功摘要整合

回归：pytest 91、ruff、`npm run precommit`。
