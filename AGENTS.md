# AGENTS.md — Memo App

## 三运行时边界

| 运行时 | 入口文件 | 职责 |
|--------|----------|------|
| **Electron 主进程** | `electron/main.ts` | 窗口/Tray/IPC 管理，通过 `electron/python-bridge.ts` 启动/停止 Python 后端 |
| **React 渲染进程** | `src/main.tsx` → `src/App.tsx` | HashRouter 四页面 (Dashboard/Recording/MeetingDetail/Settings)，通过 `src/services/api.ts` 调后端 REST，`src/services/websocket.ts` 接收推送 |
| **Python 后端** | `backend/main.py` | FastAPI + Uvicorn (127.0.0.1:8765)，音频采集→VAD→STT→LLM 流水线，`/ws/transcript/{id}` 和 `/ws/minutes/{id}` 双 WebSocket |

IPC 桥接：`electron/preload.ts` → `src/types/electron.d.ts` (类型声明) → 渲染进程 `window.electronAPI`

## 模块联动检查清单

修改以下模块时，需同步检查关联文件：

| 修改模块 | 需检查的关联文件 |
|----------|------------------|
| `backend/stt/engine.py` | `backend/main.py` WebSocket 推送 `_transcribe_segment()`、`src/components/TranscriptStream.tsx` 展示格式、`src/services/websocket.ts` 连接逻辑 |
| `backend/audio/capture.py` | `backend/main.py` `process_audio_pipeline()`、`src/components/AudioSourcePanel.tsx` 设备列表 |
| `backend/audio/vad.py` | `backend/main.py` 策略自动降级 (VAD degraded→fixed)、`src/pages/Settings.tsx` 录音参数配置 |
| `backend/llm/summarizer.py` | `backend/main.py` `generate_minutes()`、`src/components/MinutesPanel.tsx` 展示 |
| `electron/preload.ts` | `electron/ipc-handlers.ts`、`src/types/electron.d.ts` |
| `src/stores/*.ts` (Zustand) | `src/pages/*.tsx` 消费该 store 的页面、`src/services/api.ts` 调用链 |

## 常用命令

```bash
# 开发启动
.\dev.ps1                       # 一键启动 venv + 后端 + Electron (推荐)
npm run dev                     # 仅前端 + Electron (假设后端已运行)

# 构建
npm run build                   # tsc + vite build
npm run electron:build          # 构建 + electron-builder 打包

# Lint / 类型检查
npm run lint                    # eslint src/ electron/ --ext .ts,.tsx
npm run typecheck               # tsc --noEmit
npm run precommit               # lint + typecheck (每次代码变更后必须执行)

# Python
cd backend; pytest              # 后端测试 (asyncio_mode=auto)
ruff check backend/             # Python lint
```

## 高风险区域

- **`backend/main.py`** — 音频流水线 `process_audio_pipeline()` (VAD 多策略切换/滞回状态机)、WebSocket 连接池 `ws_connections`、`_run_retranscribe_sync()` 独立线程 asyncio.run、全局单例状态管理
- **`backend/stt/engine.py`** — `verify=False` 全局禁用 SSL 证书验证 (企业网络兼容)、API key 明文从 settings 表读取、滑动窗口去重逻辑
- **`electron/python-bridge.ts`** — Python 进程生命周期 (SIGTERM/SIGKILL 双阶段终止)、端口动态分配、frozen/source 双模式自动切换、Torch 三步安装与验证
- **`electron/main.ts`** — `before-quit` 清理链 (Tray→Python→窗口)、`contextIsolation: true` 安全边界、窗口关闭最小化到托盘逻辑

## 预提交验证流程（强制）

任何代码变更（`src/`、`electron/`、`backend/` 或配置文件）后，任务收尾前必须执行：

1. 运行 `npm run precommit`（等价于 `npm run lint` + `npm run typecheck`）
2. 检查输出：lint 与 typecheck 均无 error 才算通过
3. 若有 error，修复后必须重新运行直到全部通过
4. 验证通过前不得结束任务、不得请求提交代码；会话日志中须保留 `npm run precommit` 的实际输出作为验证证据

## CI 交付验收（强制）

交付完成以 **本地验证 + CI 通过** 双轨为准，提交、PR 或合并操作前必须同时满足：

1. **本地验证** — `npm run precommit` 通过；涉及 Python 后端时追加 `cd backend; ruff check backend/`
2. **CI 通过** — GitHub Actions 的 `Lint` 与 `Build & Release` 两个 workflow 的最新执行均为 `success`（状态查 https://github.com/alickjoe/memo-app/actions ）
3. **CI 触发条件** — 两个 workflow 均已配置 `push`（main/标签）与 `pull_request`（目标 main）双触发；推送后须等待 CI 完成，CI 未通过前不得标记任务完成、不得请求合并
4. **分支保护** — main 分支的 required status checks 为 `frontend-lint`、`backend-lint`、`build`；PR 显示 "Some checks haven't completed yet" 时须等待全部通过后再合并

CI 检查与本地等价命令的对应关系：

| CI 检查 | 本地等价命令 | 覆盖范围 |
|---------|--------------|----------|
| `frontend-lint` (Lint workflow) | `npm run lint` + `npm run typecheck` + `npm run test` | 前端 ESLint / TS / Vitest |
| `backend-lint` (Lint workflow) | `cd backend; ruff check backend/` + `pytest` | Python 静态检查 / 测试 |
| `build` (Build & Release workflow) | `npm run build` + `npx electron-builder` | 前端构建 / 后端打包 / 安装包 |

## 关键约定

- **Python 后端是独立进程** — 修改后端代码后必须重启 Electron (`npm run dev`)，前端热更新不会重载 Python
- **前端通过 `window.electronAPI` 获取后端 URL** — 不要硬编码 `localhost:8765`；非 Electron 环境此 API 不存在
- **每次代码变更后必须运行 `npm run precommit`（强制）** — 即 `npm run lint && npm run typecheck`；lint/typecheck 全部通过前，任何任务不得视为完成。此约束对 `src/`、`electron/`、`backend/` 的代码修改会话均生效
- **日志位置** — 后端 `~/.memo/logs/backend.log`，前端 `~/.memo/logs/frontend.log`
- **数据库** — `memo.db` (项目根目录)，SQLite via `aiosqlite`，由 `backend/storage/db.py` 管理 schema
