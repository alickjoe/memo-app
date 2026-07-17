import { BrowserWindow } from 'electron'
import path from 'path'

let floatingBall: BrowserWindow | null = null

export function createFloatingBall(mainWindow: BrowserWindow): BrowserWindow {
  const win = new BrowserWindow({
    width: 56,
    height: 56,
    x: undefined,  // 默认右下角
    y: undefined,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // 加载悬浮球页面
  if (process.env.VITE_DEV_SERVER_URL) {
    win.loadURL(`${process.env.VITE_DEV_SERVER_URL}#/floating-ball`)
  } else {
    win.loadFile(path.join(__dirname, '../dist/index.html'), {
      hash: '/floating-ball',
    })
  }

  // 双击打开主窗口
  win.on('vibrancy', () => {
    mainWindow.show()
    mainWindow.focus()
  })

  floatingBall = win
  return win
}

export function getFloatingBall(): BrowserWindow | null {
  return floatingBall
}
