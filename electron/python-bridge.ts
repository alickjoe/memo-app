import { spawn, spawnSync, ChildProcess } from 'child_process'
import path from 'path'
import fs from 'fs'
import { app } from 'electron'
import http from 'http'

let pythonProcess: ChildProcess | null = null
let backendPort: number = 0
let backendUrl: string = ''
let backendMode: 'frozen' | 'source' = 'frozen'
let systemPythonPath: string | null = null

// 查找可用端口
function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = http.createServer()
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (address && typeof address !== 'string') {
        const port = address.port
        server.close(() => resolve(port))
      } else {
        reject(new Error('Failed to get port'))
      }
    })
    server.on('error', reject)
  })
}

// 自动检测 Python 路径：项目 venv > system PATH
function findPythonPath(): string {
  const projectRoot = path.join(__dirname, '..')

  // 1. 优先: 项目本地 venv
  const localVenv = path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  if (fs.existsSync(localVenv)) {
    console.log(`[Python Bridge] Using project .venv: ${localVenv}`)
    return localVenv
  }

  // 2. 回退: 系统 PATH 中的 python
  console.log('[Python Bridge] Using system python from PATH')
  return 'python'
}

// 查找可用于安装包的 Python（不含项目 venv 检测，用于系统级操作）
function findSystemPython(): string | null {
  const projectRoot = path.join(__dirname, '..')

  // 1. 项目本地 venv
  const localVenv = path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  if (fs.existsSync(localVenv)) {
    return localVenv
  }

  // 2. 系统 PATH 中的 python
  try {
    const result = spawnSync('python', ['--version'], { timeout: 5000 })
    if (result.status === 0) return 'python'
  } catch { /* ignore */ }

  // 3. 尝试 python3
  try {
    const result = spawnSync('python3', ['--version'], { timeout: 5000 })
    if (result.status === 0) return 'python3'
  } catch { /* ignore */ }

  return null
}

// 检测指定 Python 是否安装了 torch + torchaudio（Silero VAD 所需）
function detectTorchAvailable(pythonPath: string): boolean {
  try {
    const result = spawnSync(
      pythonPath,
      ['-c', 'import torch, torchaudio; print(torch.__version__)'],
      { timeout: 30000 },
    )
    if (result.status !== 0) {
      const errOut = result.stderr?.toString() || ''
      if (errOut) {
        console.log(`[Python Bridge] torch detection failed for ${pythonPath}: ${errOut.slice(-200)}`)
      }
    }
    return result.status === 0
  } catch {
    return false
  }
}

// 获取 Python 后端可执行文件路径
function getPythonCommand(): { cmd: string; args: string[] } {
  const isDev = !app.isPackaged

  if (isDev) {
    const entryPath = getBackendEntryPath()
    const pythonPath = findPythonPath()
    return { cmd: pythonPath, args: [entryPath] }
  } else {
    // 生产环境：使用打包后的 backend.exe
    return { cmd: path.join(process.resourcesPath, 'backend', 'backend.exe'), args: [] }
  }
}

// 获取后端入口路径
function getBackendEntryPath(): string {
  if (app.isPackaged) {
    // 生产环境：源码在 extraResources 中
    return path.join(process.resourcesPath, 'backend', 'main.py')
  }
  return path.join(__dirname, '..', 'backend', 'main.py')
}

// 等待后端就绪
function waitForBackend(url: string, maxRetries = 30): Promise<void> {
  return new Promise((resolve, reject) => {
    let retries = 0
    const check = () => {
      http.get(`${url}/api/health`, (res) => {
        if (res.statusCode === 200) {
          resolve()
        } else if (retries < maxRetries) {
          retries++
          setTimeout(check, 500)
        } else {
          reject(new Error('Backend health check failed'))
        }
      }).on('error', () => {
        if (retries < maxRetries) {
          retries++
          setTimeout(check, 500)
        } else {
          reject(new Error('Backend failed to start'))
        }
      })
    }
    check()
  })
}

