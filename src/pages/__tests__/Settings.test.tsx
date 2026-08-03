import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { mockFetchRoutes } from '../../tests/helpers'

const { changeLanguageMock } = vi.hoisted(() => ({ changeLanguageMock: vi.fn() }))

vi.mock('../../i18n/config', () => ({
  default: { language: 'en', changeLanguage: changeLanguageMock },
}))

import Settings from '../Settings'

const devices = [
  { id: 'loop-1', name: 'Speakers (Loopback)', is_loopback: true },
  { id: 'mic-1', name: 'Microphone Array', is_loopback: false },
]

const energyTorchStatus = {
  available: false,
  version: null,
  backend_mode: 'source',
  vad_engine: 'energy',
  vad_error: null,
}

const sileroTorchStatus = {
  available: true,
  version: '2.3.0',
  backend_mode: 'source',
  vad_engine: 'silero',
  vad_error: null,
}

function renderSettings() {
  return render(
    <MemoryRouter initialEntries={['/settings']}>
      <Settings />
    </MemoryRouter>,
  )
}

function findSection(text: string): HTMLElement {
  const section = [...document.querySelectorAll('section')].find((el) =>
    el.textContent?.includes(text),
  )
  if (!section) throw new Error(`section with "${text}" not found`)
  return section
}

