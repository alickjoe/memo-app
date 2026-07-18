import { spawn, ChildProcess } from 'child_process'
import path from 'path'
import fs from 'fs'
import { app } from 'electron'
import http from 'http'

let pythonProcess: ChildProcess | null = null
let backendPort: number = 0
let backendUrl: string = ''

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
    return backendUrl
  } catch {
    // 没有已运行的后端，启动新实例
  }

  backendPort = await findFreePort()
  backendUrl = `http://127.0.0.1:${backendPort}`

  const { cmd, args } = getPythonCommand()
  console.log(`[Python Bridge] Starting: ${cmd} ${args.join(' ')}`)

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
