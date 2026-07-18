import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export default function Settings() {
  const navigate = useNavigate()
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
        <h1 className="text-lg font-semibold">设置</h1>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6 max-w-xl">
        {/* STT API 配置 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">STT API 配置</h2>
          <p className="text-xs text-gray-400 mb-4">
            语音转写 API。支持任意兼容 OpenAI 接口的服务。
          </p>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-500 mb-1">STT API Key</label>
              <input
                type="password"
                value={settings.stt_api_key}
                onChange={(e) => setSettings({ ...settings, stt_api_key: e.target.value })}
                placeholder="sk-..."
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">STT API Base URL</label>
              <input
                type="text"
                value={settings.stt_api_base_url}
                onChange={(e) => setSettings({ ...settings, stt_api_base_url: e.target.value })}
                placeholder="https://api.openai.com/v1"
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">STT 模型</label>
              <input
                type="text"
                value={settings.stt_model}
                onChange={(e) => setSettings({ ...settings, stt_model: e.target.value })}
                placeholder="whisper-1"
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
              <p className="text-xs text-gray-400 mt-1">常用: whisper-1 / FunAudioLLM/SenseVoiceSmall</p>
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">转写语言</label>
              <select
                value={settings.stt_language}
                onChange={(e) => setSettings({ ...settings, stt_language: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="zh">中文</option>
                <option value="en">English</option>
                <option value="auto">自动检测</option>
              </select>
            </div>
          </div>
        </section>

        {/* LLM API 配置 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">LLM API 配置</h2>
          <p className="text-xs text-gray-400 mb-4">
            纪要生成 API。支持任意兼容 OpenAI 接口的服务。
          </p>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-500 mb-1">LLM API Key</label>
              <input
                type="password"
                value={settings.llm_api_key}
                onChange={(e) => setSettings({ ...settings, llm_api_key: e.target.value })}
                placeholder="sk-..."
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">LLM API Base URL</label>
              <input
                type="text"
                value={settings.llm_api_base_url}
                onChange={(e) => setSettings({ ...settings, llm_api_base_url: e.target.value })}
                placeholder="https://api.openai.com/v1"
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">LLM 纪要模型</label>
              <input
                type="text"
                value={settings.llm_model}
                onChange={(e) => setSettings({ ...settings, llm_model: e.target.value })}
                placeholder="gpt-4o-mini"
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
              <p className="text-xs text-gray-400 mt-1">常用: gpt-4o-mini / deepseek-chat / qwen-plus</p>
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">输出语言</label>
              <select
                value={settings.llm_output_language}
                onChange={(e) => setSettings({ ...settings, llm_output_language: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="zh">中文</option>
                <option value="en">English</option>
              </select>
              <p className="text-xs text-gray-400 mt-1">会议总结的输出语言，默认英文</p>
            </div>
          </div>
        </section>

        {/* 音频设备 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">音频设备</h2>
          <p className="text-xs text-gray-400 mb-4">
            选择录制时使用的音频设备。留空则使用系统默认设备。
          </p>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-500 mb-1">输出设备（系统音频）</label>
              <select
                value={settings.audio_output_device}
                onChange={(e) => setSettings({ ...settings, audio_output_device: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="">系统默认</option>
                {audioDevices.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">输入设备（麦克风）</label>
              <select
                value={settings.audio_input_device}
                onChange={(e) => setSettings({ ...settings, audio_input_device: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="">系统默认</option>
                {audioDevices
                  .filter((d) => !d.is_loopback)
                  .map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
              </select>
            </div>
          </div>
        </section>

        <button
          onClick={handleSave}
          className="px-5 py-2 bg-primary-600 text-white rounded-md hover:bg-primary-700 text-sm"
        >
          {saved ? '已保存' : '保存设置'}
        </button>
      </div>
    </div>
  )
}
