import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'

interface TranscriptSegment {
  speaker: string
  text: string
  start_time: number
  end_time: number
}

export default function Recording() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [duration, setDuration] = useState(0)
  const [isPaused, setIsPaused] = useState(false)
  const [transcripts, setTranscripts] = useState<TranscriptSegment[]>([])
  const wsRef = useRef<WebSocket | null>(null)
  const durationRef = useRef<NodeJS.Timeout | null>(null)

  // 计时器
  useEffect(() => {
    durationRef.current = setInterval(() => {
      setDuration((d) => d + 1)
    }, 1000)
    return () => {
      if (durationRef.current) clearInterval(durationRef.current)
    }
  }, [])

  // WebSocket 连接
  useEffect(() => {
    let ws: WebSocket | null = null
    let cancelled = false

    async function connectWS() {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      if (cancelled) return
      const wsUrl = backendUrl.replace('http', 'ws') + `/ws/transcript/${id}`
      ws = new WebSocket(wsUrl)

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data)
        if (data.type === 'transcript') {
          setTranscripts((prev) => [...prev, data.segment])
        }
      }

      ws.onerror = (err) => console.error('WebSocket error:', err)
      wsRef.current = ws
    }

    connectWS()
    return () => {
      cancelled = true
      ws?.close()
    }
  }, [id])

  const formatDuration = (seconds: number): string => {
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = seconds % 60
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    return `${m}:${String(s).padStart(2, '0')}`
  }

  const handlePause = async () => {
    const backendUrl = await window.electronAPI?.getBackendUrl()
    if (isPaused) {
      await fetch(`${backendUrl}/api/record/resume`, { method: 'POST' })
    } else {
      await fetch(`${backendUrl}/api/record/pause`, { method: 'POST' })
    }
    setIsPaused(!isPaused)
  }

  const handleStop = async () => {
    const backendUrl = await window.electronAPI?.getBackendUrl()
    await fetch(`${backendUrl}/api/record/stop`, { method: 'POST' })
    if (durationRef.current) clearInterval(durationRef.current)
    wsRef.current?.close()
    navigate(`/meeting/${id}`)
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* 状态条 */}
      <div className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className={`w-2.5 h-2.5 rounded-full ${isPaused ? 'bg-yellow-400' : 'bg-red-500 animate-pulse'}`} />
            <span className="text-sm font-medium text-gray-700">
              {isPaused ? '已暂停' : '录制中'}
            </span>
          </div>
          <span className="text-lg font-mono text-gray-900 tabular-nums">
            {formatDuration(duration)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handlePause}
            className="px-3 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
          >
            {isPaused ? '继续' : '暂停'}
          </button>
          <button
            onClick={handleStop}
            className="px-4 py-1.5 text-sm bg-red-500 text-white rounded-md hover:bg-red-600"
          >
            结束录制
          </button>
        </div>
      </div>

      {/* 转写流 */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {transcripts.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            等待语音输入...
          </div>
        ) : (
          <div className="space-y-3 max-w-2xl mx-auto">
            {transcripts.map((seg, idx) => (
              <div key={idx} className="flex gap-3 items-start">
                <span className={`text-xs font-medium px-1.5 py-0.5 rounded mt-0.5 ${
                  seg.speaker === 'Speaker A'
                    ? 'bg-blue-100 text-blue-700'
                    : 'bg-green-100 text-green-700'
                }`}>
                  {seg.speaker}
                </span>
                <p className="text-sm text-gray-700 leading-relaxed">{seg.text}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
