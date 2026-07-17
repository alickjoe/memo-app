import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import MinutesPanel from '../components/MinutesPanel'

interface Meeting {
  id: string
  title: string
  audio_path: string
  duration_seconds: number
  created_at: string
  status: string
}

interface Minutes {
  summary: string
  key_points: string[]
  action_items: string[]
  next_steps: string
}

export default function MeetingDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [minutes, setMinutes] = useState<Minutes | null>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [title, setTitle] = useState('')

  useEffect(() => {
    loadMeeting()
  }, [id])

  const loadMeeting = async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      const res = await fetch(`${backendUrl}/api/meetings/${id}`)
      if (res.ok) {
        const data = await res.json()
        setMeeting(data.meeting)
        setMinutes(data.minutes)
        setTitle(data.meeting.title)
      }
    } catch (err) {
      console.error('Failed to load meeting:', err)
    }
  }

  const handleRegenerate = async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      await fetch(`${backendUrl}/api/meetings/${id}/regenerate`, { method: 'POST' })
      setTimeout(loadMeeting, 2000)
    } catch (err) {
      console.error('Failed to regenerate:', err)
    }
  }

  const handleExport = (type: 'markdown' | 'txt') => {
    if (!minutes) return
    let content = ''
    if (type === 'markdown') {
      content = `# ${meeting?.title || '会议纪要'}\n\n`
      content += `**日期**: ${meeting?.created_at}\n`
      content += `**时长**: ${Math.floor((meeting?.duration_seconds || 0) / 60)} 分钟\n\n`
      content += `## 摘要\n${minutes.summary}\n\n`
      content += `## 关键讨论点\n${minutes.key_points.map((p: string) => `- ${p}`).join('\n')}\n\n`
      content += `## 行动项\n${minutes.action_items.map((a: string) => `- [ ] ${a}`).join('\n')}\n\n`
      content += `## 下一步\n${minutes.next_steps}\n`
    }

    const blob = new Blob([content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${meeting?.title || 'meeting'}.${type === 'markdown' ? 'md' : 'txt'}`
    a.click()
    URL.revokeObjectURL(url)
  }

  const formatDuration = (seconds: number): string => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}分${s}秒`
  }

  if (!meeting) {
    return (
      <div className="flex items-center justify-center h-screen text-gray-400">
        加载中...
      </div>
    )
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* 顶部栏 */}
      <div className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-4">
        <button
          onClick={() => navigate('/')}
          className="text-gray-400 hover:text-gray-600"
        >
          &larr;
        </button>
        {editingTitle ? (
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => setEditingTitle(false)}
            onKeyDown={(e) => e.key === 'Enter' && setEditingTitle(false)}
            className="text-lg font-semibold bg-transparent border-b border-primary-400 outline-none"
            autoFocus
          />
        ) : (
          <h1
            className="text-lg font-semibold text-gray-900 cursor-pointer hover:text-primary-600"
            onClick={() => setEditingTitle(true)}
          >
            {title || '未命名会议'}
          </h1>
        )}
        <span className="text-sm text-gray-400">
          {meeting.created_at} &middot; {formatDuration(meeting.duration_seconds)}
        </span>
        <div className="flex-1" />
        <button
          onClick={handleRegenerate}
          className="px-3 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
        >
          重新生成
        </button>
        <button
          onClick={() => handleExport('markdown')}
          className="px-3 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
        >
          导出 Markdown
        </button>
        <button
          onClick={() => handleExport('txt')}
          className="px-3 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
        >
          导出 TXT
        </button>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {minutes ? (
          <MinutesPanel minutes={minutes} />
        ) : meeting.status === 'processing' ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            正在生成会议纪要...
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            暂无纪要内容
          </div>
        )}
      </div>
    </div>
  )
}
