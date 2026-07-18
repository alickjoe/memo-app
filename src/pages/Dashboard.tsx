import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import i18n from '../i18n/config'
import MeetingCard from '../components/MeetingCard'
import { useMeetingStore } from '../stores/meetings'
import { useSettingsStore } from '../stores/settings'
import type { Meeting } from '../stores/meetings'

export default function Dashboard() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { meetings, fetchMeetings, loading, deleteMeeting } = useMeetingStore()
  const { settings, fetchSettings } = useSettingsStore()
  const [dragOver, setDragOver] = useState(false)

  useEffect(() => {
    fetchMeetings()
    fetchSettings()
  }, [fetchMeetings, fetchSettings])

  // Sync i18n language with persisted ui_language setting
  useEffect(() => {
    if (settings.ui_language && settings.ui_language !== i18n.language) {
      i18n.changeLanguage(settings.ui_language)
    }
  }, [settings.ui_language])

  const handleStartRecording = async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      const res = await fetch(`${backendUrl}/api/record/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          loopback_device_id: settings.audio_output_device || null,
          input_device_id: settings.audio_input_device || null,
        }),
      })
      if (res.ok) {
        const data = await res.json()
        navigate(`/recording/${data.meeting_id}`)
      }
    } catch (err) {
      console.error('Failed to start recording:', err)
    }
  }

  const handleFileDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)

    const file = e.dataTransfer.files[0]
    if (file && file.type.startsWith('audio/')) {
      await importAudioFile(file.path)
    }
  }

  const handleFileSelect = async () => {
    const filePath = await window.electronAPI?.selectAudioFile()
    if (filePath) {
      await importAudioFile(filePath)
    }
  }

  const importAudioFile = async (filePath: string) => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      const formData = new FormData()
      // Note: actual file upload handled by backend API
      const res = await fetch(`${backendUrl}/api/import/audio`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: filePath }),
      })
      if (res.ok) {
        const data = await res.json()
        navigate(`/meeting/${data.meeting_id}`)
      }
    } catch (err) {
      console.error('Failed to import audio:', err)
    }
  }

  const handleDelete = async (meetingId: string) => {
    if (!window.confirm(t('dashboard.confirmDelete'))) return
    await deleteMeeting(meetingId)
  }

  return (
    <div className="flex h-screen bg-gray-50">
      {/* 侧边栏 */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col">
        <div className="p-4 border-b border-gray-100">
          <h1 className="text-lg font-bold text-primary-600">Memo</h1>
          <p className="text-xs text-gray-400 mt-0.5">{t('dashboard.subtitle')}</p>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          <a href="#" className="flex items-center gap-2 px-3 py-2 text-sm rounded-md bg-primary-50 text-primary-700 font-medium">
            {t('dashboard.allMeetings')}
          </a>
        </nav>
        <div className="p-3 border-t border-gray-100">
          <button
            onClick={() => navigate('/settings')}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-500 hover:text-gray-700 rounded-md w-full"
          >
            {t('dashboard.settings')}
          </button>
        </div>
      </aside>

      {/* 主内容区 */}
      <main className="flex-1 overflow-y-auto">
        {/* 顶部操作区 */}
        <div className="p-6 pb-4">
          <div className="flex items-center gap-4">
            <button
              onClick={handleStartRecording}
              className="px-5 py-2.5 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors font-medium text-sm shadow-sm"
            >
              {t('dashboard.startRecording')}
            </button>
            <div
              className={`flex-1 border-2 border-dashed rounded-lg p-4 text-center text-sm transition-colors ${
                dragOver
                  ? 'border-primary-400 bg-primary-50 text-primary-600'
                  : 'border-gray-200 text-gray-400 hover:border-gray-300'
              }`}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleFileDrop}
              onClick={handleFileSelect}
            >
              {t('dashboard.dragAudioHere')}
            </div>
          </div>
        </div>

        {/* 会议列表 */}
        <div className="px-6 pb-6">
          <h2 className="text-sm font-medium text-gray-500 mb-3">
            {t('dashboard.meetingRecords')} ({meetings.length})
          </h2>
          {loading ? (
            <div className="text-center text-gray-400 py-12">{t('common.loading')}</div>
          ) : meetings.length === 0 ? (
            <div className="text-center text-gray-400 py-12">
              <p className="text-lg mb-1">{t('dashboard.noMeetings')}</p>
              <p className="text-sm">{t('dashboard.noMeetingsHint')}</p>
            </div>
          ) : (
            <div className="space-y-2">
              {meetings.map((meeting) => (
                <MeetingCard
                  key={meeting.id}
                  meeting={meeting}
                  onClick={() => navigate(`/meeting/${meeting.id}`)}
                  onDelete={() => handleDelete(meeting.id)}
                />
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
