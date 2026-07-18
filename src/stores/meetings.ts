import { create } from 'zustand'

export interface Meeting {
  id: string
  title: string
  audio_path: string
  duration_seconds: number
  created_at: string
  status: 'recording' | 'processing' | 'done' | 'error'
}

interface MeetingState {
  meetings: Meeting[]
  loading: boolean
  fetchMeetings: () => Promise<void>
  addMeeting: (meeting: Meeting) => void
  updateMeeting: (id: string, updates: Partial<Meeting>) => void
  deleteMeeting: (id: string) => Promise<void>
}

export const useMeetingStore = create<MeetingState>((set, get) => ({
  meetings: [],
  loading: false,

  fetchMeetings: async () => {
    set({ loading: true })
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      if (!backendUrl) return
      const res = await fetch(`${backendUrl}/api/meetings`)
      if (res.ok) {
        const data = await res.json()
        set({ meetings: data.meetings || [], loading: false })
      }
    } catch {
      set({ loading: false })
    }
  },

  addMeeting: (meeting) => {
    set({ meetings: [meeting, ...get().meetings] })
  },

  updateMeeting: (id, updates) => {
    set({
      meetings: get().meetings.map((m) =>
        m.id === id ? { ...m, ...updates } : m
      ),
    })
  },

  deleteMeeting: async (id) => {
    const backendUrl = await window.electronAPI?.getBackendUrl()
    if (!backendUrl) return
    const res = await fetch(`${backendUrl}/api/meetings/${id}`, { method: 'DELETE' })
    if (res.ok) {
      set({ meetings: get().meetings.filter((m) => m.id !== id) })
    }
  },
}))
