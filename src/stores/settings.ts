import { create } from 'zustand'

interface AppSettings {
  api_key: string
  api_base_url: string
  stt_model: string
  stt_language: string
  stt_api_key: string
  stt_api_base_url: string
  llm_model: string
  llm_api_key: string
  llm_api_base_url: string
  llm_output_language: string
  audio_input_device: string
  audio_output_device: string
  ui_language: string
  // 录音默认配置
  recording_segmentation_strategy: string
  recording_max_segment_duration: string
  recording_fixed_chunk_duration: string
  recording_vad_threshold: string
  recording_vad_silence_frames: string
  recording_vad_speech_confirm_frames: string
  recording_vad_hangover_frames: string
}

interface SettingsState {
  settings: AppSettings
  loaded: boolean
  fetchSettings: () => Promise<void>
  updateSettings: (updates: Partial<AppSettings>) => void
  saveSettings: () => Promise<void>
}

const defaultSettings: AppSettings = {
  api_key: '',
  api_base_url: 'https://api.openai.com/v1',
  stt_model: 'whisper-1',
  stt_language: 'zh',
  stt_api_key: '',
  stt_api_base_url: 'https://api.openai.com/v1',
  llm_model: 'gpt-4o-mini',
  llm_api_key: '',
  llm_api_base_url: 'https://api.openai.com/v1',
  llm_output_language: 'en',
  audio_input_device: '',
  audio_output_device: '',
  ui_language: 'en',
  recording_segmentation_strategy: 'hybrid',
  recording_max_segment_duration: '15',
  recording_fixed_chunk_duration: '30',
  recording_vad_threshold: '0.6',
  recording_vad_silence_frames: '8',
  recording_vad_speech_confirm_frames: '3',
  recording_vad_hangover_frames: '3',
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
