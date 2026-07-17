import { create } from 'zustand'

interface AppSettings {
  openai_api_key: string
  openai_base_url: string
  stt_model: string
  stt_language: string
  llm_model: string
  deepseek_api_key: string
}

interface SettingsState {
  settings: AppSettings
  loaded: boolean
  fetchSettings: () => Promise<void>
  updateSettings: (updates: Partial<AppSettings>) => void
  saveSettings: () => Promise<void>
}

const defaultSettings: AppSettings = {
  openai_api_key: '',
  openai_base_url: 'https://api.openai.com/v1',
  stt_model: 'whisper-1',
  stt_language: 'zh',
  llm_model: 'gpt-4o-mini',
  deepseek_api_key: '',
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  settings: defaultSettings,
  loaded: false,

  fetchSettings: async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      if (!backendUrl) return
      const res = await fetch(`${backendUrl}/api/settings`)
      if (res.ok) {
        const data = await res.json()
        set({ settings: { ...defaultSettings, ...data }, loaded: true })
      }
    } catch {
      set({ loaded: true })
    }
  },

  updateSettings: (updates) => {
    set({ settings: { ...get().settings, ...updates } })
  },

  saveSettings: async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      if (!backendUrl) return
      await fetch(`${backendUrl}/api/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(get().settings),
      })
    } catch (err) {
      console.error('Failed to save settings:', err)
    }
  },
}))
