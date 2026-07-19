export interface ElectronAPI {
  getBackendUrl: () => Promise<string>
  selectAudioFile: () => Promise<string | null>
  showMainWindow: () => void
  onTrayStartRecording: (callback: () => void) => void
  removeTrayStartRecordingListener: () => void
  getBackendMode: () => Promise<string>
  installTorch: () => Promise<{ success: boolean; message: string }>
  restartBackend: () => Promise<string>
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
  }
}

export {}
