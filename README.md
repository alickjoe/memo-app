# Memo - Windows 会议纪要应用 / Meeting Minutes App

[中文](#中文) | [English](#english)

---

## 中文

捕获系统音频和麦克风输入，实时转写，AI 生成结构化会议纪要。

### 功能

- **双通道音频捕获**：同时录制系统播放音（WASAPI loopback）和麦克风输入，自动混合
- **多策略音频分段**：支持 VAD（语音活动检测）/ Hybrid（VAD + 最大时长）/ Fixed（固定时长）三种分段策略，录制前可配置
- **实时语音转写**：通过 OpenAI 兼容 STT API 云端转写，支持中文/英文/自动检测
- **三层 ASR 输出校验**：过滤纯标点、乱码（有效字符占比 < 30%）、幻觉输出（超 35 字/秒）
- **说话人分离**：基于能量和过零率的轻量聚类，最多区分 4 位发言人
- **AI 会议纪要**：调用 OpenAI 兼容 LLM API 生成结构化纪要（摘要 / 关键讨论点 / 行动项 / 下一步），自动生成会议标题
- **长文本分段处理**：超长转写文本自动分段摘要后汇总
- **音频导入**：支持导入 WAV/MP3/M4A/FLAC/OGG 文件生成纪要
- **重转写版本管理**：对完整音频文件重新转写（短音频整段 + 句子拆分，长音频滑动窗口），结果存入新版本，不覆盖原有转写
- **设备信号扫描**：录制前扫描所有音频设备信号强度，录制中支持手动切换设备
- **双模式 VAD 引擎**：Silero VAD（高精度，需 PyTorch）+ 能量检测 VAD（基础回退），应用内一键安装 PyTorch
- **多语言支持**：界面中英文切换，LLM 纪要输出语言可配置（中文/英文）
- **导出功能**：会议纪要支持导出 Markdown / TXT 格式
- **系统托盘**：最小化到托盘，托盘菜单快捷开始录制

### 技术栈

| 层 | 技术 |
|---|------|
| 前端 | Electron 31 + React 18 + TypeScript + Vite + TailwindCSS |
| 状态管理 | Zustand |
| 路由 | React Router (HashRouter) |
| 国际化 | i18next + react-i18next |
| 波形可视化 | wavesurfer.js |
| 后端 | Python FastAPI + WebSocket + Uvicorn |
| 音频 | soundcard (WASAPI loopback) + Silero VAD (PyTorch) |
| STT | OpenAI 兼容 API（whisper-1 / SenseVoiceSmall 等） |
| LLM | OpenAI 兼容 API（gpt-4o-mini / deepseek-chat / qwen-plus 等） |
| 数据库 | SQLite (aiosqlite, WAL 模式) |
| 打包 | electron-builder (NSIS) + PyInstaller |

### 架构

```
┌─────────────────────────────────────────────────────┐
│ Electron 主进程 (electron/main.ts)                   │
│  窗口管理 · 系统托盘 · IPC 桥接 · Python 进程生命周期  │
└──────────┬──────────────────────────────┬────────────┘
           │ preload (contextIsolation)    │ spawn
           ▼                              ▼
┌─────────────────────┐         ┌─────────────────────┐
│ React 渲染进程       │  HTTP   │ Python 后端          │
│ (src/App.tsx)       │  REST   │ (backend/main.py)   │
│  Dashboard          │ ◄────► │  FastAPI + Uvicorn   │
│  Recording          │         │  127.0.0.1:8765      │
│  MeetingDetail      │  WS     │                      │
│  Settings           │ ◄─────► │  音频采集 → VAD →   │
└─────────────────────┘         │  STT → 说话人分离 →  │
                                 │  LLM 纪要生成        │
                                 └─────────────────────┘
```

- **前端**通过 `window.electronAPI` 获取后端 URL，不硬编码地址
- **后端**为独立 Python 进程，通过本地 HTTP + WebSocket 通信
- **双 WebSocket**：`/ws/transcript/{id}` 实时转写推送，`/ws/minutes/{id}` 纪要生成进度推送

### 快速开始

#### 环境要求

- Node.js 20+
- Python 3.11–3.13（不支持 3.14+）
- Windows 10/11
- FFmpeg（音频导入转码，可选）

#### 开发

```bash
# 1. 克隆仓库
git clone https://github.com/alickjoe/memo-app.git
cd memo-app

# 2. 安装前端依赖
npm install

# 3. 一键启动（自动创建 venv + 安装依赖 + 启动前后端）
.\dev.ps1

# 或者分别启动：
.venv\Scripts\python backend/main.py    # 启动后端
npm run dev                              # 启动前端 + Electron (新终端)

# dev.ps1 也支持单独启动：
.\dev.ps1 -BackendOnly                   # 仅后端
.\dev.ps1 -FrontendOnly                  # 仅前端
```

#### 配置 API Key

在应用内 **设置** 页面配置（推荐），或通过环境变量：

| 配置项 | 环境变量 | 说明 |
|--------|---------|------|
| STT API Key | `MEMO_API_KEY` | 语音转写 API 密钥 |
| STT Base URL | `MEMO_STT_API_BASE_URL` | 默认 `https://api.openai.com/v1` |
| LLM API Key | `MEMO_API_KEY` | 纪要生成 API 密钥（可独立配置） |
| LLM Base URL | `MEMO_API_BASE_URL` | 默认 `https://api.openai.com/v1` |

STT 和 LLM 支持独立的 API Key / Base URL / Model 配置，兼容任意 OpenAI 接口服务（OpenRouter、DeepSeek、Qwen 等）。

#### 打包

```bash
# 完整构建（Python 后端 + 前端 + Electron 安装包）
.\build\build-electron.ps1

# 仅构建 Python 后端（PyInstaller 打包为 backend.exe）
.\build\build-python.ps1

# 或使用 npm 脚本
npm run electron:build    # tsc + vite build + electron-builder
```

安装包输出在 `release/` 目录。安装路径 `%LOCALAPPDATA%\MemoApp`，无需管理员权限。

#### 发布

推送语义化版本 tag 即可触发 GitHub Actions 自动构建并发布 Release：

```bash
git tag v1.2.1
git push origin main --tags
```

### 项目结构

```
memo-app/
├── electron/                  # Electron 主进程
│   ├── main.ts                 # 应用入口、窗口管理、IPC 注册
│   ├── preload.ts              # 预加载脚本（contextIsolation 安全边界）
│   ├── tray.ts                 # 系统托盘（菜单 + 录制状态图标）
│   ├── ipc-handlers.ts         # IPC 消息处理（文件选择、后端管理等）
│   └── python-bridge.ts        # Python 进程生命周期管理
├── src/                        # React 前端
│   ├── App.tsx                 # 路由配置（HashRouter 四页面）
│   ├── pages/                  # 页面组件
│   │   ├── Dashboard.tsx       # 首页（会议列表 + 录音入口 + 拖拽导入）
│   │   ├── Recording.tsx       # 录制页（实时转写 + 设备面板 + 波形）
│   │   ├── MeetingDetail.tsx   # 会议详情（纪要 + 转写 + 重转写）
│   │   └── Settings.tsx        # 设置（API 配置 + 设备 + VAD + 录音默认）
│   ├── components/             # UI 组件
│   │   ├── AudioSourcePanel.tsx
│   │   ├── MeetingCard.tsx
│   │   ├── MinutesPanel.tsx
│   │   ├── StartRecordingDialog.tsx
│   │   ├── TranscriptStream.tsx
│   │   └── WaveformVisualizer.tsx
│   ├── stores/                 # Zustand 状态管理
│   │   ├── meetings.ts
│   │   ├── recording.ts
│   │   └── settings.ts
│   ├── services/               # API 调用封装
│   │   ├── api.ts              # REST API 封装
│   │   └── websocket.ts        # WebSocket 连接管理
│   ├── i18n/                   # 国际化
│   │   └── config.ts           # 中英文翻译资源
│   └── types/
│       └── electron.d.ts       # Electron API 类型声明
├── backend/                    # Python 后端
│   ├── main.py                 # FastAPI 入口 + 所有路由 + 音频流水线
│   ├── audio/
│   │   ├── capture.py          # WASAPI 双通道捕获 + 设备切换
│   │   ├── mixer.py            # 音频混音
│   │   └── vad.py              # Silero VAD + 能量 VAD 降级
│   ├── stt/
│   │   └── engine.py           # 云端 STT + 输出校验 + 文件级转写
│   ├── diarization/
│   │   └── speaker.py          # 说话人分离（能量 + 过零率聚类）
│   ├── llm/
│   │   ├── summarizer.py       # LLM 纪要生成 + 长文本分段 + 标题生成
│   │   └── prompts.py          # System/User prompt 模板
│   ├── storage/
│   │   ├── db.py               # SQLite 连接管理 + Schema 初始化
│   │   └── models.py           # 数据模型
│   ├── tests/                  # 后端测试 (pytest + asyncio)
│   └── requirements.txt
├── build/                      # 构建脚本
│   ├── build-electron.ps1      # 完整构建（Node + Python + Electron）
│   ├── build-python.ps1        # PyInstaller 后端打包
│   ├── install-torch.ps1       # PyTorch 安装脚本（随安装包分发）
│   ├── generate-icons.py       # 图标生成
│   └── installer.nsh           # NSIS 安装向导自定义脚本
├── assets/                     # 应用图标 + 托盘图标 + NSIS 脚本
├── .github/workflows/          # CI/CD
│   ├── lint.yml                 # Lint workflow（前端 ESLint/TS/Vitest + 后端 Ruff/pytest）
│   └── build.yml                # Build & Release workflow（构建 + 安装包 + GitHub Release）
├── dev.ps1                     # 一键开发启动脚本
├── electron-builder.yml        # electron-builder 打包配置
├── ruff.toml                   # Python lint 配置
└── package.json
```

### 数据存储

| 类型 | 路径 | 说明 |
|------|------|------|
| 数据库 | `~/.memo/memo.db` | SQLite，WAL 模式 |
| 录音文件 | `~/.memo/recordings/{meeting_id}.wav` | 16kHz mono 16-bit |
| 后端日志 | `~/.memo/logs/backend.log` | 每次启动覆盖 |
| 前端日志 | `~/.memo/logs/frontend.log` | 每次启动覆盖 |

可通过 `DATA_DIR` 环境变量自定义数据目录。

### CI/CD

两个 GitHub Actions workflow：

| Workflow | 触发条件 | 内容 |
|----------|---------|------|
| **Lint** | push/PR to main | 前端：ESLint + tsc + Vitest；后端：Ruff + pytest |
| **Build & Release** | push to main / tag `v*.*.*` / PR to main | PyInstaller 后端打包 + Vite 前端构建 + electron-builder NSIS 安装包 + GitHub Release |

CI 检查与本地等价命令：

| CI 检查 | 本地等价命令 |
|---------|-------------|
| `frontend-lint` | `npm run lint && npm run typecheck && npm run test` |
| `backend-lint` | `cd backend; ruff check backend/ && pytest` |
| `build` | `npm run build && npx electron-builder` |

### 开发命令

```bash
# 开发
.\dev.ps1                       # 一键启动 venv + 后端 + Electron (推荐)
npm run dev                     # 仅前端 + Electron (假设后端已运行)

# Lint / 类型检查（代码变更后必须执行）
npm run precommit               # eslint + tsc --noEmit
npm run lint                    # 仅 eslint
npm run typecheck               # 仅 tsc --noEmit
npm run test                    # Vitest 前端测试

# Python
cd backend; pytest              # 后端测试 (asyncio_mode=auto)
ruff check backend/             # Python lint

# 构建
npm run build                   # tsc + vite build
npm run electron:build          # 构建 + electron-builder 打包
```

### 许可证

MIT

---

## English

Capture system audio and microphone input, real-time transcription, AI-generated structured meeting minutes.

### Features

- **Dual-channel audio capture**: Simultaneously record system playback audio (WASAPI loopback) and microphone input, auto-mixed
- **Multi-strategy audio segmentation**: Supports VAD (Voice Activity Detection) / Hybrid (VAD + max duration) / Fixed (fixed duration) strategies, configurable before recording
- **Real-time speech-to-text**: Cloud transcription via OpenAI-compatible STT API, supports Chinese/English/auto-detect
- **Three-layer ASR output validation**: Filters punctuation-only, gibberish (meaningful char ratio < 30%), and hallucinated output (> 35 chars/sec)
- **Speaker diarization**: Lightweight clustering based on energy and zero-crossing rate, up to 4 speakers
- **AI meeting minutes**: Generate structured minutes via OpenAI-compatible LLM API (summary / key points / action items / next steps), with auto-generated meeting titles
- **Long text chunked processing**: Long transcripts are automatically chunked, summarized, and consolidated
- **Audio import**: Import WAV/MP3/M4A/FLAC/OGG files to generate minutes
- **Re-transcription versioning**: Re-transcribe full audio files (short audio: whole-file + sentence splitting; long audio: sliding window), results stored as new versions without overwriting existing transcripts
- **Device signal scanning**: Scan all audio device signal levels before recording; switch devices manually during recording
- **Dual-mode VAD engine**: Silero VAD (high accuracy, requires PyTorch) + energy-based VAD (basic fallback), install PyTorch in-app with one click
- **Multi-language support**: Toggle UI between Chinese/English, configurable LLM output language (Chinese/English)
- **Export**: Export meeting minutes as Markdown / TXT
- **System tray**: Minimize to tray, tray menu for quick recording start

### Tech Stack

| Layer | Technology |
|---|------|
| Frontend | Electron 31 + React 18 + TypeScript + Vite + TailwindCSS |
| State management | Zustand |
| Routing | React Router (HashRouter) |
| i18n | i18next + react-i18next |
| Waveform visualization | wavesurfer.js |
| Backend | Python FastAPI + WebSocket + Uvicorn |
| Audio | soundcard (WASAPI loopback) + Silero VAD (PyTorch) |
| STT | OpenAI-compatible API (whisper-1 / SenseVoiceSmall, etc.) |
| LLM | OpenAI-compatible API (gpt-4o-mini / deepseek-chat / qwen-plus, etc.) |
| Database | SQLite (aiosqlite, WAL mode) |
| Packaging | electron-builder (NSIS) + PyInstaller |

### Architecture

```
┌─────────────────────────────────────────────────────┐
│ Electron Main Process (electron/main.ts)             │
│  Window · Tray · IPC Bridge · Python Lifecycle       │
└──────────┬──────────────────────────────┬────────────┘
           │ preload (contextIsolation)    │ spawn
           ▼                              ▼
┌─────────────────────┐         ┌─────────────────────┐
│ React Renderer       │  HTTP   │ Python Backend        │
│ (src/App.tsx)       │  REST   │ (backend/main.py)   │
│  Dashboard          │ ◄────► │  FastAPI + Uvicorn   │
│  Recording          │         │  127.0.0.1:8765      │
│  MeetingDetail      │  WS     │                      │
│  Settings           │ ◄─────► │  Audio Capture →     │
└─────────────────────┘         │  VAD → STT →         │
                                 │  Diarization →      │
                                 │  LLM Minutes         │
                                 └─────────────────────┘
```

- **Frontend** obtains backend URL via `window.electronAPI`, no hardcoded addresses
- **Backend** runs as an independent Python process, communicating via local HTTP + WebSocket
- **Dual WebSocket**: `/ws/transcript/{id}` for real-time transcription push, `/ws/minutes/{id}` for minutes generation progress push

### Quick Start

#### Prerequisites

- Node.js 20+
- Python 3.11–3.13 (3.14+ not supported)
- Windows 10/11
- FFmpeg (for audio import transcoding, optional)

#### Development

```bash
# 1. Clone the repository
git clone https://github.com/alickjoe/memo-app.git
cd memo-app

# 2. Install frontend dependencies
npm install

# 3. One-click launch (auto-creates venv + installs deps + starts backend + frontend)
.\dev.ps1

# Or start separately:
.venv\Scripts\python backend/main.py    # Start backend
npm run dev                              # Start frontend + Electron (new terminal)

# dev.ps1 also supports standalone modes:
.\dev.ps1 -BackendOnly                   # Backend only
.\dev.ps1 -FrontendOnly                  # Frontend only
```

#### API Key Configuration

Configure in the in-app **Settings** page (recommended), or via environment variables:

| Config | Environment Variable | Description |
|--------|---------|------|
| STT API Key | `MEMO_API_KEY` | Speech-to-text API key |
| STT Base URL | `MEMO_STT_API_BASE_URL` | Default `https://api.openai.com/v1` |
| LLM API Key | `MEMO_API_KEY` | Minutes generation API key (independently configurable) |
| LLM Base URL | `MEMO_API_BASE_URL` | Default `https://api.openai.com/v1` |

STT and LLM support independent API Key / Base URL / Model configuration, compatible with any OpenAI-compatible service (OpenRouter, DeepSeek, Qwen, etc.).

#### Packaging

```bash
# Full build (Python backend + frontend + Electron installer)
.\build\build-electron.ps1

# Build Python backend only (PyInstaller → backend.exe)
.\build\build-python.ps1

# Or use npm script
npm run electron:build    # tsc + vite build + electron-builder
```

Installer output is in the `release/` directory. Installation path: `%LOCALAPPDATA%\MemoApp`, no admin privileges required.

#### Release

Push a semantic version tag to trigger GitHub Actions auto-build and publish a Release:

```bash
git tag v1.2.1
git push origin main --tags
```

### Project Structure

```
memo-app/
├── electron/                  # Electron main process
│   ├── main.ts                 # App entry, window management, IPC registration
│   ├── preload.ts              # Preload script (contextIsolation security boundary)
│   ├── tray.ts                 # System tray (menu + recording status icon)
│   ├── ipc-handlers.ts         # IPC message handlers (file selection, backend management)
│   └── python-bridge.ts        # Python process lifecycle management
├── src/                        # React frontend
│   ├── App.tsx                 # Route config (HashRouter, 4 pages)
│   ├── pages/                  # Page components
│   │   ├── Dashboard.tsx       # Home (meeting list + recording entry + drag-drop import)
│   │   ├── Recording.tsx       # Recording (real-time transcript + device panel + waveform)
│   │   ├── MeetingDetail.tsx   # Meeting detail (minutes + transcript + re-transcribe)
│   │   └── Settings.tsx        # Settings (API config + devices + VAD + recording defaults)
│   ├── components/             # UI components
│   │   ├── AudioSourcePanel.tsx
│   │   ├── MeetingCard.tsx
│   │   ├── MinutesPanel.tsx
│   │   ├── StartRecordingDialog.tsx
│   │   ├── TranscriptStream.tsx
│   │   └── WaveformVisualizer.tsx
│   ├── stores/                 # Zustand state management
│   │   ├── meetings.ts
│   │   ├── recording.ts
│   │   └── settings.ts
│   ├── services/               # API call wrappers
│   │   ├── api.ts              # REST API wrapper
│   │   └── websocket.ts        # WebSocket connection management
│   ├── i18n/                   # Internationalization
│   │   └── config.ts           # Chinese/English translation resources
│   └── types/
│       └── electron.d.ts       # Electron API type declarations
├── backend/                    # Python backend
│   ├── main.py                 # FastAPI entry + all routes + audio pipeline
│   ├── audio/
│   │   ├── capture.py          # WASAPI dual-channel capture + device switching
│   │   ├── mixer.py            # Audio mixing
│   │   └── vad.py              # Silero VAD + energy VAD fallback
│   ├── stt/
│   │   └── engine.py           # Cloud STT + output validation + file-level transcription
│   ├── diarization/
│   │   └── speaker.py          # Speaker diarization (energy + zero-crossing rate clustering)
│   ├── llm/
│   │   ├── summarizer.py       # LLM minutes generation + long text chunking + title generation
│   │   └── prompts.py          # System/User prompt templates
│   ├── storage/
│   │   ├── db.py               # SQLite connection management + schema initialization
│   │   └── models.py           # Data models
│   ├── tests/                  # Backend tests (pytest + asyncio)
│   └── requirements.txt
├── build/                      # Build scripts
│   ├── build-electron.ps1      # Full build (Node + Python + Electron)
│   ├── build-python.ps1        # PyInstaller backend packaging
│   ├── install-torch.ps1       # PyTorch install script (bundled with installer)
│   ├── generate-icons.py       # Icon generation
│   └── installer.nsh           # NSIS installer wizard custom script
├── assets/                     # App icon + tray icons + NSIS scripts
├── .github/workflows/          # CI/CD
│   ├── lint.yml                 # Lint workflow (frontend ESLint/TS/Vitest + backend Ruff/pytest)
│   └── build.yml                # Build & Release workflow (build + installer + GitHub Release)
├── dev.ps1                     # One-click dev launch script
├── electron-builder.yml        # electron-builder packaging config
├── ruff.toml                   # Python lint config
└── package.json
```

### Data Storage

| Type | Path | Description |
|------|------|------|
| Database | `~/.memo/memo.db` | SQLite, WAL mode |
| Recordings | `~/.memo/recordings/{meeting_id}.wav` | 16kHz mono 16-bit |
| Backend log | `~/.memo/logs/backend.log` | Overwritten on each start |
| Frontend log | `~/.memo/logs/frontend.log` | Overwritten on each start |

Custom data directory via the `DATA_DIR` environment variable.

### CI/CD

Two GitHub Actions workflows:

| Workflow | Trigger | Content |
|----------|---------|------|
| **Lint** | push/PR to main | Frontend: ESLint + tsc + Vitest; Backend: Ruff + pytest |
| **Build & Release** | push to main / tag `v*.*.*` / PR to main | PyInstaller backend packaging + Vite frontend build + electron-builder NSIS installer + GitHub Release |

CI checks and local equivalent commands:

| CI Check | Local Equivalent |
|---------|-------------|
| `frontend-lint` | `npm run lint && npm run typecheck && npm run test` |
| `backend-lint` | `cd backend; ruff check backend/ && pytest` |
| `build` | `npm run build && npx electron-builder` |

### Development Commands

```bash
# Development
.\dev.ps1                       # One-click launch: venv + backend + Electron (recommended)
npm run dev                     # Frontend + Electron only (assumes backend is running)

# Lint / Type checking (must run after code changes)
npm run precommit               # eslint + tsc --noEmit
npm run lint                    # eslint only
npm run typecheck               # tsc --noEmit only
npm run test                    # Vitest frontend tests

# Python
cd backend; pytest              # Backend tests (asyncio_mode=auto)
ruff check backend/             # Python lint

# Build
npm run build                   # tsc + vite build
npm run electron:build          # Build + electron-builder packaging
```

### License

MIT
