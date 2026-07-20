import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import i18n from '../i18n/config'

export default function Settings() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [settings, setSettings] = useState({
    api_key: '',
    api_base_url: 'https://api.openai.com/v1',
    stt_model: 'whisper-1',
    stt_language: 'zh',
    stt_api_key: '',
    stt_api_base_url: 'https://api.openai.com/v1',
    llm_model: 'gpt-4o-mini',
    llm_api_key: '',
    llm_api_base_url: 'https://api.openai.com/v1',
    llm_output_language: 'en',
    audio_input_device: '',
    audio_output_device: '',
    ui_language: 'en',
    recording_segmentation_strategy: 'hybrid',
    recording_max_segment_duration: '15',
    recording_fixed_chunk_duration: '30',
    recording_vad_threshold: '0.6',
    recording_vad_silence_frames: '8',
    recording_vad_speech_confirm_frames: '3',
    recording_vad_hangover_frames: '3',
  })
  const [saved, setSaved] = useState(false)
  const [audioDevices, setAudioDevices] = useState<{ id: string; name: string; is_loopback: boolean }[]>([])
  const [torchStatus, setTorchStatus] = useState<{
    available: boolean
    version: string | null
    backend_mode: string
    vad_engine: string
    vad_error: string | null
  } | null>(null)
  const [torchInstalling, setTorchInstalling] = useState(false)
  const [torchMessage, setTorchMessage] = useState('')
  const [torchRestarting, setTorchRestarting] = useState(false)
  const [pythonInfo, setPythonInfo] = useState<{ source: 'managed' | 'system' | 'none'; path: string | null } | null>(null)
  const [uninstallingPython, setUninstallingPython] = useState(false)

  useEffect(() => {
    loadSettings()
    loadAudioDevices()
    loadTorchStatus()
    loadPythonInfo()
  }, [])

  const loadSettings = async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      const res = await fetch(`${backendUrl}/api/settings`)
      if (res.ok) {
        const data = await res.json()
        setSettings((prev) => ({ ...prev, ...data }))
        // Sync i18n language with persisted ui_language
        const lang = data.ui_language || 'en'
        if (lang !== i18n.language) {
          i18n.changeLanguage(lang)
        }
      }
    } catch (err) {
      console.error('Failed to load settings:', err)
    }
  }

  const loadAudioDevices = async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      const res = await fetch(`${backendUrl}/api/audio/devices`)
      if (res.ok) {
        const data = await res.json()
        setAudioDevices(data.devices || [])
      }
    } catch (err) {
      console.error('Failed to load audio devices:', err)
    }
  }

  const loadTorchStatus = async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      const res = await fetch(`${backendUrl}/api/system/torch-status`)
      if (res.ok) {
        const data = await res.json()
        setTorchStatus(data)
      }
    } catch (err) {
      console.error('Failed to load torch status:', err)
    }
  }

  const loadPythonInfo = async () => {
    try {
      const info = await window.electronAPI?.getPythonInfo()
      if (info) setPythonInfo(info)
    } catch {
      // ignore
    }
  }

  const handleUninstallManagedPython = async () => {
    setUninstallingPython(true)
    setTorchMessage('')
    try {
      const result = await window.electronAPI?.uninstallManagedPython()
      if (result) {
        setTorchMessage(result.success ? t('settings.vadUninstallSuccess') : `${t('settings.vadUninstallFailed')}: ${result.message}`)
        if (result.success) {
          setPythonInfo({ source: 'none', path: null })
        }
      }
    } catch (err: any) {
      setTorchMessage(`${t('settings.vadUninstallFailed')}: ${err.message || ''}`)
    } finally {
      setUninstallingPython(false)
    }
  }

  const handleInstallTorch = async () => {
    setTorchInstalling(true)
    setTorchMessage('')
    try {
      const result = await window.electronAPI?.installTorch()
      if (result) {
        if (result.success) {
          setTorchMessage(t('settings.vadInstallSuccess'))
        } else {
          const isNoPython = result.message.includes('Python not found')
          setTorchMessage(isNoPython ? t('settings.vadNoPython') : `${t('settings.vadInstallFailed')}: ${result.message}`)
        }
      }
    } catch (err: any) {
      setTorchMessage(`${t('settings.vadInstallFailed')}: ${err.message || ''}`)
    } finally {
      setTorchInstalling(false)
    }
  }

  const handleRestartBackend = async () => {
    setTorchRestarting(true)
    setTorchMessage('')
    try {
      await window.electronAPI?.restartBackend()
      // 后端已重启，但 VAD 引擎可能还未初始化完成，延迟并重试加载
      await new Promise((r) => setTimeout(r, 2000))
      let loaded = false
      for (let i = 0; i < 5; i++) {
        try {
          await loadTorchStatus()
          loaded = true
          break
        } catch {
          await new Promise((r) => setTimeout(r, 1500))
        }
      }
      if (loaded) {
        setTorchMessage(t('settings.vadRestartDone'))
      } else {
        setTorchMessage(t('settings.vadRestartFailed'))
      }
    } catch (err: any) {
      console.error('Failed to restart backend:', err)
      setTorchMessage(`${t('settings.vadRestartFailed')}: ${err.message || ''}`)
    } finally {
      setTorchRestarting(false)
    }
  }

  const handleUiLanguageChange = (lang: string) => {
    i18n.changeLanguage(lang)
    setSettings((prev) => ({
      ...prev,
      ui_language: lang,
      llm_output_language: lang,
    }))
  }

  const handleSave = async () => {
    try {
      const backendUrl = await window.electronAPI?.getBackendUrl()
      await fetch(`${backendUrl}/api/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (err) {
      console.error('Failed to save settings:', err)
    }
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      <div className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-4">
        <button onClick={() => navigate('/')} className="text-gray-400 hover:text-gray-600">&larr;</button>
        <h1 className="text-lg font-semibold">{t('settings.title')}</h1>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6 max-w-xl">
        {/* STT API 配置 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">{t('settings.sttConfig')}</h2>
          <p className="text-xs text-gray-400 mb-4">
            {t('settings.sttDescription')}
          </p>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.sttApiKey')}</label>
              <input
                type="password"
                value={settings.stt_api_key}
                onChange={(e) => setSettings({ ...settings, stt_api_key: e.target.value })}
                placeholder="sk-..."
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.sttBaseUrl')}</label>
              <input
                type="text"
                value={settings.stt_api_base_url}
                onChange={(e) => setSettings({ ...settings, stt_api_base_url: e.target.value })}
                placeholder="https://api.openai.com/v1"
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.sttModel')}</label>
              <input
                type="text"
                value={settings.stt_model}
                onChange={(e) => setSettings({ ...settings, stt_model: e.target.value })}
                placeholder="whisper-1"
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
              <p className="text-xs text-gray-400 mt-1">{t('settings.sttModelHint')}</p>
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.sttLanguage')}</label>
              <select
                value={settings.stt_language}
                onChange={(e) => setSettings({ ...settings, stt_language: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="zh">{t('common.chinese')}</option>
                <option value="en">{t('common.english')}</option>
                <option value="auto">{t('common.autoDetect')}</option>
              </select>
            </div>
          </div>
        </section>

        {/* LLM API 配置 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">{t('settings.llmConfig')}</h2>
          <p className="text-xs text-gray-400 mb-4">
            {t('settings.llmDescription')}
          </p>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.llmApiKey')}</label>
              <input
                type="password"
                value={settings.llm_api_key}
                onChange={(e) => setSettings({ ...settings, llm_api_key: e.target.value })}
                placeholder="sk-..."
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.llmBaseUrl')}</label>
              <input
                type="text"
                value={settings.llm_api_base_url}
                onChange={(e) => setSettings({ ...settings, llm_api_base_url: e.target.value })}
                placeholder="https://api.openai.com/v1"
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.llmModel')}</label>
              <input
                type="text"
                value={settings.llm_model}
                onChange={(e) => setSettings({ ...settings, llm_model: e.target.value })}
                placeholder="gpt-4o-mini"
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
              <p className="text-xs text-gray-400 mt-1">{t('settings.llmModelHint')}</p>
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.llmOutputLanguage')}</label>
              <select
                value={settings.llm_output_language}
                onChange={(e) => setSettings({ ...settings, llm_output_language: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="zh">{t('common.chinese')}</option>
                <option value="en">{t('common.english')}</option>
              </select>
              <p className="text-xs text-gray-400 mt-1">{t('settings.llmOutputLanguageHint')}</p>
            </div>
          </div>
        </section>

        {/* 音频设备 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">{t('settings.audioDevices')}</h2>
          <p className="text-xs text-gray-400 mb-4">
            {t('settings.audioDescription')}
          </p>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.audioOutputDevice')}</label>
              <select
                value={settings.audio_output_device}
                onChange={(e) => setSettings({ ...settings, audio_output_device: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="">{t('settings.systemDefault')}</option>
                {audioDevices.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.audioInputDevice')}</label>
              <select
                value={settings.audio_input_device}
                onChange={(e) => setSettings({ ...settings, audio_input_device: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="">{t('settings.systemDefault')}</option>
                {audioDevices
                  .filter((d) => !d.is_loopback)
                  .map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
              </select>
            </div>
          </div>
        </section>

        {/* 录音默认配置 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">{t('settings.recordingDefaults')}</h2>
          <p className="text-xs text-gray-400 mb-4">
            {t('settings.recordingDefaultsDesc')}
          </p>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.recordingSegmentationStrategy')}</label>
              <select
                value={settings.recording_segmentation_strategy}
                onChange={(e) => setSettings({ ...settings, recording_segmentation_strategy: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="vad">{t('recording.strategyVad')}</option>
                <option value="hybrid">{t('recording.strategyHybrid')}</option>
                <option value="fixed">{t('recording.strategyFixed')}</option>
              </select>
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.recordingMaxSegmentDuration')}</label>
              <input
                type="number"
                min={5}
                max={60}
                value={settings.recording_max_segment_duration}
                onChange={(e) => setSettings({ ...settings, recording_max_segment_duration: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.recordingFixedChunkDuration')}</label>
              <input
                type="number"
                min={10}
                max={120}
                value={settings.recording_fixed_chunk_duration}
                onChange={(e) => setSettings({ ...settings, recording_fixed_chunk_duration: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.recordingVadThreshold')}</label>
              <input
                type="number"
                min={0.3}
                max={0.9}
                step={0.1}
                value={settings.recording_vad_threshold}
                onChange={(e) => setSettings({ ...settings, recording_vad_threshold: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm text-gray-500 mb-1">{t('settings.recordingVadSilenceFrames')}</label>
                <input
                  type="number"
                  min={3}
                  max={20}
                  value={settings.recording_vad_silence_frames}
                  onChange={(e) => setSettings({ ...settings, recording_vad_silence_frames: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-500 mb-1">{t('settings.recordingVadHangoverFrames')}</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={settings.recording_vad_hangover_frames}
                  onChange={(e) => setSettings({ ...settings, recording_vad_hangover_frames: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
                />
              </div>
            </div>
          </div>
        </section>

        {/* VAD 引擎 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">{t('settings.vadEngine')}</h2>
          <p className="text-xs text-gray-400 mb-4">
            {t('settings.vadEngineDescription')}
          </p>
          {torchStatus ? (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <span className={`w-2.5 h-2.5 rounded-full ${torchStatus.vad_engine === 'silero' ? 'bg-green-500' : 'bg-yellow-500'}`} />
                <span className="text-sm text-gray-700">
                  {torchStatus.vad_engine === 'silero' ? t('settings.vadSilero') : t('settings.vadEnergy')}
                </span>
                {torchStatus.available && (
                  <span className="text-xs text-gray-400">PyTorch {torchStatus.version}</span>
                )}
              </div>
              {pythonInfo && (
                <div className="text-xs text-gray-500">
                  {pythonInfo.source === 'managed' && t('settings.vadPythonManaged')}
                  {pythonInfo.source === 'system' && `${t('settings.vadPythonSystem')}: ${pythonInfo.path}`}
                  {pythonInfo.source === 'none' && t('settings.vadPythonNone')}
                </div>
              )}
              {torchStatus.available && torchStatus.vad_engine === 'energy' && torchStatus.vad_error && (
                <div className="text-xs px-3 py-2 bg-yellow-50 text-yellow-700 rounded">
                  {t('settings.vadDegradedReason')}: {torchStatus.vad_error}
                </div>
              )}
              {torchMessage && (
                <div className={`text-xs px-3 py-2 rounded ${torchMessage.includes(t('settings.vadInstallSuccess')) || torchMessage.includes(t('settings.vadUninstallSuccess')) ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                  {torchMessage}
                </div>
              )}
              <div className="flex gap-2 flex-wrap">
                {torchStatus.vad_engine === 'energy' && (
                  <button
                    onClick={handleInstallTorch}
                    disabled={torchInstalling || torchRestarting}
                    className="px-4 py-2 bg-primary-600 text-white rounded-md hover:bg-primary-700 disabled:opacity-50 text-sm"
                  >
                    {torchInstalling ? t('settings.vadInstalling') : t('settings.vadInstallTorch')}
                  </button>
                )}
                {(torchMessage && torchMessage.includes(t('settings.vadInstallSuccess'))) || torchRestarting ? (
                  <button
                    onClick={handleRestartBackend}
                    disabled={torchRestarting}
                    className="px-4 py-2 border border-primary-200 text-primary-600 rounded-md hover:bg-primary-50 disabled:opacity-50 text-sm"
                  >
                    {torchRestarting ? t('settings.vadRestarting') : t('settings.vadRestart')}
                  </button>
                ) : null}
                {pythonInfo?.source === 'managed' && (
                  <>
                    <button
                      onClick={handleInstallTorch}
                      disabled={torchInstalling || torchRestarting || uninstallingPython}
                      className="px-4 py-2 border border-primary-200 text-primary-600 rounded-md hover:bg-primary-50 disabled:opacity-50 text-sm"
                    >
                      {t('settings.vadReinstall')}
                    </button>
                    <button
                      onClick={handleUninstallManagedPython}
                      disabled={uninstallingPython}
                      className="px-4 py-2 border border-red-200 text-red-600 rounded-md hover:bg-red-50 disabled:opacity-50 text-sm"
                    >
                      {uninstallingPython ? t('settings.vadUninstalling') : t('settings.vadUninstall')}
                    </button>
                  </>
                )}
              </div>
            </div>
          ) : (
            <div className="text-xs text-gray-400">{t('common.loading')}...</div>
          )}
        </section>

        {/* 界面语言 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">{t('settings.uiLanguage')}</h2>
          <p className="text-xs text-gray-400 mb-4">
            {t('settings.uiLanguageDescription')}
          </p>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-500 mb-1">{t('settings.uiLanguage')}</label>
              <select
                value={settings.ui_language}
                onChange={(e) => handleUiLanguageChange(e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="en">{t('common.english')}</option>
                <option value="zh">{t('common.chinese')}</option>
              </select>
            </div>
          </div>
        </section>

        <button
          onClick={handleSave}
          className="px-5 py-2 bg-primary-600 text-white rounded-md hover:bg-primary-700 text-sm"
        >
          {saved ? t('common.saved') : t('common.save')}
        </button>
      </div>
    </div>
  )
}
