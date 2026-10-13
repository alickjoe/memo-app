# Spec：重新生成纪要 / 重新转写的任务反馈与进度状态

状态：**已确认**（2026-10-13 确认：方案实施 + 启动看门狗 + 完成后发 v1.3.3；追加纪要标题语言问题一并修复）

---

## 0. 追加需求：纪要章节标题语言跟随输出语言（2026-10-13 确认并入）

**问题**：界面语言为中文、纪要输出语言为英文时，总结页各章节标题（摘要/关键讨论点/行动项/下一步）跟随界面语言显示中文，与英文纪要内容不一致。根因：`MinutesPanel` 用 `t('minutes.*')`，永远跟随界面语言。

**修复**：
1. minutes 表加 `language TEXT` 列（幂等迁移），`generate_minutes` 写入生成时的 `llm_output_language`（'en'/'zh'），历史行该列为空
2. `MinutesPanel` 标题渲染按行覆盖 i18n 语言：`t('minutes.summary', { lng })`；`lng = minutes.language`，历史行无 language 时用启发式兜底（summary 含 CJK 字符 → zh，否则 en）
3. `GET /api/meetings/{id}` 的 minutes 响应带上 language 字段

---

## 1. 问题分析（现状根因）

### 1.1 重新生成纪要 —— 完全无反馈（根因级）

`MeetingDetail.handleRegenerate` 现状：

```ts
await fetch(`${backendUrl}/api/meetings/${id}/regenerate`, { method: 'POST' })
setTimeout(loadMeeting, 2000)   // 盲等 2 秒刷新一次，之后再也没有任何动作
```

| # | 缺陷 | 说明 |
|---|------|------|
| 1 | **后端不置状态** | `regenerate` 端点不把会议置为 `processing`（LLM 生成通常 5-60s），期间 status 保持 `done` |
| 2 | **WS 收不到结果事件** | 详情页 WS 只在 `recording/processing` 状态下连接；对 done 会议重新生成时无 WS 连接 → 后端的 `minutes_ready` 事件永远收不到 |
| 3 | 按钮无加载态 | 生成期间按钮仍可点击（可重复点击堆积多个生成任务） |
| 4 | 失败静默 | `generate_minutes` 异常时把 status 置 `error`，但**不推送任何事件**；前端 2 秒后刷新一次，若生成还在跑就看到旧纪要，之后彻底无感知 |
| 5 | POST 响应不检查 | 无转写文本时后端返回 404，前端忽略 `res.ok`，用户毫无感知 |
| 6 | 错误原因丢失 | 失败原因只写后端日志，数据库不存，前端无法展示"为什么失败" |

### 1.2 重新转写 —— 有反馈但有三类缺口

`handleRetranscribe` 已有：按钮 spinner、蓝色进行中横幅、错误横幅、2 秒轮询（版本号出现=成功 / status=error=失败）、WS 兜底。缺口：

| # | 缺陷 | 说明 |
|---|------|------|
| 1 | **无进度/阶段** | 重新转写 = 整文件 STT（长会议要几分钟）+ 重聚类 + 重新生成纪要，横幅只有一行静态文字，用户不知道进行到哪一步、还要多久 |
| 2 | **卡死无解** | 轮询只认"版本号出现"或"status=error"两种终态；若后端线程在设置状态前崩溃、或应用升级重启导致 status 永久停留 `processing`，前端**永久转圈** |
| 3 | 错误信息笼统 | 失败只显示"重新转写失败"，后端具体原因（如音频文件损坏、API key 失效）不落库不展示 |

---

## 2. 方案总览

前后端配合的**任务状态反馈闭环**：

```
点击按钮 → 按钮进入加载态（禁用+spinner）
         → 后端置 status=processing、清空 error_message
         → 前端显示反馈横幅（阶段文字 + 已耗时秒数 ticker）
         → 后端按阶段推 WS 事件（transcribing → reclustering → minutes）
         → 终态：成功 minutes_ready / 失败 minutes_error（带原因）
         → 前端展示结果或失败原因（可关闭横幅），按钮恢复
兜底：后端启动时把遗留的 processing 会议重置为 error（防升级重启卡死）
```

---

## 3. 后端设计

### 3.1 数据库：meetings 表加 `error_message` 列

- 幂等迁移 `ALTER TABLE meetings ADD COLUMN error_message TEXT`（照抄 `_migrate_add_version_column` 模式）
- 成功时置 NULL，失败时写入异常摘要（截断 500 字符）
- `GET /api/meetings/{id}` 响应带上 `error_message`

### 3.2 regenerate 端点修复

- 有转写文本时：先 `UPDATE meetings SET status='processing', error_message=NULL` 再 `create_task`，返回 200
- 无转写文本：保持 404（前端现在会检查）

### 3.3 `generate_minutes` 补反馈

- 成功路径不变（已有 `minutes_ready` WS 事件 + status=done + 清空 error_message）
- **异常路径新增**：写 `error_message` + 推 WS 事件 `{"type": "minutes_error", "message": "<原因>"}`
- 该函数被三条路径复用（录音收尾 / regenerate / retranscribe），一处修改全部受益

### 3.4 `_do_retranscribe` 阶段事件 + 错误落库

