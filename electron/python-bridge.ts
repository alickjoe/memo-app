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

// 扫描 Windows 常见 Python 安装目录，返回候选 exe 路径（版本降序）
function scanWindowsPythonDirs(): string[] {
  const candidates: string[] = []
  const seen = new Set<string>()

  const addIfExists = (dir: string): void => {
    const exe = path.join(dir, 'python.exe')
    if (!seen.has(exe) && fs.existsSync(exe)) {
      seen.add(exe)
      candidates.push(exe)
    }
  }

  // %LOCALAPPDATA%/Programs/Python/ — 官网及 Store 版默认路径
  try {
    const localAppData = process.env.LOCALAPPDATA
    if (localAppData) {
      const pyParent = path.join(localAppData, 'Programs', 'Python')
      if (fs.existsSync(pyParent)) {
        const entries = fs.readdirSync(pyParent, { withFileTypes: true })
        for (const entry of entries) {
          if (entry.isDirectory() && /^Python3\d+$/i.test(entry.name)) {
            addIfExists(path.join(pyParent, entry.name))
          }
        }
      }
    }
  } catch { /* ignore */ }

  // C:/Python3X — 经典安装路径，覆盖 3.8 ~ 3.20
  for (let minor = 20; minor >= 8; minor--) {
    addIfExists(`C:/Python3${minor}`)
  }

  // C:/Program Files/Python3X
  for (let minor = 20; minor >= 8; minor--) {
    addIfExists(`C:/Program Files/Python3${minor}`)
  }

  // 按版本号降序，稳定版 (3.11-3.13) 优先于实验版 (3.14+)
  candidates.sort((a, b) => {
    const va = parseInt((a.match(/Python3(\d+)/i) || [])[1] || '0', 10)
    const vb = parseInt((b.match(/Python3(\d+)/i) || [])[1] || '0', 10)
    const scoreA = (va >= 11 && va <= 13) ? va + 100 : va
    const scoreB = (vb >= 11 && vb <= 13) ? vb + 100 : vb
    return scoreB - scoreA
  })

  return candidates
}

// 查找可用于安装包的 Python（不含项目 venv 检测，用于系统级操作）
function findSystemPython(): string | null {
  const projectRoot = path.join(__dirname, '..')

  // 1. 项目本地 venv
  const localVenv = path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  if (fs.existsSync(localVenv)) {
    return localVenv
  }

  // 2. 系统 PATH 中的 python（解析为绝对路径，避免 spawn 时解析到不同解释器）
  try {
    const result = spawnSync('python', ['-c', 'import sys; print(sys.executable)'], { timeout: 5000 })
    if (result.status === 0) {
      const fullPath = result.stdout?.toString().trim()
      if (fullPath) return fullPath
    }
  } catch { /* ignore */ }

  // 3. 尝试 python3
  try {
    const result = spawnSync('python3', ['-c', 'import sys; print(sys.executable)'], { timeout: 5000 })
    if (result.status === 0) {
      const fullPath = result.stdout?.toString().trim()
      if (fullPath) return fullPath
    }
  } catch { /* ignore */ }

  // 4. 扫描 Windows 常见安装目录
  if (process.platform === 'win32') {
    const candidates = scanWindowsPythonDirs()
    for (const candidate of candidates) {
      try {
        const result = spawnSync(candidate, ['--version'], { timeout: 5000 })
        if (result.status === 0) {
          console.log(`[Python Bridge] Found Python at: ${candidate}`)
          return candidate
        }
      } catch { /* ignore */ }
    }
  }

  return null
}

// 验证指定 Python 是否安装了所有运行时依赖（source mode 切换前置条件）
function verifyAllDepsAvailable(pythonPath: string): boolean {
  try {
    const result = spawnSync(
      pythonPath,
      ['-c', 'import fastapi, uvicorn, soundcard, numpy, httpx, aiosqlite, torch, torchaudio; print("OK")'],
      { timeout: 30000 },
    )
    if (result.status !== 0) {
      const errOut = result.stderr?.toString() || ''
      if (errOut) {
        console.log(`[Python Bridge] dependency check failed for ${pythonPath}: ${errOut.slice(-200)}`)
      }
    }
    return result.status === 0 && (result.stdout?.toString() || '').includes('OK')
  } catch {
    return false
  }
}

