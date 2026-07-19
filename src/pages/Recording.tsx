import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

interface TranscriptSegment {
  speaker: string
  text: string
  start_time: number
  end_time: number
}

interface AudioDevice {
  id: string
  name: string
  is_loopback: boolean
  channels: number
}

interface RecordingConfig {
  segmentation_strategy: string
  max_segment_duration: number
  fixed_chunk_duration: number
  vad_threshold: number
}

export default function Recording() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()

  // 录制状态
  const [duration, setDuration] = useState(0)
  const [isPaused, setIsPaused] = useState(false)
  const [transcripts, setTranscripts] = useState<TranscriptSegment[]>([])
  const [deviceNotification, setDeviceNotification] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const durationRef = useRef<NodeJS.Timeout | null>(null)
  const signalPollRef = useRef<NodeJS.Timeout | null>(null)
  const recordingRef = useRef(true)  // 用于 poll 守卫，停止后跳过请求
  const backendUrlRef = useRef<string>('')

  // 设备面板状态
  const [devices, setDevices] = useState<AudioDevice[]>([])
  const [loopbackDevice, setLoopbackDevice] = useState('')
  const [inputDevice, setInputDevice] = useState('')
  const [loopbackDeviceName, setLoopbackDeviceName] = useState('')
  const [inputDeviceName, setInputDeviceName] = useState('')
  const [loopbackRms, setLoopbackRms] = useState(0)
  const [micRms, setMicRms] = useState(0)
  const [showDevicePanel, setShowDevicePanel] = useState(true)

  // 只读配置
  const [config, setConfig] = useState<RecordingConfig | null>(null)

  // 初始化：获取 backend URL，启动设备加载和信号轮询
  useEffect(() => {
    window.electronAPI?.getBackendUrl().then((url: string) => {
      backendUrlRef.current = url
      loadDevices(url)
      signalPollRef.current = startSignalPolling(url)
    })
    return () => {
      if (signalPollRef.current) {
        clearInterval(signalPollRef.current)
        signalPollRef.current = null
      }
    }
  }, [])

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
      const url = backendUrlRef.current || await window.electronAPI?.getBackendUrl()
      if (cancelled || !url) return
      const wsUrl = url.replace('http', 'ws') + `/ws/transcript/${id}`
      ws = new WebSocket(wsUrl)

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data)
        if (data.type === 'transcript') {
          setTranscripts((prev) => [...prev, data.segment])
        } else if (data.type === 'device_switched') {
          const msg = `${t('recording.deviceSwitched')}: ${data.old_device} -> ${data.new_device}`
          setDeviceNotification(msg)
          setTimeout(() => setDeviceNotification(null), 5000)
          // 刷新当前设备信息
          if (data.device_type === 'loopback') {
            setLoopbackDeviceName(data.new_device)
          } else {
            setInputDeviceName(data.new_device)
          }
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

  // 加载设备列表
  const loadDevices = useCallback(async (baseUrl?: string) => {
    try {
      const url = baseUrl || backendUrlRef.current
      if (!url) return
      const res = await fetch(`${url}/api/audio/devices`)
      if (res.ok) {
        const data = await res.json()
        setDevices(data.devices || [])
      }
    } catch (err) {
      console.error('Failed to load devices:', err)
    }
  }, [])

  // 轮询信号强度（返回 interval ID 供清理）
  const startSignalPolling = useCallback((baseUrl?: string): NodeJS.Timeout => {
    const poll = async () => {
      if (!recordingRef.current) return  // 停止后跳过
      try {
        const url = baseUrl || backendUrlRef.current
        if (!url) return
        const res = await fetch(`${url}/api/audio/signal-status`)
        if (res.ok) {
          const data = await res.json()
          setLoopbackRms(data.loopback_rms || 0)
          setMicRms(data.mic_rms || 0)
        }
      } catch {
        // ignore polling errors
      }
    }

    poll()
    return setInterval(poll, 1000)
  }, [])

  // 切换设备
  const handleSwitchDevice = useCallback(async (deviceType: string, deviceId: string) => {
    try {
      const url = backendUrlRef.current
      if (!url) return
      const res = await fetch(`${url}/api/record/switch-device`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_type: deviceType, device_id: deviceId }),
      })
      if (res.ok) {
        const data = await res.json()
        if (deviceType === 'loopback') {
          setLoopbackDevice(deviceId)
          setLoopbackDeviceName(data.device_name || '')
        } else {
          setInputDevice(deviceId)
          setInputDeviceName(data.device_name || '')
        }
      }
    } catch (err) {
      console.error('Failed to switch device:', err)
    }
  }, [])

  const formatDuration = (seconds: number): string => {
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = seconds % 60
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    return `${m}:${String(s).padStart(2, '0')}`
  }

  const handlePause = async () => {
    const url = backendUrlRef.current
    if (!url) return
    if (isPaused) {
      await fetch(`${url}/api/record/resume`, { method: 'POST' })
    } else {
      await fetch(`${url}/api/record/pause`, { method: 'POST' })
    }
    setIsPaused(!isPaused)
  }

  const handleStop = async () => {
    recordingRef.current = false  // 先标记停止，阻止后续 poll
    const url = backendUrlRef.current
    if (!url) return
    await fetch(`${url}/api/record/stop`, { method: 'POST' })
    if (durationRef.current) clearInterval(durationRef.current)
    if (signalPollRef.current) {
      clearInterval(signalPollRef.current)
      signalPollRef.current = null
    }
    wsRef.current?.close()
    navigate(`/meeting/${id}`)
  }

  const loopbackDevices = devices.filter((d) => d.is_loopback)
  const inputDevices = devices.filter((d) => !d.is_loopback)

  // RMS 信号条渲染
  const renderRmsBar = (rms: number, color: string) => {
    const pct = Math.min(100, Math.round(rms * 10000))
    return (
      <div className="w-16 h-1.5 bg-gray-100 rounded-full overflow-hidden shrink-0">
        <div
          className={`h-full rounded-full transition-all duration-300 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* 状态条 */}
      <div className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className={`w-2.5 h-2.5 rounded-full ${isPaused ? 'bg-yellow-400' : 'bg-red-500 animate-pulse'}`} />
            <span className="text-sm font-medium text-gray-700">
              {isPaused ? t('recording.paused') : t('recording.recording')}
            </span>
          </div>
          <span className="text-lg font-mono text-gray-900 tabular-nums">
            {formatDuration(duration)}
          </span>
          {/* 折叠按钮 */}
          <button
            onClick={() => setShowDevicePanel(!showDevicePanel)}
            className="ml-2 text-xs text-gray-400 hover:text-gray-600"
          >
            {showDevicePanel ? '\u25B2' : '\u25BC'} {t('recording.audioDevices')}
          </button>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handlePause}
            className="px-3 py-1.5 text-sm border border-gray-200 rounded-md hover:bg-gray-50"
          >
            {isPaused ? t('recording.resume') : t('recording.pause')}
          </button>
          <button
            onClick={handleStop}
            className="px-4 py-1.5 text-sm bg-red-500 text-white rounded-md hover:bg-red-600"
          >
            {t('recording.stopRecording')}
          </button>
        </div>
      </div>

      {/* 设备切换通知 */}
      {deviceNotification && (
        <div className="bg-amber-50 border-b border-amber-200 px-6 py-2 text-sm text-amber-700">
          {deviceNotification}
        </div>
      )}

      {/* 设备选择面板（可折叠） */}
      {showDevicePanel && (
        <div className="bg-white border-b border-gray-200 px-6 py-3">
          <div className="flex items-center gap-6 flex-wrap">
            {/* 系统音频（loopback） */}
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-gray-500 whitespace-nowrap">
                {t('audio.systemAudio')}
              </label>
              <select
                value={loopbackDevice}
                onChange={(e) => handleSwitchDevice('loopback', e.target.value)}
                className="px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:border-primary-400"
              >
                <option value="">{t('settings.systemDefault')}</option>
                {loopbackDevices.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
              {renderRmsBar(loopbackRms, 'bg-primary-400')}
              <span className="text-xs text-gray-400 w-12 text-right">
                {loopbackRms > 0.0001 ? loopbackRms.toFixed(4) : '-'}
              </span>
            </div>

            {/* 麦克风 */}
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-gray-500 whitespace-nowrap">
                {t('audio.microphone')}
              </label>
              <select
                value={inputDevice}
                onChange={(e) => handleSwitchDevice('input', e.target.value)}
                className="px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:border-primary-400"
              >
                <option value="">{t('settings.systemDefault')}</option>
                {inputDevices.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
              {renderRmsBar(micRms, 'bg-green-400')}
              <span className="text-xs text-gray-400 w-12 text-right">
                {micRms > 0.0001 ? micRms.toFixed(4) : '-'}
              </span>
            </div>
          </div>

          {/* 只读配置信息 */}
          {config && (
            <div className="mt-2 pt-2 border-t border-gray-100 flex items-center gap-4 text-xs text-gray-400">
              <span>{t('recording.segmentationStrategy')}: {config.segmentation_strategy}</span>
              <span>max: {config.max_segment_duration}s</span>
              {config.segmentation_strategy !== 'fixed' && (
                <span>VAD: {config.vad_threshold}</span>
              )}
            </div>
          )}
        </div>
      )}

      {/* 转写流 */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {transcripts.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            {t('recording.waitingAudio')}
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
