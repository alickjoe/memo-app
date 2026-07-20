import { app, BrowserWindow, ipcMain, dialog, Menu } from 'electron'
import path from 'path'
import { createTray, destroyTray } from './tray'
import { startPythonBackend, stopPythonBackend, getBackendUrl, getBackendMode, installTorch, restartBackend, getPythonInfo, uninstallManagedPython } from './python-bridge'

let mainWindow: BrowserWindow | null = null
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

  ipcMain.handle('get-backend-mode', () => {
    return getBackendMode()
  })

  ipcMain.handle('install-torch', () => {
    return installTorch()
  })

  ipcMain.handle('restart-backend', async () => {
    const url = await restartBackend()
    return url
  })

  ipcMain.handle('get-python-info', () => {
    return getPythonInfo()
  })

  ipcMain.handle('uninstall-managed-python', () => {
    return uninstallManagedPython()
  })

}

// 应用启动
app.whenReady().then(async () => {
  // 禁用菜单栏
  Menu.setApplicationMenu(null)

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
  createTray(mainWindow, () => {
    isQuitting = true
    app.quit()
  })

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
  destroyTray()
  await stopPythonBackend()
})
