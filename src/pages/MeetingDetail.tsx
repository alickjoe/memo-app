import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import MinutesPanel from '../components/MinutesPanel'
import TranscriptStream from '../components/TranscriptStream'
import { useSettingsStore } from '../stores/settings'

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

interface TranscriptSegment {
  speaker: string
  text: string
  start_time: number
  end_time: number
}

const ZH = {
  summary: '总结',
  transcript: '转写记录',
  regenerate: '重新生成',
  exportMd: '导出 Markdown',
  exportTxt: '导出 TXT',
  delete: '删除',
  untitled: '未命名会议',
  loading: '加载中...',
  generating: '正在生成会议纪要...',
  noMinutes: '暂无纪要内容',
  waitingTranscript: '等待转写内容...',
  noTranscript: '暂无转写记录',
  confirmDelete: '确定要删除此会议吗？相关录音和纪要将被永久删除。',
  durationMin: '分',
  durationSec: '秒',
  date: '日期',
  durationLabel: '时长',
  exportSummary: '摘要',
  exportKeyPoints: '关键讨论点',
  exportActionItems: '行动项',
  exportNextSteps: '下一步',
  exportTranscript: '转写记录',
  exportTitle: '会议纪要',
}

const EN: typeof ZH = {
  summary: 'Summary',
  transcript: 'Transcript',
  regenerate: 'Regenerate',
  exportMd: 'Export Markdown',
  exportTxt: 'Export TXT',
  delete: 'Delete',
  untitled: 'Untitled Meeting',
  loading: 'Loading...',
  generating: 'Generating meeting minutes...',
  noMinutes: 'No minutes yet',
  waitingTranscript: 'Waiting for transcript...',
  noTranscript: 'No transcript yet',
  confirmDelete: 'Are you sure you want to delete this meeting? All related recordings and minutes will be permanently deleted.',
  durationMin: 'min',
  durationSec: 's',
  date: 'Date',
  durationLabel: 'Duration',
  exportSummary: 'Summary',
  exportKeyPoints: 'Key Discussion Points',
  exportActionItems: 'Action Items',
  exportNextSteps: 'Next Steps',
  exportTranscript: 'Transcript',
  exportTitle: 'Meeting Minutes',
}

