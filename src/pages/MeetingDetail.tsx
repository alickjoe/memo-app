import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
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
  version?: number
}

export default function MeetingDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const uiLanguage = useSettingsStore((s) => s.settings.ui_language) || 'en'
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [minutes, setMinutes] = useState<Minutes | null>(null)
  const [transcripts, setTranscripts] = useState<TranscriptSegment[]>([])
  const [transcriptVersions, setTranscriptVersions] = useState<number[]>([])
  const [activeVersion, setActiveVersion] = useState<number>(0)
  const [editingTitle, setEditingTitle] = useState(false)
  const [title, setTitle] = useState('')
  const [activeTab, setActiveTab] = useState<'summary' | 'transcript'>('summary')
  const [isRetranscribing, setIsRetranscribing] = useState(false)
  const [pendingRetranscribeVersion, setPendingRetranscribeVersion] = useState<number | null>(null)
  const [retranscribeError, setRetranscribeError] = useState(false)
  const [retranscribeErrorMessage, setRetranscribeErrorMessage] = useState('')
  const wsRef = useRef<WebSocket | null>(null)
  const pollRef = useRef<NodeJS.Timeout | null>(null)
  const retranscribePollRef = useRef<NodeJS.Timeout | null>(null)
  const manualVersionRef = useRef(false)

  useEffect(() => {
    loadMeeting()
    return () => {
      wsRef.current?.close()
      if (pollRef.current) clearInterval(pollRef.current)
      if (retranscribePollRef.current) clearInterval(retranscribePollRef.current)
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
      if (cancelled || !backendUrl) return
      const wsUrl = backendUrl.replace('http', 'ws') + `/ws/transcript/${id}`
      ws = new WebSocket(wsUrl)
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data)
        if (data.type === 'retranscribe_done') {
          setPendingRetranscribeVersion(null)
          setIsRetranscribing(false)
          setRetranscribeError(false)
          setActiveVersion(data.version)
          manualVersionRef.current = false
          loadMeeting()
          ws?.close()
          if (pollRef.current) clearInterval(pollRef.current)
          if (retranscribePollRef.current) { clearInterval(retranscribePollRef.current); retranscribePollRef.current = null }
        } else if (data.type === 'minutes_ready') {
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
            loadMeeting()
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
        setTranscriptVersions(data.transcript_versions || [])
        // 自动选择最新版本（除非用户手动切换过）
        const versions: number[] = data.transcript_versions || []
        if (versions.length > 0 && !manualVersionRef.current) {
          setActiveVersion(Math.max(...versions))
        }
        setTitle(data.meeting.title)
        // 重转写完成检测：目标版本号已出现在转写列表中
        if (pendingRetranscribeVersion !== null && versions.includes(pendingRetranscribeVersion)) {
          setIsRetranscribing(false)
          setPendingRetranscribeVersion(null)
          setRetranscribeError(false)
          setActiveVersion(pendingRetranscribeVersion)
          manualVersionRef.current = false
        }
        // 重转写失败检测
        if (pendingRetranscribeVersion !== null && data.meeting.status === 'error') {
          setIsRetranscribing(false)
          setPendingRetranscribeVersion(null)
          setRetranscribeError(true)
        }
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

  const handleRetranscribe = async () => {
    if (!id || !window.confirm(t('meeting.confirmRetranscribe'))) return
    setIsRetranscribing(true)
    setRetranscribeError(false)
    setRetranscribeErrorMessage('')
    manualVersionRef.current = false
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      const res = await fetch(`${backendUrl}/api/meetings/${id}/retranscribe`, { method: 'POST' })
      if (res.ok) {
        const data = await res.json()
        setPendingRetranscribeVersion(data.version)
        if (retranscribePollRef.current) clearInterval(retranscribePollRef.current)
        setTimeout(() => loadMeeting(), 100)
        // 专用轮询：等待版本出现或失败，兜底 WebSocket
        retranscribePollRef.current = setInterval(async () => {
          try {
            const pollUrl = await window.electronAPI?.getBackendUrl()
            const pollRes = await fetch(`${pollUrl}/api/meetings/${id}`)
            if (pollRes.ok) {
              const pollData = await pollRes.json()
              const versions: number[] = pollData.transcript_versions || []
              // 成功：目标版本号出现在列表中
              if (versions.includes(data.version)) {
                setIsRetranscribing(false)
                setPendingRetranscribeVersion(null)
                setRetranscribeError(false)
                setActiveVersion(data.version)
                manualVersionRef.current = false
                loadMeeting()
                if (retranscribePollRef.current) { clearInterval(retranscribePollRef.current); retranscribePollRef.current = null }
                return
              }
              // 失败：状态变为 error
              if (pollData.meeting.status === 'error') {
                setIsRetranscribing(false)
                setPendingRetranscribeVersion(null)
                setRetranscribeError(true)
                setRetranscribeErrorMessage(t('meeting.retranscribeFailed'))
                if (retranscribePollRef.current) { clearInterval(retranscribePollRef.current); retranscribePollRef.current = null }
                return
              }
            }
          } catch { /* ignore */ }
        }, 2000)
      } else {
        const errData = await res.json().catch(() => ({ error: '' }))
        const msg = errData.error || ''
        setIsRetranscribing(false)
        setRetranscribeError(true)
        setRetranscribeErrorMessage(
          msg.includes('No audio') ? t('meeting.retranscribeNoAudio')
          : msg.includes('not found') ? t('meeting.retranscribeNoAudio')
          : t('meeting.retranscribeFailed')
        )
      }
    } catch (err) {
      console.error('Failed to retranscribe:', err)
      setIsRetranscribing(false)
      setRetranscribeError(true)
      setRetranscribeErrorMessage(t('meeting.retranscribeFailed'))
    }
  }

  const handleExport = (type: 'markdown' | 'txt') => {
    if (!minutes) return
    let content = ''

    const transcriptText = filteredTranscripts
      .map((seg) => `[${seg.speaker}] ${seg.text}`)
      .join('\n')

    if (type === 'markdown') {
      content = `# ${meeting?.title || t('export.title')}\n\n`
      content += `**${t('meeting.date')}**: ${meeting?.created_at}\n`
      content += `**${t('meeting.duration')}**: ${Math.floor((meeting?.duration_seconds || 0) / 60)} ${uiLanguage === 'zh' ? '分钟' : 'min'}\n\n`
      content += `## ${t('export.summary')}\n${minutes.summary}\n\n`
      content += `## ${t('export.keyPoints')}\n${minutes.key_points.map((p: string) => `- ${p}`).join('\n')}\n\n`
      content += `## ${t('export.actionItems')}\n${minutes.action_items.map((a: string) => `- [ ] ${a}`).join('\n')}\n\n`
      content += `## ${t('export.nextSteps')}\n${minutes.next_steps}\n\n`
      if (transcriptText) {
        content += `## ${t('export.transcript')}\n${transcriptText}\n`
      }
    } else {
      content = `${meeting?.title || t('export.title')}\n`
      content += `${t('meeting.date')}: ${meeting?.created_at}\n`
      content += `${t('meeting.duration')}: ${Math.floor((meeting?.duration_seconds || 0) / 60)} ${uiLanguage === 'zh' ? '分钟' : 'min'}\n\n`
      content += `${t('export.summary')}:\n${minutes.summary}\n\n`
      content += `${t('export.keyPoints')}:\n${minutes.key_points.map((p: string) => `- ${p}`).join('\n')}\n\n`
      content += `${t('export.actionItems')}:\n${minutes.action_items.map((a: string) => `[ ] ${a}`).join('\n')}\n\n`
      content += `${t('export.nextSteps')}:\n${minutes.next_steps}\n\n`
      if (transcriptText) {
        content += `${t('export.transcript')}:\n${transcriptText}\n`
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
    if (!id || !window.confirm(t('meeting.confirmDelete'))) return
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      await fetch(`${backendUrl}/api/meetings/${id}`, { method: 'DELETE' })
      navigate('/')
    } catch (err) {
      console.error('Failed to delete meeting:', err)
    }
  }

  const formatDateTime = (dateStr: string): string => {
    const d = new Date(dateStr)
    const locale = uiLanguage === 'zh' ? 'zh-CN' : 'en-US'
    const datePart = d.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
    const timePart = d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
    return `${datePart} ${timePart}`
  }

  const formatDuration = (seconds: number): string => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}${t('meeting.durationMin')}${s}${t('meeting.durationSec')}`
  }

  // 根据 activeVersion 过滤转写记录
  const filteredTranscripts = activeVersion > 0
    ? transcripts.filter((seg) => (seg.version || 1) === activeVersion)
    : transcripts

  if (!meeting) {
    return (
      <div className="flex items-center justify-center h-screen text-gray-400">
        {t('meeting.loading')}
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
            {title || t('meeting.untitled')}
          </h1>
        )}
        <span className="text-sm text-gray-400">
          {formatDateTime(meeting.created_at)} &middot; {formatDuration(meeting.duration_seconds)}
        </span>
        <div className="flex-1" />
        {meeting.status === 'done' && transcriptVersions.length > 1 && (
          <select
            value={activeVersion}
            onChange={(e) => {
              setActiveVersion(Number(e.target.value))
              manualVersionRef.current = true
            }}
            className="px-2 py-1.5 text-sm border border-gray-200 rounded-md bg-white"
          >
            {transcriptVersions.map((v) => (
              <option key={v} value={v}>
                {v === 1 ? `${t('meeting.version')} ${v} (${t('meeting.versionOriginal')})` : `${t('meeting.version')} ${v}`}
              </option>
            ))}
          </select>
        )}
        <button
          onClick={handleRegenerate}
          className="px-3 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
        >
          {t('meeting.regenerate')}
        </button>
        <button
          onClick={handleRetranscribe}
          disabled={isRetranscribing || meeting.status === 'processing' || meeting.status === 'recording'}
          className="px-3 py-1.5 text-sm border border-primary-200 text-primary-600 rounded-md hover:bg-primary-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isRetranscribing ? t('meeting.retranscribing') : t('meeting.retranscribe')}
        </button>
        <button
          onClick={() => handleExport('markdown')}
          className="px-3 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
        >
          {t('meeting.exportMd')}
        </button>
        <button
          onClick={() => handleExport('txt')}
          className="px-3 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
        >
          {t('meeting.exportTxt')}
        </button>
        <button
          onClick={handleDelete}
          className="px-3 py-1.5 text-sm border border-red-200 text-red-600 rounded-md hover:bg-red-50"
        >
          {t('meeting.delete')}
        </button>
      </div>

      {/* 重转写状态提示栏 */}
      {isRetranscribing && pendingRetranscribeVersion !== null && (
        <div className="bg-blue-50 border-b border-blue-200 px-6 py-2.5 flex items-center gap-3">
          <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-blue-700 font-medium">
            {t('meeting.retranscribingStatus')} (v{pendingRetranscribeVersion})
          </span>
          <span className="text-xs text-blue-500">
            {t('meeting.retranscribeHint')}
          </span>
        </div>
      )}
      {retranscribeError && (
        <div className="bg-red-50 border-b border-red-200 px-6 py-2.5 flex items-center gap-3">
          <span className="text-sm text-red-700">{retranscribeErrorMessage || t('meeting.retranscribeFailed')}</span>
          <button
            onClick={() => { setRetranscribeError(false); setRetranscribeErrorMessage('') }}
            className="ml-auto text-xs text-red-500 hover:text-red-700 underline"
          >
            {t('meeting.dismissError')}
          </button>
        </div>
      )}

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
            {t('meeting.summary')}
          </button>
          <button
            onClick={() => setActiveTab('transcript')}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'transcript'
                ? 'border-primary-500 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t('meeting.transcript')}
            {filteredTranscripts.length > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 text-xs bg-gray-100 rounded-full">
                {filteredTranscripts.length}
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
                {t('meeting.generating')}
              </div>
            ) : minutes ? (
              <div className="max-w-3xl mx-auto">
                <MinutesPanel minutes={minutes} />
              </div>
            ) : (
              <div className="flex items-center justify-center h-full text-gray-400 text-sm">
                {t('meeting.noMinutes')}
              </div>
            )
          ) : transcripts.length > 0 ? (
            <div className="max-w-3xl mx-auto">
              <TranscriptStream segments={filteredTranscripts} />
            </div>
          ) : meeting.status === 'recording' ? (
            <div className="flex items-center justify-center h-full text-gray-400 text-sm">
              {t('meeting.waitingTranscript')}
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-gray-400 text-sm">
              {t('meeting.noTranscript')}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
