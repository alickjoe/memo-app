import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

// --- Mock electronAPI on window ---
const mockElectronAPI = {
  getBackendUrl: vi.fn().mockResolvedValue('http://localhost:8765'),
  selectAudioFile: vi.fn().mockResolvedValue(null),
  showMainWindow: vi.fn(),
  onTrayStartRecording: vi.fn(),
  removeTrayStartRecordingListener: vi.fn(),
  getBackendMode: vi.fn().mockResolvedValue('source'),
  installTorch: vi.fn(),
  restartBackend: vi.fn(),
  getPythonInfo: vi.fn().mockResolvedValue({ source: 'none', path: null }),
  uninstallManagedPython: vi.fn(),
}

Object.defineProperty(window, 'electronAPI', {
  value: mockElectronAPI,
  writable: true,
  configurable: true,
})

// --- Mock react-i18next ---
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
  initReactI18next: { type: '3rdParty', init: vi.fn() },
}))

// --- Mock fetch globally ---
const mockFetch = vi.fn()
global.fetch = mockFetch as unknown as typeof fetch

// Reset between tests
beforeEach(() => {
  mockFetch.mockReset()
  mockFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
  mockElectronAPI.getBackendUrl.mockResolvedValue('http://localhost:8765')
})
