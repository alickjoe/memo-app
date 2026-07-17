import { Tray, Menu, nativeImage, BrowserWindow } from 'electron'
import path from 'path'

let tray: Tray | null = null

export function createTray(mainWindow: BrowserWindow, onQuit?: () => void): Tray {
  const trayIconPath = path.join(__dirname, '../assets/tray-icon.png')
  const trayIcon = nativeImage.createFromPath(trayIconPath)
  const trayObj = new Tray(trayIcon.resize({ width: 16, height: 16 }))
  trayObj.setToolTip('Memo - 会议纪要')

  const contextMenu = Menu.buildFromTemplate([
    {
      label: '显示主窗口',
      click: () => {
        mainWindow.show()
        mainWindow.focus()
      },
    },
    {
      label: '开始录制',
      click: () => {
        mainWindow.webContents.send('tray:start-recording')
        mainWindow.show()
        mainWindow.focus()
      },
    },
    { type: 'separator' },
    {
      label: '最近会议',
      click: () => {
        mainWindow.show()
        mainWindow.focus()
      },
    },
    { type: 'separator' },
    {
      label: '退出 Memo',
      click: () => {
        if (onQuit) {
          onQuit()
        }
      },
    },
  ])

  trayObj.setContextMenu(contextMenu)

  trayObj.on('double-click', () => {
    mainWindow.show()
    mainWindow.focus()
  })

  tray = trayObj
  return trayObj
}

export function setRecordingIcon(isRecording: boolean): void {
  if (!tray) return

  const iconName = isRecording ? 'tray-icon-recording.png' : 'tray-icon.png'
  const iconPath = path.join(__dirname, '../assets', iconName)
  const icon = nativeImage.createFromPath(iconPath)
  tray.setImage(icon.resize({ width: 16, height: 16 }))

  if (isRecording) {
    tray.setToolTip('Memo - 录制中...')
  } else {
    tray.setToolTip('Memo - 会议纪要')
  }
}

export function destroyTray(): void {
  if (tray) {
    tray.destroy()
    tray = null
  }
}
