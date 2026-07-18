import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import MeetingCard from '../components/MeetingCard'
import { useMeetingStore } from '../stores/meetings'
import { useSettingsStore } from '../stores/settings'
import type { Meeting } from '../stores/meetings'

export default function Dashboard() {
  const navigate = useNavigate()
  const { meetings, fetchMeetings, loading, deleteMeeting } = useMeetingStore()
  const { settings, fetchSettings } = useSettingsStore()
  const [dragOver, setDragOver] = useState(false)

  useEffect(() => {
    fetchMeetings()
    fetchSettings()
  }, [fetchMeetings, fetchSettings])

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
    if (!window.confirm('确定要删除此会议吗？相关录音和纪要将被永久删除。')) return
    await deleteMeeting(meetingId)
  }

  return (
    <div className="flex h-screen bg-gray-50">
      {/* 侧边栏 */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col">
        <div className="p-4 border-b border-gray-100">
          <h1 className="text-lg font-bold text-primary-600">Memo</h1>
          <p className="text-xs text-gray-400 mt-0.5">会议纪要</p>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          <a href="#" className="flex items-center gap-2 px-3 py-2 text-sm rounded-md bg-primary-50 text-primary-700 font-medium">
            所有会议
          </a>
        </nav>
        <div className="p-3 border-t border-gray-100">
          <button
            onClick={() => navigate('/settings')}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-500 hover:text-gray-700 rounded-md w-full"
          >
            设置
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
              开始录制
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
              拖拽音频文件到此处导入，或点击选择文件
            </div>
          </div>
        </div>

        {/* 会议列表 */}
        <div className="px-6 pb-6">
          <h2 className="text-sm font-medium text-gray-500 mb-3">
            会议记录 ({meetings.length})
          </h2>
          {loading ? (
            <div className="text-center text-gray-400 py-12">加载中...</div>
          ) : meetings.length === 0 ? (
            <div className="text-center text-gray-400 py-12">
              <p className="text-lg mb-1">暂无会议记录</p>
              <p className="text-sm">点击"开始录制"或拖入音频文件开始</p>
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
