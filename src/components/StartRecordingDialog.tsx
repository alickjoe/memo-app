import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'

export interface RecordingConfig {
  segmentation_strategy: string
  max_segment_duration: number
  fixed_chunk_duration: number
  vad_threshold: number
  vad_silence_frames: number
  vad_speech_confirm_frames: number
  vad_hangover_frames: number
}

interface AudioDevice {
  id: string
  name: string
  is_loopback: boolean
}

interface SignalDevice {
  device_id: string
  device_name: string
  has_signal: boolean
  rms_level: number
  is_loopback: boolean
}

interface Props {
  defaultConfig: RecordingConfig
  defaultLoopbackDevice: string
  defaultInputDevice: string
  onStart: (config: {
    loopback_device_id: string | null
    input_device_id: string | null
    config: RecordingConfig
  }) => void
  onCancel: () => void
}

const STRATEGY_OPTIONS = [
  { value: 'vad', labelKey: 'recording.strategyVad' },
  { value: 'hybrid', labelKey: 'recording.strategyHybrid' },
  { value: 'fixed', labelKey: 'recording.strategyFixed' },
]

export default function StartRecordingDialog({
  defaultConfig,
  defaultLoopbackDevice,
  defaultInputDevice,
  onStart,
  onCancel,
}: Props) {
  const { t } = useTranslation()
  const [useDefaults, setUseDefaults] = useState(true)
  const [config, setConfig] = useState<RecordingConfig>({ ...defaultConfig })
  const [loopbackDevice, setLoopbackDevice] = useState(defaultLoopbackDevice || '')
  const [inputDevice, setInputDevice] = useState(defaultInputDevice || '')
  const [devices, setDevices] = useState<AudioDevice[]>([])
  const [scanning, setScanning] = useState(false)
  const [signalDevices, setSignalDevices] = useState<SignalDevice[]>([])

  useEffect(() => {
    loadDevices()
  }, [])

  // 当切换"使用默认"时，重置配置
  useEffect(() => {
    if (useDefaults) {
      setConfig({ ...defaultConfig })
      setLoopbackDevice(defaultLoopbackDevice || '')
      setInputDevice(defaultInputDevice || '')
    }
  }, [useDefaults, defaultConfig, defaultLoopbackDevice, defaultInputDevice])

  const loadDevices = async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      const res = await fetch(`${backendUrl}/api/audio/devices`)
      if (res.ok) {
        const data = await res.json()
        setDevices(data.devices || [])
      }
    } catch (err) {
      console.error('Failed to load devices:', err)
    }
  }

  const handleScanDevices = async () => {
    setScanning(true)
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      const res = await fetch(`${backendUrl}/api/audio/scan-devices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_type: 'all', duration: 1.5 }),
      })
      if (res.ok) {
        const data = await res.json()
        setSignalDevices(data.devices || [])
      }
    } catch (err) {
      console.error('Failed to scan devices:', err)
    } finally {
      setScanning(false)
    }
  }

  const handleStart = useCallback(() => {
    onStart({
      loopback_device_id: loopbackDevice || null,
      input_device_id: inputDevice || null,
      config,
    })
  }, [loopbackDevice, inputDevice, config, onStart])

  const updateConfig = (key: keyof RecordingConfig, value: number | string) => {
    setConfig((prev) => ({ ...prev, [key]: value }))
  }

  const loopbackDevices = devices.filter((d) => d.is_loopback)
  const inputDevices = devices.filter((d) => !d.is_loopback)

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto mx-4">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-900">{t('recording.startDialog')}</h2>
          <p className="text-sm text-gray-500 mt-0.5">{t('recording.startDialogDesc')}</p>
        </div>

        <div className="px-6 py-4 space-y-5">
          {/* 使用默认配置切换 */}
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={useDefaults}
              onChange={(e) => setUseDefaults(e.target.checked)}
              className="w-4 h-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
            />
            <span className="text-sm text-gray-700">{t('recording.useDefaults')}</span>
          </label>

          {/* 分段策略 */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              {t('recording.segmentationStrategy')}
            </label>
            <select
              value={config.segmentation_strategy}
              onChange={(e) => updateConfig('segmentation_strategy', e.target.value)}
              disabled={useDefaults}
              className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400 disabled:bg-gray-50 disabled:text-gray-400"
            >
              {STRATEGY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {t(opt.labelKey)}
                </option>
              ))}
            </select>
            <p className="text-xs text-gray-400 mt-1">{t('recording.strategyHint')}</p>
          </div>

          {/* 最大分段时长 (VAD/Hybrid) */}
          {(config.segmentation_strategy === 'vad' || config.segmentation_strategy === 'hybrid') && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                {t('recording.maxSegmentDuration')}: {config.max_segment_duration}s
              </label>
              <input
                type="range"
                min={5}
                max={60}
                step={5}
                value={config.max_segment_duration}
                onChange={(e) => updateConfig('max_segment_duration', Number(e.target.value))}
                disabled={useDefaults}
                className="w-full accent-primary-600 disabled:opacity-40"
              />
              <div className="flex justify-between text-xs text-gray-400">
                <span>5s</span>
                <span>60s</span>
              </div>
            </div>
          )}

          {/* 固定分段时长 (Fixed) */}
          {config.segmentation_strategy === 'fixed' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                {t('recording.fixedChunkDuration')}: {config.fixed_chunk_duration}s
              </label>
              <input
                type="range"
                min={10}
                max={120}
                step={10}
                value={config.fixed_chunk_duration}
                onChange={(e) => updateConfig('fixed_chunk_duration', Number(e.target.value))}
                disabled={useDefaults}
                className="w-full accent-primary-600 disabled:opacity-40"
              />
              <div className="flex justify-between text-xs text-gray-400">
                <span>10s</span>
                <span>120s</span>
              </div>
            </div>
          )}

          {/* VAD 灵敏度 */}
          {(config.segmentation_strategy === 'vad' || config.segmentation_strategy === 'hybrid') && (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">
                  {t('recording.vadSensitivity')}: {config.vad_threshold.toFixed(1)}
                </label>
                <input
                  type="range"
                  min={0.3}
                  max={0.9}
                  step={0.1}
                  value={config.vad_threshold}
                  onChange={(e) => updateConfig('vad_threshold', Number(e.target.value))}
                  disabled={useDefaults}
                  className="w-full accent-primary-600 disabled:opacity-40"
                />
                <div className="flex justify-between text-xs text-gray-400">
                  <span>{t('recording.sensitive')} (0.3)</span>
                  <span>{t('recording.strict')} (0.9)</span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    {t('recording.silenceFrames')}
                  </label>
                  <input
                    type="number"
                    min={3}
                    max={20}
                    value={config.vad_silence_frames}
                    onChange={(e) => updateConfig('vad_silence_frames', Number(e.target.value))}
                    disabled={useDefaults}
                    className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400 disabled:bg-gray-50"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    {t('recording.hangoverFrames')}
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={config.vad_hangover_frames}
                    onChange={(e) => updateConfig('vad_hangover_frames', Number(e.target.value))}
                    disabled={useDefaults}
                    className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400 disabled:bg-gray-50"
                  />
                </div>
              </div>
            </>
          )}

          {/* 音频设备选择 */}
          <div className="border-t border-gray-100 pt-4">
            <h3 className="text-sm font-semibold text-gray-700 mb-3">{t('recording.audioDevices')}</h3>

            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">
                  {t('recording.outputDevice')}
                </label>
                <select
                  value={loopbackDevice}
                  onChange={(e) => setLoopbackDevice(e.target.value)}
                  disabled={useDefaults}
                  className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400 disabled:bg-gray-50"
                >
                  <option value="">{t('settings.systemDefault')}</option>
                  {loopbackDevices.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">
                  {t('recording.inputDevice')}
                </label>
                <select
                  value={inputDevice}
                  onChange={(e) => setInputDevice(e.target.value)}
                  disabled={useDefaults}
                  className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400 disabled:bg-gray-50"
                >
                  <option value="">{t('settings.systemDefault')}</option>
                  {inputDevices.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* 设备信号扫描 */}
            <div className="mt-3">
              <button
                onClick={handleScanDevices}
                disabled={scanning}
                className="text-sm text-primary-600 hover:text-primary-700 disabled:text-gray-400"
              >
                {scanning ? t('recording.scanning') : t('recording.scanDevices')}
              </button>

              {signalDevices.length > 0 && (
                <div className="mt-2 space-y-1 max-h-32 overflow-y-auto">
                  {signalDevices.map((d) => (
                    <button
                      key={d.device_id}
                      onClick={() => {
                        if (d.is_loopback) setLoopbackDevice(d.device_id)
                        else setInputDevice(d.device_id)
                      }}
                      className={`w-full text-left px-3 py-1.5 rounded text-xs flex items-center justify-between ${
                        d.has_signal
                          ? 'bg-green-50 text-green-700 hover:bg-green-100'
                          : 'bg-gray-50 text-gray-400'
                      }`}
                    >
                      <span className="truncate flex-1">{d.device_name || d.name}</span>
                      <span className="ml-2 shrink-0">
                        {d.has_signal ? `✓ RMS:${d.rms_level.toFixed(4)}` : '✗'}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-100 flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 border border-gray-200 rounded-md"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleStart}
            className="px-5 py-2 text-sm bg-primary-600 text-white rounded-md hover:bg-primary-700"
          >
            {t('recording.startNow')}
          </button>
        </div>
      </div>
    </div>
  )
}
