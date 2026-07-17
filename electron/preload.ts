import { contextBridge, ipcRenderer } from 'electron'

// 向渲染进程暴露安全的 API
contextBridge.exposeInMainWorld('electronAPI', {
  getBackendUrl: (): Promise<string> => ipcRenderer.invoke('get-backend-url'),
  selectAudioFile: (): Promise<string | null> => ipcRenderer.invoke('select-audio-file'),
  showMainWindow: (): void => {
    ipcRenderer.invoke('show-main-window')
  },
  onTrayStartRecording: (callback: () => void): void => {
    ipcRenderer.on('tray:start-recording', callback)
  },
  removeTrayStartRecordingListener: (): void => {
    ipcRenderer.removeAllListeners('tray:start-recording')
  },
})