describe('Settings Page', () => {
  beforeEach(() => {
    changeLanguageMock.mockClear()
    vi.mocked(window.electronAPI!.installTorch).mockResolvedValue({ success: true, message: '' })
    vi.mocked(window.electronAPI!.restartBackend).mockResolvedValue('ok')
    vi.mocked(window.electronAPI!.getPythonInfo).mockResolvedValue({ source: 'none', path: null })
    vi.mocked(window.electronAPI!.uninstallManagedPython).mockResolvedValue({ success: true, message: '' })
    mockFetchRoutes((url) => {
      if (url.endsWith('/api/settings')) {
        return { ui_language: 'en', stt_api_key: 'sk-123', stt_model: 'whisper-large-v3' }
      }
      if (url.endsWith('/api/audio/devices')) return { devices }
      if (url.endsWith('/api/system/torch-status')) return energyTorchStatus
      return {}
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders all configuration sections', async () => {
    renderSettings()

    expect(screen.getByText('settings.title')).toBeInTheDocument()
    expect(await screen.findByDisplayValue('sk-123')).toBeInTheDocument()
    expect(screen.getByText('settings.sttConfig')).toBeInTheDocument()
    expect(screen.getByText('settings.llmConfig')).toBeInTheDocument()
    expect(screen.getByText('settings.audioDevices')).toBeInTheDocument()
    expect(screen.getByText('settings.recordingDefaults')).toBeInTheDocument()
    expect(screen.getByText('settings.vadEngine')).toBeInTheDocument()
    expect(screen.getAllByText('settings.uiLanguage').length).toBeGreaterThan(0)
    expect(screen.getByText('common.save')).toBeInTheDocument()
  })

  it('loads persisted settings into the form', async () => {
    renderSettings()

    expect(await screen.findByDisplayValue('sk-123')).toBeInTheDocument()
    expect(screen.getByDisplayValue('whisper-large-v3')).toBeInTheDocument()
  })

  it('syncs i18n language with persisted ui_language', async () => {
    mockFetchRoutes((url) => {
      if (url.endsWith('/api/settings')) return { ui_language: 'zh' }
      if (url.endsWith('/api/audio/devices')) return { devices }
      if (url.endsWith('/api/system/torch-status')) return energyTorchStatus
      return {}
    })

    renderSettings()
    await screen.findByText('settings.vadEngine')

    expect(changeLanguageMock).toHaveBeenCalledWith('zh')
  })

  it('filters loopback devices out of the input device select', async () => {
    renderSettings()
    await screen.findByText('settings.audioDevices')

    const audioSection = findSection('settings.audioDescription')
    const selects = audioSection.querySelectorAll('select')
    expect(selects.length).toBe(2)

    // 输出设备：包含 loopback 设备
    const outputSelect = within(selects[0] as HTMLElement)
    expect(outputSelect.getByRole('option', { name: 'Speakers (Loopback)' })).toBeInTheDocument()
    expect(outputSelect.getByRole('option', { name: 'Microphone Array' })).toBeInTheDocument()

    // 输入设备：过滤掉 loopback
    const inputSelect = within(selects[1] as HTMLElement)
    expect(inputSelect.queryByText('Speakers (Loopback)')).not.toBeInTheDocument()
    expect(inputSelect.getByRole('option', { name: 'Microphone Array' })).toBeInTheDocument()
  })

  it('shows install button for energy VAD and installs PyTorch', async () => {
    renderSettings()

    expect(await screen.findByText('settings.vadEnergy')).toBeInTheDocument()
    expect(screen.queryByText('settings.vadSilero')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('settings.vadInstallTorch'))

    expect(await screen.findByText('settings.vadInstallSuccess')).toBeInTheDocument()
    expect(vi.mocked(window.electronAPI!.installTorch)).toHaveBeenCalledTimes(1)
  })

  it('shows no-python hint when install fails due to missing Python', async () => {
    vi.mocked(window.electronAPI!.installTorch).mockResolvedValue({
      success: false,
      message: 'Python not found. Please install Python 3.11+',
    })

    renderSettings()
    await screen.findByText('settings.vadInstallTorch')

    fireEvent.click(screen.getByText('settings.vadInstallTorch'))

    expect(await screen.findByText('settings.vadNoPython')).toBeInTheDocument()
  })

  it('hides install button when Silero VAD is active', async () => {
    mockFetchRoutes((url) => {
      if (url.endsWith('/api/settings')) return { ui_language: 'en' }
      if (url.endsWith('/api/audio/devices')) return { devices }
      if (url.endsWith('/api/system/torch-status')) return sileroTorchStatus
      return {}
    })

    renderSettings()

    expect(await screen.findByText('settings.vadSilero')).toBeInTheDocument()
    expect(screen.getByText(/PyTorch 2\.3\.0/)).toBeInTheDocument()
    expect(screen.queryByText('settings.vadInstallTorch')).not.toBeInTheDocument()
  })

  it('shows managed python actions and uninstalls managed python', async () => {
    vi.mocked(window.electronAPI!.getPythonInfo).mockResolvedValue({ source: 'managed', path: null })

    renderSettings()

    expect(await screen.findByText('settings.vadPythonManaged')).toBeInTheDocument()
    expect(screen.getByText('settings.vadReinstall')).toBeInTheDocument()

    fireEvent.click(screen.getByText('settings.vadUninstall'))

    expect(await screen.findByText('settings.vadUninstallSuccess')).toBeInTheDocument()
    expect(vi.mocked(window.electronAPI!.uninstallManagedPython)).toHaveBeenCalledTimes(1)
    // 卸载后回到未检测状态
    expect(screen.getByText('settings.vadPythonNone')).toBeInTheDocument()
  })

  it('saves settings with PUT and shows saved indicator', async () => {
    renderSettings()

    const modelInput = await screen.findByDisplayValue('whisper-large-v3')
    fireEvent.change(modelInput, { target: { value: 'whisper-tiny' } })

    fireEvent.click(screen.getByText('common.save'))

    await waitFor(() => {
      const fetchMock = vi.mocked(global.fetch)
      const putCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          typeof input === 'string' && input.endsWith('/api/settings') && init?.method === 'PUT',
      )
      expect(putCall).toBeDefined()
      const body = JSON.parse((putCall![1] as RequestInit).body as string)
      expect(body.stt_model).toBe('whisper-tiny')
    })
    expect(await screen.findByText('common.saved')).toBeInTheDocument()
  })

  it('syncs llm output language when ui language changes', async () => {
    renderSettings()
    await screen.findByText('settings.uiLanguageDescription')

    const allSelects = document.querySelectorAll('select')
    const uiLangSelect = allSelects[allSelects.length - 1] as HTMLSelectElement
    const llmOutputSelect = allSelects[1] as HTMLSelectElement

    fireEvent.change(uiLangSelect, { target: { value: 'zh' } })

    expect(changeLanguageMock).toHaveBeenCalledWith('zh')
    expect(uiLangSelect.value).toBe('zh')
    expect(llmOutputSelect.value).toBe('zh')
  })

  it('restarts backend after successful torch install', async () => {
    vi.useFakeTimers()
    let torchStatusBody: typeof energyTorchStatus | typeof sileroTorchStatus = energyTorchStatus

    mockFetchRoutes((url) => {
      if (url.endsWith('/api/settings')) return { ui_language: 'en' }
      if (url.endsWith('/api/audio/devices')) return { devices }
      if (url.endsWith('/api/system/torch-status')) return torchStatusBody
      return {}
    })

    renderSettings()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    // 安装成功 → 出现重启按钮
    fireEvent.click(screen.getByText('settings.vadInstallTorch'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.getByText('settings.vadInstallSuccess')).toBeInTheDocument()

    // 重启后端：2s 延迟后重新加载 torch 状态
    torchStatusBody = sileroTorchStatus
    fireEvent.click(screen.getByText('settings.vadRestart'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    expect(vi.mocked(window.electronAPI!.restartBackend)).toHaveBeenCalledTimes(1)
    expect(screen.getByText('settings.vadRestartDone')).toBeInTheDocument()
  })
})
