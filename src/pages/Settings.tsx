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
  })
  const [saved, setSaved] = useState(false)
  const [audioDevices, setAudioDevices] = useState<{ id: string; name: string; is_loopback: boolean }[]>([])

  useEffect(() => {
    loadSettings()
    loadAudioDevices()
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