export default function MeetingDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const language = useSettingsStore((s) => s.settings.llm_output_language) || 'en'
  const t = language === 'zh' ? ZH : EN
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [minutes, setMinutes] = useState<Minutes | null>(null)
  const [transcripts, setTranscripts] = useState<TranscriptSegment[]>([])
  const [editingTitle, setEditingTitle] = useState(false)
  const [title, setTitle] = useState('')
  const [activeTab, setActiveTab] = useState<'summary' | 'transcript'>('summary')
  const wsRef = useRef<WebSocket | null>(null)
  const pollRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    loadMeeting()
    return () => {
      wsRef.current?.close()
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [id])

  // 监听 meeting 状态变化，建立 WebSocket / 轮询
  useEffect(() => {
    if (!meeting || !id) return
    if (meeting.status !== 'recording' && meeting.status !== 'processing') return

    // 清理之前的连接
    wsRef.current?.close()
    if (pollRef.current) clearInterval(pollRef.current)

    // WebSocket 监听 minutes_ready
    let ws: WebSocket | null = null
    let cancelled = false
    const connectWs = async () => {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      if (cancelled) return
      const wsUrl = backendUrl.replace('http', 'ws') + `/ws/transcript/${id}`
      ws = new WebSocket(wsUrl)
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data)
        if (data.type === 'minutes_ready') {
          loadMeeting()
          ws?.close()
          if (pollRef.current) clearInterval(pollRef.current)
        }
      }
      wsRef.current = ws
    }
    connectWs()

    // 兜底轮询：每 3 秒刷新
    pollRef.current = setInterval(async () => {
      try {
        const backendUrl = await window.electronAPI?.getBackendUrl()
        const res = await fetch(`${backendUrl}/api/meetings/${id}`)
        if (res.ok) {
          const data = await res.json()
          if (data.meeting.status === 'done' || data.meeting.status === 'error') {
            setMeeting(data.meeting)
            setMinutes(data.minutes)
            setTranscripts(data.transcripts || [])
            if (pollRef.current) clearInterval(pollRef.current)
            ws?.close()
          }
        }
      } catch { /* ignore */ }
    }, 3000)

    return () => {
      cancelled = true
      ws?.close()
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [meeting?.status, id])

  const loadMeeting = async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      const res = await fetch(`${backendUrl}/api/meetings/${id}`)
      if (res.ok) {
        const data = await res.json()
        setMeeting(data.meeting)
        setMinutes(data.minutes)
        setTranscripts(data.transcripts || [])
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

    const transcriptText = transcripts
      .map((seg) => `[${seg.speaker}] ${seg.text}`)
      .join('\n')

    if (type === 'markdown') {
      content = `# ${meeting?.title || t.exportTitle}\n\n`
      content += `**${t.date}**: ${meeting?.created_at}\n`
      content += `**${t.durationLabel}**: ${Math.floor((meeting?.duration_seconds || 0) / 60)} ${language === 'zh' ? '分钟' : 'min'}\n\n`
      content += `## ${t.exportSummary}\n${minutes.summary}\n\n`
      content += `## ${t.exportKeyPoints}\n${minutes.key_points.map((p: string) => `- ${p}`).join('\n')}\n\n`
      content += `## ${t.exportActionItems}\n${minutes.action_items.map((a: string) => `- [ ] ${a}`).join('\n')}\n\n`
      content += `## ${t.exportNextSteps}\n${minutes.next_steps}\n\n`
      if (transcriptText) {
        content += `## ${t.exportTranscript}\n${transcriptText}\n`
      }
    } else {
      content = `${meeting?.title || t.exportTitle}\n`
      content += `${t.date}: ${meeting?.created_at}\n`
      content += `${t.durationLabel}: ${Math.floor((meeting?.duration_seconds || 0) / 60)} ${language === 'zh' ? '分钟' : 'min'}\n\n`
      content += `${t.exportSummary}:\n${minutes.summary}\n\n`
      content += `${t.exportKeyPoints}:\n${minutes.key_points.map((p: string) => `- ${p}`).join('\n')}\n\n`
      content += `${t.exportActionItems}:\n${minutes.action_items.map((a: string) => `[ ] ${a}`).join('\n')}\n\n`
      content += `${t.exportNextSteps}:\n${minutes.next_steps}\n\n`
      if (transcriptText) {
        content += `${t.exportTranscript}:\n${transcriptText}\n`
      }
    }

    const blob = new Blob([content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${meeting?.title || 'meeting'}.${type === 'markdown' ? 'md' : 'txt'}`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleDelete = async () => {
    if (!id || !window.confirm(t.confirmDelete)) return
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      await fetch(`${backendUrl}/api/meetings/${id}`, { method: 'DELETE' })
      navigate('/')
    } catch (err) {
      console.error('Failed to delete meeting:', err)
    }
  }

  const formatDuration = (seconds: number): string => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}${t.durationMin}${s}${t.durationSec}`
  }

  if (!meeting) {
    return (
      <div className="flex items-center justify-center h-screen text-gray-400">
        {t.loading}
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
            {title || t.untitled}
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
          {t.regenerate}
        </button>
        <button
          onClick={() => handleExport('markdown')}
          className="px-3 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
        >
          {t.exportMd}
        </button>
        <button
          onClick={() => handleExport('txt')}
          className="px-3 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
        >
          {t.exportTxt}
        </button>
        <button
          onClick={handleDelete}
          className="px-3 py-1.5 text-sm border border-red-200 text-red-600 rounded-md hover:bg-red-50"
        >
          {t.delete}
        </button>
      </div>

      {/* 内容区 */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Tab 栏 */}
        <div className="bg-white border-b border-gray-200 px-6 flex gap-0">
          <button
            onClick={() => setActiveTab('summary')}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'summary'
                ? 'border-primary-500 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.summary}
          </button>
          <button
            onClick={() => setActiveTab('transcript')}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'transcript'
                ? 'border-primary-500 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.transcript}
            {transcripts.length > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 text-xs bg-gray-100 rounded-full">
                {transcripts.length}
              </span>
            )}
          </button>
        </div>

        {/* Tab 内容 */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {activeTab === 'summary' ? (
            meeting.status === 'processing' || meeting.status === 'recording' ? (
              <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-400 text-sm">
                <div className="w-6 h-6 border-2 border-primary-400 border-t-transparent rounded-full animate-spin" />
                {t.generating}
              </div>
            ) : minutes ? (
              <div className="max-w-3xl mx-auto">
                <MinutesPanel minutes={minutes} language={language} />
              </div>
            ) : (
              <div className="flex items-center justify-center h-full text-gray-400 text-sm">
                {t.noMinutes}
              </div>
            )
          ) : transcripts.length > 0 ? (
            <div className="max-w-3xl mx-auto">
              <TranscriptStream segments={transcripts} />
            </div>
          ) : meeting.status === 'recording' ? (
            <div className="flex items-center justify-center h-full text-gray-400 text-sm">
              {t.waitingTranscript}
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-gray-400 text-sm">
              {t.noTranscript}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
