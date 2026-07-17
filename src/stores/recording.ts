import { create } from 'zustand'

interface RecordingState {
  isRecording: boolean
  isPaused: boolean
  meetingId: string | null
  startRecording: (meetingId: string) => void
  pauseRecording: () => void
  resumeRecording: () => void
  stopRecording: () => void
}

export const useRecordingStore = create<RecordingState>((set) => ({
  isRecording: false,
  isPaused: false,
  meetingId: null,

  startRecording: (meetingId) => {
    set({ isRecording: true, isPaused: false, meetingId })
  },

  pauseRecording: () => {
    set({ isPaused: true })
  },

  resumeRecording: () => {
    set({ isPaused: false })
  },

  stopRecording: () => {
    set({ isRecording: false, isPaused: false, meetingId: null })
  },
}))
