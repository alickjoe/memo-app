# Memo - Windows 会议纪要应用

捕获系统音频和麦克风输入，实时转写，AI 生成会议纪要。

## 功能

- **双通道音频捕获**：同时录制系统播放音（WASAPI loopback）和麦克风输入
- **实时语音转写**：通过 OpenAI Whisper API 云端转写，支持中文/英文
- **说话人分离**：自动区分不同发言人
- **AI 会议纪要**：调用 GPT-4o-mini / DeepSeek 自动生成结构化纪要
- **悬浮球快捷操作**：桌面悬浮球 + 系统托盘，一键开始录制
- **音频导入**：支持导入 WAV/MP3/M4A/FLAC 文件生成纪要

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | Electron + React 18 + TypeScript + Vite + TailwindCSS |
| 后端 | Python FastAPI + WebSocket |
| 音频 | soundcard (WASAPI) + Silero VAD |
| STT | OpenAI Whisper API |
| LLM | OpenAI GPT-4o-mini / DeepSeek |
| 数据库 | SQLite (aiosqlite) |
| 打包 | electron-builder (NSIS) + PyInstaller |

## 快速开始

### 环境要求

- Node.js 20+
- Python 3.11+
- Windows 10/11
- Visual Studio Build Tools（编译 PyAudio C 扩展）

### 开发

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/memo-app.git
cd memo-app

# 2. 安装前端依赖
npm install

# 3. 创建 Python 虚拟环境
python -m venv .venv

# 4. 激活环境并安装后端依赖
.venv\Scripts\activate
pip install -r backend\requirements.txt

# 5. 配置 API Key
# 方式 A: 在应用内设置页面填写 API Key
# 方式 B: 设置环境变量 MEMO_API_KEY 和 MEMO_API_BASE_URL

# 6. 一键启动（自动创建 venv + 安装依赖 + 启动前后端）
.\dev.ps1

# 或者分别启动：
.venv\Scripts\python backend/main.py    # 启动后端
npm run dev                              # 启动前端 (新终端)
```

### 打包

```bash
# 完整构建（Python 后端 + Electron 安装包）
.\build\build-electron.ps1

# 仅构建 Python 后端
.\build\build-python.ps1
```

安装包输出在 `release/` 目录，安装到 `%LOCALAPPDATA%\MemoApp`，无需管理员权限。

### 发布

推送 tag 即可触发 GitHub Actions 自动构建并发布 Release：

```bash
git tag v0.1.0
git push origin main --tags
```

## 项目结构

```
memo-app/
├── electron/           # Electron 主进程
│   ├── main.ts         # 应用入口、窗口管理
│   ├── preload.ts      # 预加载脚本（安全 IPC）
│   ├── tray.ts         # 系统托盘
│   ├── floating-ball.ts # 悬浮球窗口
│   ├── ipc-handlers.ts # IPC 消息处理
│   └── python-bridge.ts # Python 子进程管理
├── src/                # React 前端
│   ├── pages/          # 页面组件
│   ├── components/     # UI 组件
│   ├── stores/         # Zustand 状态管理
│   └── services/       # API 调用封装
├── backend/            # Python 后端
│   ├── main.py         # FastAPI 入口 + 所有路由
│   ├── audio/          # 音频捕获、混音、VAD
│   ├── stt/            # 云端语音转写
│   ├── diarization/    # 说话人分离
│   ├── llm/            # LLM 纪要生成
│   └── storage/        # SQLite 数据库
├── build/              # 构建脚本
└── .github/workflows/  # CI/CD
```

## 许可证

MIT
