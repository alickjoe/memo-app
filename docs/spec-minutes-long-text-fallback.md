# Spec：长会议纪要生成分段失败修复（v1.3.5）

状态：已确认（2026-11-03，用户选定"完整方案"+ 发 v1.3.5）

## 现象

长会议（如 286 段 / 25 分钟）生成纪要**反复**失败，红色横幅：

```
会议纪要生成失败：LLM produced no segment summaries for long transcript
```

## 根因（已实证）

用户环境：OpenRouter + LLM 模型 `z-ai/glm-5.3-flash`（推理型），输出语言中文。

frontend.log 证据：

- `WARNING:memo.llm:Title generation returned empty content (finish_reason=length)`
  —— 标题生成（`max_tokens=500`）被 reasoning 吃光预算，返回空 content
- 同一会议 `360dba6c…` 每次 regenerate 都 `LLM produced no segment summaries for long transcript`
- 无任何 429/401/额度类错误 → 不是限流/网络问题

结论：推理型模型把 `max_tokens` 预算全部花在思维链上，500 token 时 content 为空
（finish_reason=length）；分段摘要与标题生成都是 500 → 必然失败，主路径 4000 则正常。

转写 >8000 字符走 `_summarize_long_text`：按 6000 字符切段、逐段调 LLM 生成摘要
（`max_tokens=500`、单条 user 消息、无 system prompt），**全部分段都拿不到内容**时抛错。

## 修复方案（后端 only）

1. **预算**：`CHUNK_SUMMARY_MAX_TOKENS = 2000`（原 500）、`TITLE_MAX_TOKENS = 2000`
   （标题生成原 500，修复 "Untitled Meeting"）
2. **诊断日志**：非 200 记录 status + body；content 为空记录 finish_reason 并区分
   「推理预算耗尽」
3. **早退 + 降级兜底**：首个分段因推理预算耗尽失败即放弃分段（其余只会同样失败且
   每次耗时数十秒），降级为「截断转写后单次直连生成」（头 8000 + 尾 3000 字符）；
   全部分段失败同样降级；兜底仍失败才抛错
4. **切分改进**：`_split_chunks` 按转写行边界切分（不腰斩句子/发言），超长单行兜底
   硬切；>8 段时告警并截断（受模型上下文限制）
5. 部分分段成功且未早退时，用成功摘要继续整合

## 测试

`backend/tests/test_minutes_long_text.py`（8 例）：

- 分段/标题预算断言；分段按行边界拼接等于原文、每段 ≤6000、最多 8 段
- 超长无换行文本硬切；`_truncate_for_single_call` 头尾保留 + 省略标记
- 全部分段失败 → 单次直连兜底（截断标记存在、不走摘要汇总）
- 推理预算耗尽 → 只调 1 段即早退 + 单次兜底
- 部分分段失败 → 用成功摘要整合

回归：pytest 92、ruff、`npm run precommit`。

---

# v1.3.6：分段重试（避免个别段落抖动丢掉大半个会议）

## 背景（v1.3.5 上线后复查发现的质量缺陷）

v1.3.5 的「首个分段推理超预算即放弃全部分段」是**错误的乐观假设**。真实 API 实测
（`z-ai/glm-5.3-flash`，33031 字符中文转写，6 段）各段 reasoning token 为
378 / 910 / 668 / 457 / 75 / 569，`completion_tokens` 峰值 1047（占 2000 的 52%）——
逐段波动极大。只要个别段落抖动超过 2000，v1.3.5 就会把整场会议降级成
「头 8000 + 尾 3000 字符」的截断兜底，25 分钟会议会丢失约 2/3 议题内容。
纪要虽然「不报错了」，但内容是残缺的，用户几乎无法察觉。

## 修复方案（后端 only）

1. **单段重试**：`_call_llm_for_chunk(prompt, max_tokens=...)` 支持覆盖预算；
   某段因 `finish_reason=length` 耗尽预算时，用
   `CHUNK_SUMMARY_RETRY_MAX_TOKENS = 4000` 对**同一段**重试一次
2. **不早退**：单段彻底失败只记录并继续下一段；仅当**连续 2 段**即使加倍预算也失败
   （判定为系统性预算不足，重试无意义）才放弃分段
3. **部分成功即整合**：只要有 ≥1 段摘要成功，就用它整合纪要（覆盖大部分会议），
   仅 0 段成功时才走截断单次兜底；部分失败时打 warning 说明覆盖率可能不完整
4. 常量注释记录实测依据，避免后人又把预算调小

## 测试（`backend/tests/test_minutes_long_text.py` 共 10 例）

- 分段基础预算失败 → 重试预算成功 → 走摘要汇总（`已省略` 不出现）
- 连续 2 段（各 2 次调用）均失败 → 第 3 段不再尝试、转截断兜底（共 4 次分段调用）
- 重试预算必须 > 基础预算的断言
- 原有：全失败兜底、部分失败仍整合、切分边界、8 段上限、截断头尾

## 真实 API 验证

基础预算强制 500（已知超支）、重试预算 4000、33031 字符转写：
每段先 500 失败再用 4000 成功，6 段全部拿到摘要，整合路径生成有效 JSON，
全程未使用截断兜底（日志见会话记录）。

回归：pytest 93、ruff、`npm run precommit`。
