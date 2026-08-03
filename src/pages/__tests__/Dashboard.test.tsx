import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { mockFetchRoutes } from '../../tests/helpers'
import { useMeetingStore } from '../../stores/meetings'
import { useSettingsStore } from '../../stores/settings'

import Dashboard from '../Dashboard'

const meetings = [
  {
    id: 'm1',
    title: 'Alpha Review',
    audio_path: '',
    duration_seconds: 600,
    created_at: '2026-07-01T10:00:00Z',
    status: 'done' as const,
  },
  {
    id: 'm2',
    title: 'Beta Planning',
    audio_path: '',
    duration_seconds: 300,
    created_at: '2026-07-02T09:00:00Z',
    status: 'recording' as const,
  },
]

const fullSettings = {
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
}

function renderDashboard() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/meeting/:id" element={<div data-testid="meeting-page" />} />
        <Route path="/recording/:id" element={<div data-testid="recording-page" />} />
        <Route path="/settings" element={<div data-testid="settings-page" />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Dashboard Page', () => {
  beforeEach(() => {
    useMeetingStore.setState({ meetings: [], loading: false })
    useSettingsStore.setState({ settings: fullSettings, loaded: true })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(window.electronAPI!.selectAudioFile).mockResolvedValue(null)
    mockFetchRoutes((url, init) => {
      if (url.endsWith('/api/meetings') && (!init?.method || init.method === 'GET')) {
        return { meetings }
      }
      if (url.endsWith('/api/settings')) return {}
      return {}
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders meeting list with status badges and count', async () => {
    renderDashboard()

    expect(await screen.findByText('Alpha Review')).toBeInTheDocument()
    expect(screen.getByText('Beta Planning')).toBeInTheDocument()
    expect(screen.getByText(/dashboard.meetingRecords/)).toBeInTheDocument()
    expect(screen.getByText('meetingCard.done')).toBeInTheDocument()
    expect(screen.getByText('meetingCard.recording')).toBeInTheDocument()
  })

  it('shows empty state when no meetings exist', async () => {
    mockFetchRoutes((url, init) => {
      if (url.endsWith('/api/meetings') && (!init?.method || init.method === 'GET')) {
        return { meetings: [] }
      }
      if (url.endsWith('/api/settings')) return {}
      return {}
    })

    renderDashboard()

    expect(await screen.findByText('dashboard.noMeetings')).toBeInTheDocument()
    expect(screen.getByText('dashboard.noMeetingsHint')).toBeInTheDocument()
  })

  it('shows loading indicator while meetings are being fetched', async () => {
    const resolvers: Array<(value: Response) => void> = []
    vi.mocked(global.fetch).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvers.push(resolve)
        }),
    )

    renderDashboard()

    expect(screen.getByText('common.loading')).toBeInTheDocument()

    await act(async () => {
      // flush 微任务使 fetchMeetings/fetchSettings 调用 fetch
      await Promise.resolve()
      await Promise.resolve()
      for (const resolveFetch of resolvers) {
        resolveFetch({ ok: true, json: async () => ({ meetings: [] }) } as Response)
      }
    })

    expect(screen.getByText('dashboard.noMeetings')).toBeInTheDocument()
  })

  it('navigates to meeting detail when a card is clicked', async () => {
    renderDashboard()

    fireEvent.click(await screen.findByText('Alpha Review'))

    expect(await screen.findByTestId('meeting-page')).toBeInTheDocument()
  })

  it('opens recording dialog and starts recording with default config', async () => {
    renderDashboard()
    await screen.findByText('Alpha Review')

    fireEvent.click(screen.getByText('dashboard.startRecording'))
    expect(screen.getByText('recording.startDialog')).toBeInTheDocument()

    fireEvent.click(screen.getByText('recording.startNow'))

    await waitFor(() => expect(screen.getByTestId('recording-page')).toBeInTheDocument())
    const fetchMock = vi.mocked(global.fetch)
    const postCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        typeof input === 'string' && input.endsWith('/api/record/start') && init?.method === 'POST',
    )
    expect(postCall).toBeDefined()
    const body = JSON.parse((postCall![1] as RequestInit).body as string)
    expect(body.config.segmentation_strategy).toBe('hybrid')
  })

  it('deletes a meeting via card delete button', async () => {
    renderDashboard()
    await screen.findByText('Alpha Review')

    const deleteButtons = screen.getAllByTitle('meetingCard.delete')
    fireEvent.click(deleteButtons[0])

    await waitFor(() => expect(screen.queryByText('Alpha Review')).not.toBeInTheDocument())
    const fetchMock = vi.mocked(global.fetch)
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          typeof input === 'string' &&
          input.endsWith('/api/meetings/m1') &&
          init?.method === 'DELETE',
      ),
    ).toBe(true)
  })

  it('does not delete when confirm is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)

    renderDashboard()
    await screen.findByText('Alpha Review')

    const deleteButtons = screen.getAllByTitle('meetingCard.delete')
    fireEvent.click(deleteButtons[0])

    expect(screen.getByText('Alpha Review')).toBeInTheDocument()
    const fetchMock = vi.mocked(global.fetch)
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          typeof input === 'string' &&
          input.endsWith('/api/meetings/m1') &&
          init?.method === 'DELETE',
      ),
    ).toBe(false)
  })

  it('imports an audio file dropped on the dropzone', async () => {
    renderDashboard()
    await screen.findByText('Alpha Review')

    const dropZone = screen.getByText('dashboard.dragAudioHere')
    const file = new File(['data'], 'meeting.mp3', { type: 'audio/mpeg' })
    Object.defineProperty(file, 'path', { value: 'C:/audio/meeting.mp3' })
    fireEvent.drop(dropZone, { dataTransfer: { files: [file] } })

    await waitFor(() => expect(screen.getByTestId('meeting-page')).toBeInTheDocument())
    const fetchMock = vi.mocked(global.fetch)
    const postCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        typeof input === 'string' && input.endsWith('/api/import/audio') && init?.method === 'POST',
    )
    expect(postCall).toBeDefined()
    const body = JSON.parse((postCall![1] as RequestInit).body as string)
    expect(body.file_path).toBe('C:/audio/meeting.mp3')
  })

  it('ignores dropped files that are not audio', async () => {
    renderDashboard()
    await screen.findByText('Alpha Review')

    const dropZone = screen.getByText('dashboard.dragAudioHere')
    const file = new File(['x'], 'notes.txt', { type: 'text/plain' })
    fireEvent.drop(dropZone, { dataTransfer: { files: [file] } })

    await waitFor(() => {
      expect(screen.queryByTestId('meeting-page')).not.toBeInTheDocument()
    })
    const fetchMock = vi.mocked(global.fetch)
    expect(
      fetchMock.mock.calls.some(
        ([input]) => typeof input === 'string' && input.endsWith('/api/import/audio'),
      ),
    ).toBe(false)
  })

  it('imports audio file via file select dialog', async () => {
    vi.mocked(window.electronAPI!.selectAudioFile).mockResolvedValue('C:/audio/import.wav')

    renderDashboard()
    await screen.findByText('Alpha Review')

    fireEvent.click(screen.getByText('dashboard.dragAudioHere'))

    await waitFor(() => expect(screen.getByTestId('meeting-page')).toBeInTheDocument())
    const fetchMock = vi.mocked(global.fetch)
    const postCall = fetchMock.mock.calls.find(
      ([input]) => typeof input === 'string' && input.endsWith('/api/import/audio'),
    )
    expect(postCall).toBeDefined()
  })

  it('navigates to settings page', async () => {
    renderDashboard()

    fireEvent.click(await screen.findByText('dashboard.settings'))

    expect(await screen.findByTestId('settings-page')).toBeInTheDocument()
  })
})