// 启动 Python 后端
export async function startPythonBackend(): Promise<string> {
  // 优先检测是否已有后端运行（dev.ps1 可能已启动）
  const defaultUrl = `http://127.0.0.1:8765`
  try {
    await waitForBackend(defaultUrl, 1)
    console.log(`[Python Bridge] Reusing existing backend at ${defaultUrl}`)
    backendUrl = defaultUrl
    backendMode = 'source'
    return backendUrl
  } catch {
    // 没有已运行的后端，启动新实例
  }

  backendPort = await findFreePort()
  backendUrl = `http://127.0.0.1:${backendPort}`

  // 决定启动模式
  let cmd: string
  let args: string[]

  if (app.isPackaged) {
    // 生产环境：检测系统 Python + torch
    systemPythonPath = findSystemPython()
    if (systemPythonPath && detectTorchAvailable(systemPythonPath)) {
      // 使用源码模式（Python + torch → Silero VAD）
      const entryPath = getBackendEntryPath()
      cmd = systemPythonPath
      args = [entryPath]
      backendMode = 'source'
      console.log(`[Python Bridge] Using source mode with ${systemPythonPath} (torch detected)`)
    } else {
      // 使用冻结 exe（无 torch → 能量 VAD）
      cmd = path.join(process.resourcesPath, 'backend', 'backend.exe')
      args = []
      backendMode = 'frozen'
      console.log('[Python Bridge] Using frozen backend.exe (no torch)')
    }
  } else {
    // 开发模式：始终使用源码
    const { cmd: devCmd, args: devArgs } = getPythonCommand()
    cmd = devCmd
    args = devArgs
    backendMode = 'source'
  }

  console.log(`[Python Bridge] Starting (${backendMode}): ${cmd} ${args.join(' ')}`)

  pythonProcess = spawn(cmd, args, {
    env: {
      ...process.env,
      BACKEND_PORT: String(backendPort),
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  })

  pythonProcess.stdout?.on('data', (data: Buffer) => {
    console.log(`[Python Backend] ${data.toString().trim()}`)
  })

  pythonProcess.stderr?.on('data', (data: Buffer) => {
    console.error(`[Python Backend Error] ${data.toString().trim()}`)
  })

  pythonProcess.on('exit', (code: number | null) => {
    console.log(`[Python Backend] exited with code ${code}`)
    pythonProcess = null
  })

  // 等待后端启动完成
  await waitForBackend(backendUrl)
  console.log(`[Python Backend] started at ${backendUrl}`)

  return backendUrl
}

// 停止 Python 后端
export async function stopPythonBackend(): Promise<void> {
  if (pythonProcess) {
    // 发送优雅关闭请求
    try {
      await fetch(`${backendUrl}/api/shutdown`, { method: 'POST' })
    } catch {
      // 忽略错误，直接 kill
    }

    pythonProcess.kill('SIGTERM')

    // 给 3 秒时间优雅退出
    await new Promise((resolve) => setTimeout(resolve, 3000))

    if (pythonProcess && !pythonProcess.killed) {
      pythonProcess.kill('SIGKILL')
    }

    pythonProcess = null
  }
}

// 获取后端 URL
export function getBackendUrl(): string {
  return backendUrl
}

// 获取后端运行模式
export function getBackendMode(): string {
  return backendMode
}

// 安装 PyTorch（异步，使用系统 Python 执行 pip install）
export function installTorch(): Promise<{ success: boolean; message: string }> {
  return new Promise((resolve) => {
    const pythonPath = systemPythonPath || findSystemPython()
    if (!pythonPath) {
      resolve({ success: false, message: 'Python not found. Please install Python 3.11+ and add to PATH.' })
      return
    }

    console.log(`[Python Bridge] Installing torch + torchaudio via ${pythonPath}...`)
    const proc = spawn(pythonPath, [
      '-m', 'pip', 'install', 'torch', 'torchaudio',
      '--index-url', 'https://download.pytorch.org/whl/cpu',
    ])

    let stderr = ''
    proc.stderr?.on('data', (data: Buffer) => {
      stderr += data.toString()
    })

    proc.on('close', (code: number | null) => {
      if (code === 0) {
        // 安装后验证 torch 和 torchaudio 是否真正可导入
        console.log('[Python Bridge] pip install completed, verifying torch + torchaudio import...')
        if (detectTorchAvailable(pythonPath)) {
          console.log('[Python Bridge] PyTorch + torchaudio installed and verified')
          resolve({ success: true, message: 'PyTorch installed. Restart app to enable Silero VAD.' })
        } else {
          resolve({ success: false, message: 'Installation completed but torch import failed. Check pip output.' })
        }
      } else {
        const errMsg = stderr.slice(-500) || `Exit code: ${code}`
        console.error(`[Python Bridge] PyTorch install failed: ${errMsg}`)
        resolve({ success: false, message: errMsg })
      }
    })

    proc.on('error', (err: Error) => {
      resolve({ success: false, message: err.message || 'Installation failed' })
    })
  })
}

// 重启后端（安装 torch 后切换到源码模式）
export async function restartBackend(): Promise<string> {
  await stopPythonBackend()
  // 重新检测 Python + torch
  systemPythonPath = null
  return startPythonBackend()
}