// 带超时的 spawn promise 封装
function spawnPromise(
  cmd: string,
  args: string[],
  timeoutMs: number,
): Promise<{ code: number | null; stderr: string }> {
  return new Promise((resolve) => {
    const proc = spawn(cmd, args)
    let stderr = ''
    let settled = false

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true
        proc.kill()
        resolve({ code: null, stderr: 'Installation timed out' })
      }
    }, timeoutMs)

    proc.stderr?.on('data', (data: Buffer) => {
      stderr += data.toString()
    })

    proc.on('close', (code) => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        resolve({ code, stderr })
      }
    })

    proc.on('error', (err: Error) => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        resolve({ code: null, stderr: err.message })
      }
    })
  })
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
    if (systemPythonPath && verifyAllDepsAvailable(systemPythonPath)) {
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
  return (async (): Promise<{ success: boolean; message: string }> => {
    const pythonPath = systemPythonPath || findSystemPython()
    if (!pythonPath) {
      return { success: false, message: 'Python not found. Please install Python 3.11+ and add to PATH.' }
    }

    // Step 1: 安装运行时基础依赖（从 requirements.txt 读取）
    const reqPath = app.isPackaged
      ? path.join(process.resourcesPath, 'backend', 'requirements.txt')
      : path.join(__dirname, '..', 'backend', 'requirements.txt')

    let runtimeDeps: string[] = []
    try {
      const content = fs.readFileSync(reqPath, 'utf-8')
      runtimeDeps = content
        .split('\n')
        .map(line => line.trim())
        .filter(line => line && !line.startsWith('#') && !line.includes('pyinstaller'))
        .map(line => line.split(/[>=<~!]/)[0].trim())
        .filter(name => name && name !== 'torch' && name !== 'torchaudio')
      console.log(`[Python Bridge] Runtime deps parsed from requirements.txt: ${runtimeDeps.join(', ')}`)
    } catch {
      // requirements.txt 不可用时回退到硬编码列表
      runtimeDeps = ['fastapi', 'uvicorn[standard]', 'soundcard', 'numpy', 'httpx', 'aiosqlite']
      console.log('[Python Bridge] Using hardcoded runtime deps fallback')
    }

    console.log(`[Python Bridge] Step 1/3: Installing runtime deps via ${pythonPath}...`)
    const step1 = await spawnPromise(pythonPath, ['-m', 'pip', 'install', ...runtimeDeps], 600_000)
    if (step1.code !== 0) {
      const errMsg = step1.stderr.slice(-500) || `Exit code: ${step1.code}`
      console.error(`[Python Bridge] Runtime deps install failed: ${errMsg}`)
      return { success: false, message: `Dependencies install failed: ${errMsg}` }
    }
    console.log('[Python Bridge] Runtime deps installed')

    // Step 2: 安装 PyTorch（需要单独指定 CPU 索引）
    console.log(`[Python Bridge] Step 2/3: Installing torch + torchaudio via ${pythonPath}...`)
    const step2 = await spawnPromise(pythonPath, [
      '-m', 'pip', 'install', 'torch', 'torchaudio',
      '--index-url', 'https://download.pytorch.org/whl/cpu',
    ], 600_000)
    if (step2.code !== 0) {
      const errMsg = step2.stderr.slice(-500) || `Exit code: ${step2.code}`
      console.error(`[Python Bridge] PyTorch install failed: ${errMsg}`)
      return { success: false, message: `PyTorch install failed: ${errMsg}` }
    }

    // Step 3: 验证所有依赖均可导入
    console.log('[Python Bridge] Step 3/3: Verifying all dependencies...')
    if (verifyAllDepsAvailable(pythonPath)) {
      console.log('[Python Bridge] All dependencies verified')
      return { success: true, message: 'PyTorch installed. Restart app to enable Silero VAD.' }
    }

    // 逐个诊断失败模块
    const modules = ['fastapi', 'uvicorn', 'soundcard', 'numpy', 'httpx', 'aiosqlite', 'torch', 'torchaudio']
    const failed: string[] = []
    for (const mod of modules) {
      const r = spawnSync(pythonPath, ['-c', `import ${mod}`], { timeout: 15000 })
      if (r.status !== 0) {
        const detail = (r.stderr?.toString() || '').split('\n').slice(-2).join(' | ').trim() || 'unknown error'
        failed.push(`${mod}(${detail})`)
      }
    }
    const detail = failed.length > 0 ? ` Failed: ${failed.join('; ')}` : ''
    return { success: false, message: `Import verification failed.${detail}` }
  })()
}

// 重启后端（安装 torch 后切换到源码模式）
export async function restartBackend(): Promise<string> {
  await stopPythonBackend()
  // 重新检测 Python + torch
  systemPythonPath = null
  return startPythonBackend()
}
