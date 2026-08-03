import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { mockFetchRoutes } from '../../tests/helpers'
import { useSettingsStore } from '../../stores/settings'

class MockWebSocket {
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: ((_err: unknown) => void) | null = null
  close = vi.fn()
  send = vi.fn()
}

vi.stubGlobal('WebSocket', MockWebSocket)

import MeetingDetail from '../MeetingDetail'

const doneMeeting = {
  id: 'm1',
  title: 'Weekly Sync',
  audio_path: '/audio/m1.wav',
  duration_seconds: 125,
  created_at: '2026-07-01T10:00:00Z',
  status: 'done',
}

const minutes = {
  summary: 'Discussed Q3 roadmap',
  key_points: ['Key point 1', 'Key point 2'],
  action_items: ['Action 1'],
  next_steps: 'Follow up next week',
}

const v1Segments = [
  { speaker: 'Speaker A', text: 'First segment', start_time: 0, end_time: 10, version: 1 },
  { speaker: 'Speaker B', text: 'Second segment', start_time: 10, end_time: 20, version: 1 },
]

const v2Segments = [
  { speaker: 'Speaker A', text: 'First segment v2', start_time: 0, end_time: 10, version: 2 },
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

let createObjectURLMock: ReturnType<typeof vi.fn>

function renderMeeting(id = 'm1') {
  return render(
    <MemoryRouter initialEntries={[`/meeting/${id}`]}>
      <Routes>
        <Route path="/meeting/:id" element={<MeetingDetail />} />
        <Route path="/" element={<div data-testid="home-stub" />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('MeetingDetail Page', () => {
  beforeEach(() => {
    useSettingsStore.setState({ settings: fullSettings, loaded: true })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    createObjectURLMock = vi.fn(() => 'blob:mock')
    Object.defineProperty(URL, 'createObjectURL', {
      value: createObjectURLMock,
      writable: true,
      configurable: true,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: vi.fn(),
      writable: true,
      configurable: true,
    })
    mockFetchRoutes((url) => {
      if (url.endsWith('/api/meetings/m1')) {
        return { meeting: doneMeeting, minutes, transcripts: v1Segments, transcript_versions: [1] }
      }
      return {}
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('renders meeting info and minutes in summary tab by default', async () => {
    renderMeeting()

    expect(await screen.findByText('Weekly Sync')).toBeInTheDocument()
    expect(screen.getByText(/2meeting\.durationMin5meeting\.durationSec/)).toBeInTheDocument()

    // MinutesPanel 内容
    expect(screen.getByText('Discussed Q3 roadmap')).toBeInTheDocument()
    expect(screen.getByText('Key point 1')).toBeInTheDocument()
    expect(screen.getByText('Action 1')).toBeInTheDocument()
    expect(screen.getByText('Follow up next week')).toBeInTheDocument()
  })

  it('switches to transcript tab and renders segments', async () => {
    renderMeeting()
    await screen.findByText('Weekly Sync')

    fireEvent.click(screen.getByText('meeting.transcript'))

    expect(await screen.findByText('First segment')).toBeInTheDocument()
    expect(screen.getByText('Second segment')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('shows no-minutes hint when meeting has no minutes', async () => {
    mockFetchRoutes((url) => {
      if (url.endsWith('/api/meetings/m1')) {
        return { meeting: doneMeeting, minutes: null, transcripts: [], transcript_versions: [] }
      }
      return {}
    })

    renderMeeting()

    expect(await screen.findByText('meeting.noMinutes')).toBeInTheDocument()
  })

  it('shows generating state while meeting is processing', async () => {
    mockFetchRoutes((url) => {
      if (url.endsWith('/api/meetings/m1')) {
        return {
          meeting: { ...doneMeeting, status: 'processing' },
          minutes: null,
          transcripts: [],
          transcript_versions: [],
        }
      }
      return {}
    })

    renderMeeting()

    expect(await screen.findByText('meeting.generating')).toBeInTheDocument()
  })

  it('edits and saves the meeting title', async () => {
    renderMeeting()

    fireEvent.click(await screen.findByText('Weekly Sync'))
    const input = screen.getByRole('textbox')
    fireEvent.change(input, { target: { value: 'Q3 Roadmap Sync' } })
    fireEvent.blur(input)

    expect(await screen.findByText('Q3 Roadmap Sync')).toBeInTheDocument()
    const fetchMock = vi.mocked(global.fetch)
    const putCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        typeof input === 'string' && input.endsWith('/api/meetings/m1') && init?.method === 'PUT',
    )
    expect(putCall).toBeDefined()
    const body = JSON.parse((putCall![1] as RequestInit).body as string)
    expect(body.title).toBe('Q3 Roadmap Sync')
  })

  it('deletes the meeting and navigates home', async () => {
    renderMeeting()
    await screen.findByText('Weekly Sync')

    fireEvent.click(screen.getByText('meeting.delete'))

    await waitFor(() => expect(screen.getByTestId('home-stub')).toBeInTheDocument())
    const fetchMock = vi.mocked(global.fetch)
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          typeof input === 'string' && input.endsWith('/api/meetings/m1') && init?.method === 'DELETE',
      ),
    ).toBe(true)
  })

  it('exports meeting minutes as markdown', async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    renderMeeting()
    await screen.findByText('Weekly Sync')

    fireEvent.click(screen.getByText('meeting.exportMd'))

    await waitFor(() => expect(createObjectURLMock).toHaveBeenCalled())
    const blob = createObjectURLMock.mock.calls[0][0] as Blob
    const content = await blob.text()
    expect(content).toContain('# Weekly Sync')
    expect(content).toContain('Discussed Q3 roadmap')
    expect(content).toContain('- Key point 1')
    expect(content).toContain('- [ ] Action 1')
    expect(content).toContain('[Speaker A] First segment')
    expect(clickSpy).toHaveBeenCalled()
  })

  it('exports meeting minutes as txt', async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    renderMeeting()
    await screen.findByText('Weekly Sync')

    fireEvent.click(screen.getByText('meeting.exportTxt'))

    await waitFor(() => expect(createObjectURLMock).toHaveBeenCalled())
    const blob = createObjectURLMock.mock.calls[0][0] as Blob
    const content = await blob.text()
    expect(content).toContain('Weekly Sync')
    expect(content).toContain('[ ] Action 1')
    expect(clickSpy).toHaveBeenCalled()
  })

  it('re-transcribes and stops when the new version appears', async () => {
    vi.useFakeTimers()
    let getCount = 0

    mockFetchRoutes((url, init) => {
      if (url.endsWith('/api/meetings/m1') && (!init?.method || init.method === 'GET')) {
        getCount += 1
        if (getCount <= 1) {
          return { meeting: doneMeeting, minutes, transcripts: v1Segments, transcript_versions: [1] }
        }
        return {
          meeting: doneMeeting,
          minutes,
          transcripts: [...v1Segments, ...v2Segments],
          transcript_versions: [1, 2],
        }
      }
      if (url.endsWith('/retranscribe')) return { version: 2 }
      return {}
    })

    renderMeeting()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.getByText('Weekly Sync')).toBeInTheDocument()

    fireEvent.click(screen.getByText('meeting.retranscribe'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    // POST 完成 → 显示重转写状态栏
    expect(screen.getByText(/meeting.retranscribingStatus/)).toBeInTheDocument()

    // 100ms 后 loadMeeting 刷新（旧闭包不检测完成），2000ms 轮询检测到新版本出现 → 状态清除
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(screen.queryByText(/meeting.retranscribingStatus/)).not.toBeInTheDocument()
  })

  it('shows error banner when retranscribe fails', async () => {
    vi.useFakeTimers()

    mockFetchRoutes((url) => {
      if (url.endsWith('/api/meetings/m1')) {
        return { meeting: doneMeeting, minutes, transcripts: v1Segments, transcript_versions: [1] }
      }
      if (url.endsWith('/retranscribe')) {
        return { ok: false, error: 'No audio file' }
      }
      return {}
    })
    const fetchMock = vi.mocked(global.fetch)
    fetchMock.mockImplementation(async (input, _init) => {
      const url = typeof input === 'string' ? input : ''
      if (url.endsWith('/retranscribe')) {
        return { ok: false, status: 400, json: async () => ({ error: 'No audio file' }) } as Response
      }
      if (url.endsWith('/api/meetings/m1')) {
        return {
          ok: true,
          json: async () => ({ meeting: doneMeeting, minutes, transcripts: v1Segments, transcript_versions: [1] }),
        } as Response
      }
      return { ok: true, json: async () => ({}) } as Response
    })

    renderMeeting()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    fireEvent.click(screen.getByText('meeting.retranscribe'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(screen.getByText('meeting.retranscribeNoAudio')).toBeInTheDocument()
    expect(screen.getByText('meeting.dismissError')).toBeInTheDocument()

    // 关闭错误提示
    fireEvent.click(screen.getByText('meeting.dismissError'))
    expect(screen.queryByText('meeting.retranscribeNoAudio')).not.toBeInTheDocument()
  })

  it('switches transcript version via selector and filters segments', async () => {
    mockFetchRoutes((url) => {
      if (url.endsWith('/api/meetings/m1')) {
        return {
          meeting: doneMeeting,
          minutes,
          transcripts: [...v1Segments, ...v2Segments],
          transcript_versions: [1, 2],
        }
      }
      return {}
    })

    renderMeeting()
    await screen.findByText('Weekly Sync')

    fireEvent.click(screen.getByText('meeting.transcript'))
    // 默认选择最新版本 2
    expect(await screen.findByText('First segment v2')).toBeInTheDocument()
    expect(screen.queryByText('First segment')).not.toBeInTheDocument()

    const versionSelect = screen.getByRole('combobox')
    fireEvent.change(versionSelect, { target: { value: '1' } })

    expect(screen.getByText('First segment')).toBeInTheDocument()
    expect(screen.queryByText('First segment v2')).not.toBeInTheDocument()
  })
})
