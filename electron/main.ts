import { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, dialog } from 'electron'
import path from 'path'
import { createFloatingBall } from './floating-ball'
import { startPythonBackend, stopPythonBackend, getBackendUrl } from './python-bridge'

let mainWindow: BrowserWindow | null = null
let tray: Tray | null = null
let floatingBallWindow: BrowserWindow | null = null
let isQuitting = false

// 创建主窗口
function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1100,
    height: 720,
    minWidth: 800,
    minHeight: 560,
    title: 'Memo - 会议纪要',
    icon: path.join(__dirname, '../assets/icon.ico'),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // 开发环境加载 Vite dev server，生产环境加载打包文件
  if (process.env.VITE_DEV_SERVER_URL) {
    win.loadURL(process.env.VITE_DEV_SERVER_URL)
  } else {
    win.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  // 关闭窗口时最小化到托盘而非退出
  win.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault()
      win.hide()
    }
  })

  win.once('ready-to-show', () => {
    win.show()
  })

  return win
}

// 创建系统托盘
function createTray(): Tray {
  const trayIcon = nativeImage.createFromPath(
    path.join(__dirname, '../assets/tray-icon.png')
  )
  const trayObj = new Tray(trayIcon.resize({ width: 16, height: 16 }))
  trayObj.setToolTip('Memo - 会议纪要')

  const contextMenu = Menu.buildFromTemplate([
    {
      label: '显示主窗口',
      click: () => {
        mainWindow?.show()
        mainWindow?.focus()
      },
    },
    {
      label: '开始录制',
      click: () => {
        mainWindow?.webContents.send('tray:start-recording')
      },
    },
    { type: 'separator' },
    {
      label: '最近会议',
      click: () => {
        mainWindow?.show()
        mainWindow?.focus()
      },
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        isQuitting = true
        app.quit()
      },
    },
  ])

  trayObj.setContextMenu(contextMenu)

  // 双击托盘图标显示主窗口
  trayObj.on('double-click', () => {
    mainWindow?.show()
    mainWindow?.focus()
  })

  return trayObj
}

// IPC 处理
function registerIpcHandlers(): void {
  ipcMain.handle('get-backend-url', () => {
    return getBackendUrl()
  })

  ipcMain.handle('select-audio-file', async () => {
    const result = await dialog.showOpenDialog({
      properties: ['openFile'],
      filters: [
        { name: '音频文件', extensions: ['wav', 'mp3', 'm4a', 'flac', 'ogg'] },
      ],
    })
    return result.canceled ? null : result.filePaths[0]
  })

  ipcMain.handle('show-main-window', () => {
    mainWindow?.show()
    mainWindow?.focus()
  })
}

// 应用启动
app.whenReady().then(async () => {
  registerIpcHandlers()

  // 启动 Python 后端
  try {
    await startPythonBackend()
  } catch (err) {
    console.error('Failed to start Python backend:', err)
  }

  // 创建主窗口
  mainWindow = createMainWindow()

  // 创建系统托盘
  tray = createTray()

  // 创建悬浮球
  floatingBallWindow = createFloatingBall(mainWindow)

  // macOS 特定处理
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createMainWindow()
    } else {
      mainWindow?.show()
    }
  })
})

// 所有窗口关闭时的处理
app.on('window-all-closed', () => {
  // Windows 上不退出，保持在托盘
  if (process.platform !== 'darwin') {
    // 不退出
  }
})

// 应用退出前清理
app.on('before-quit', async () => {
  isQuitting = true
  await stopPythonBackend()
})
