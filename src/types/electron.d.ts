export interface ElectronAPI {
  getBackendUrl: () => Promise<string>
  selectAudioFile: () => Promise<string | null>
  showMainWindow: () => void
  onTrayStartRecording: (callback: () => void) => void
  removeTrayStartRecordingListener: () => void
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
  }
}

export {}
