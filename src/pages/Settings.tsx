import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export default function Settings() {
  const navigate = useNavigate()
  const [settings, setSettings] = useState({
    openai_api_key: '',
    openai_base_url: 'https://api.openai.com/v1',
    stt_model: 'whisper-1',
    stt_language: 'zh',
    llm_model: 'gpt-4o-mini',
    deepseek_api_key: '',
  })
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    loadSettings()
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
        {/* LLM 配置 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">LLM 配置</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-gray-500 mb-1">OpenAI API Key</label>
              <input
                type="password"
                value={settings.openai_api_key}
                onChange={(e) => setSettings({ ...settings, openai_api_key: e.target.value })}
                placeholder="sk-..."
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">API Base URL</label>
              <input
                type="text"
                value={settings.openai_base_url}
                onChange={(e) => setSettings({ ...settings, openai_base_url: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-500 mb-1">纪要模型</label>
              <select
                value={settings.llm_model}
                onChange={(e) => setSettings({ ...settings, llm_model: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
              >
                <option value="gpt-4o-mini">GPT-4o Mini（推荐）</option>
                <option value="gpt-4o">GPT-4o</option>
                <option value="deepseek-chat">DeepSeek Chat</option>
              </select>
            </div>
          </div>
        </section>

        {/* STT 配置 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">语音转写</h2>
          <div className="space-y-4">
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

        {/* DeepSeek 配置 */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">DeepSeek（可选备选）</h2>
          <div>
            <label className="block text-sm text-gray-500 mb-1">DeepSeek API Key</label>
            <input
              type="password"
              value={settings.deepseek_api_key}
              onChange={(e) => setSettings({ ...settings, deepseek_api_key: e.target.value })}
              placeholder="sk-..."
              className="w-full px-3 py-2 border border-gray-200 rounded-md text-sm focus:outline-none focus:border-primary-400"
            />
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