- 在三个阶段点推 WS：`{"type": "retranscribe_stage", "stage": "transcribing" | "reclustering" | "minutes"}`（STT 开始 / 重聚类前 / 生成纪要前——最后者即 `generate_minutes` 自身的事件流）
- 失败路径（STT 失败、重聚类异常、纪要失败）：写 `error_message` + status=error（现有逻辑），并推 `minutes_error`
- `_run_retranscribe_sync` 外层崩溃兜底：目前只打日志，补 `status=error` + `error_message`（修掉"status 永久 processing"的边缘路径）

### 3.5 启动看门狗（防卡死）

`lifespan` 启动时：`UPDATE meetings SET status='error', error_message='应用重启时任务被中断，请重试' WHERE status='processing'`
——处理"升级重启后永久转圈"，对重新生成/重新转写/导入三类任务统一生效。

### 3.6 WS 时序说明

重新生成时前端在 POST 返回后立即（乐观）切换本地 status 为 processing 并建 WS 连接；生成任务耗时 ≥ 数秒，连接先于终态事件建立，时序安全。轮询作为 WS 失效时的兜底（现有模式沿用）。

---

## 4. 前端设计（MeetingDetail.tsx）

### 4.1 重新生成按钮状态机

```
idle → (点击) generating → done（短暂提示后消失）/ failed（横幅常驻可关闭）→ idle
```

- `isRegenerating` 状态：按钮禁用 + 文案变"正在生成纪要..."（复用现有 spinner 样式）
- 点击时检查 `res.ok`（404 → 横幅"没有可用的转写文本"）
- 生成中显示反馈横幅：spinner + 阶段文字 + **已耗时 ticker**（每秒更新，用户能看到"活着"）
- 终态判定三通道（任一先到）：WS `minutes_ready` / WS `minutes_error` / 轮询到 status=done|error（沿用现有 2s 轮询模式，失败时从 GET 响应读 `error_message` 展示具体原因）
- 成功时短暂显示"纪要已更新"2 秒后自动消失；失败横幅常驻可手动关闭
- 与 retranscribe 互斥：两者任一进行中，另一按钮禁用
- 会议处于 recording/processing 状态时禁用重新生成（防止对不完整转写生成纪要）

### 4.2 重新转写横幅增强（保留现有机制，补三块）

- 横幅加**已耗时 ticker**（同 4.1 组件复用）
- 阶段文字跟随 WS `retranscribe_stage` 事件：转写中 → 修正说话人标签 → 生成纪要中
- 失败横幅展示后端 `error_message`（有则显示具体原因，无则回退现有笼统文案）

### 4.3 组件化

进行中横幅（spinner + 文字 + 耗时）抽成小组件（如 `TaskStatusBar`），两个按钮共用，避免重复样式代码。

---

## 5. i18n（EN + ZH）

新增 key（命名空间 `meeting`）：`regenerating`（正在生成纪要...）、`regenerateDone`（纪要已更新）、`regenerateFailed`（纪要生成失败）、`noTranscript`（没有可用的转写文本）、`taskElapsed`（已进行 {s} 秒）、`stageTranscribing`（语音转写中）、`stageReclustering`（修正说话人标签）、`stageMinutes`（生成会议纪要中）、`taskInterrupted`（应用重启时任务被中断，请重试）。

---

## 6. 测试计划

**后端**（pytest，延续现有 mock 模式）：
- regenerate 端点：置 processing + 清 error_message；无转写文本返回 404
- `generate_minutes` 失败：写 error_message、推 `minutes_error` WS 事件、status=error；成功：清 error_message
- retranscribe 失败路径：error_message 落库；外层崩溃兜底置 error
- 启动看门狗：遗留 processing → error + 提示语
- 迁移：error_message 列幂等添加

**前端**：
- 现有 MeetingDetail 测试保持通过（新增状态不得破坏现有断言；如测试断言按钮文案需同步）
- regenerate 按钮生成中禁用（fetch mock 下断言按钮 disabled 与文案切换）

**工程质量**：`npm run precommit` + `cd backend && ruff check` + `pytest` 全绿。

### 验收标准

1. 重新生成：点击后按钮立即禁用转圈，横幅显示耗时递增；失败时横幅显示具体原因（如 API key 无效）可关闭；成功时纪要自动刷新并短暂提示
2. 重新转写：横幅显示耗时与阶段（转写→修正说话人→生成纪要）；失败显示具体原因
3. 后端升级重启后，遗留 processing 会议显示"任务被中断"而非永久转圈
4. precommit + CI 三项检查全通过

---

## 7. 实施顺序与工作量

| 步骤 | 内容 | 估时 |
|------|------|------|
| 1 | DB 迁移 + regenerate 端点修复 + generate_minutes/retranscribe 错误落库与 WS 事件 + 启动看门狗 | 2h |
| 2 | MeetingDetail 状态机 + TaskStatusBar 组件 + i18n | 2h |
| 3 | 前后端测试 + precommit + CI | 1h |

合计约 5h，单 PR 交付（含版本号 bump 1.3.3 或按发布节奏定）。

## 8. 本次不做（边界）

- STT 逐句进度百分比（需要 STT 引擎改造，收益低）
- Dashboard 卡片上的错误原因展示（详情页已有，暂不扩散）
- 任务队列/并发控制（现有单线程池 + 禁用按钮已足够）
