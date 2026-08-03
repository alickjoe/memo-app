import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

// --- WebSocket mock ---
let wsInstances: MockWebSocket[] = []
class MockWebSocket {
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: ((_err: unknown) => void) | null = null
  close = vi.fn()
  send = vi.fn()

  constructor() {
    wsInstances.push(this)
  }
}

vi.stubGlobal('WebSocket', MockWebSocket)

import Recording from '../Recording'

// Helper: render Recording inside MemoryRouter
function renderRecording(id = 'test-meeting-123') {
  return render(
    <MemoryRouter initialEntries={[`/recording/${id}`]}>
      <Routes>
        <Route path="/recording/:id" element={<Recording />} />
        <Route path="/meeting/:id" element={<div data-testid="meeting-detail-page">Meeting Detail</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Recording Page', () => {
  beforeEach(() => {
    wsInstances = []
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders with initial recording state', async () => {
    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByText('recording.recording')).toBeInTheDocument()
    expect(screen.getByText('recording.pause')).toBeInTheDocument()
    expect(screen.getByText('recording.stopRecording')).toBeInTheDocument()
    expect(screen.getByText('0:00')).toBeInTheDocument()
  })

  it('toggles pause/resume when pause button is clicked', async () => {
    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    // Click pause using synchronous fireEvent
    const pauseBtn = screen.getByText('recording.pause')
    await act(async () => {
      fireEvent.click(pauseBtn)
      // Let the async fetch promise resolve
      await vi.advanceTimersByTimeAsync(0)
    })

    // After pause: status = Paused, button = Resume, yellow dot
    expect(screen.getByText('recording.paused')).toBeInTheDocument()
    expect(screen.getByText('recording.resume')).toBeInTheDocument()
    expect(document.querySelector('.bg-yellow-400')).toBeInTheDocument()

    // Click resume
    const resumeBtn = screen.getByText('recording.resume')
    await act(async () => {
      fireEvent.click(resumeBtn)
      await vi.advanceTimersByTimeAsync(0)
    })

    // Back to recording state
    expect(screen.getByText('recording.recording')).toBeInTheDocument()
    expect(screen.getByText('recording.pause')).toBeInTheDocument()
    expect(document.querySelector('.bg-red-500')).toBeInTheDocument()
  })

  it('calls pause and resume API endpoints', async () => {
    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    const urls: string[] = []
    const fetchMock = vi.mocked(global.fetch)
    fetchMock.mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input instanceof Request ? input.url : ''
      urls.push(url)
      return { ok: true, json: async () => ({}) } as Response
    })

    await act(async () => {
      fireEvent.click(screen.getByText('recording.pause'))
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(urls).toContain('http://localhost:8765/api/record/pause')

    await act(async () => {
      fireEvent.click(screen.getByText('recording.resume'))
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(urls).toContain('http://localhost:8765/api/record/resume')
  })

  it('navigates to meeting detail when stop is clicked', async () => {
    renderRecording('meeting-456')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    // Ensure the fetch for stop returns ok
    const fetchMock = vi.mocked(global.fetch)
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) } as Response)

    await act(async () => {
      fireEvent.click(screen.getByText('recording.stopRecording'))
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByTestId('meeting-detail-page')).toBeInTheDocument()
    expect(screen.getByText('Meeting Detail')).toBeInTheDocument()
  })

  it('shows waiting message when no transcripts exist', async () => {
    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByText('recording.waitingAudio')).toBeInTheDocument()
  })

  it('timer increments over time', async () => {
    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByText('0:00')).toBeInTheDocument()

    // Advance 65 seconds
    await act(async () => {
      await vi.advanceTimersByTimeAsync(65000)
    })

    expect(screen.getByText('1:05')).toBeInTheDocument()
  })

  it('renders transcript segments received via WebSocket', async () => {
    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    const ws = wsInstances[wsInstances.length - 1]
    expect(ws).toBeDefined()

    await act(async () => {
      ws.onmessage?.({ data: JSON.stringify({ type: 'transcript', segment: { speaker: 'Speaker A', text: 'Hello world', start_time: 1, end_time: 3 } }) })
      ws.onmessage?.({ data: JSON.stringify({ type: 'transcript', segment: { speaker: 'Speaker B', text: 'Second line', start_time: 4, end_time: 6 } }) })
    })

    expect(screen.getByText('Hello world')).toBeInTheDocument()
    expect(screen.getByText('Second line')).toBeInTheDocument()
    expect(screen.queryByText('recording.waitingAudio')).not.toBeInTheDocument()
  })

  it('shows device switched notification and clears it after 5s', async () => {
    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    const ws = wsInstances[wsInstances.length - 1]
    await act(async () => {
      ws.onmessage?.({
        data: JSON.stringify({ type: 'device_switched', old_device: 'Old Mic', new_device: 'New Mic', device_type: 'input' }),
      })
    })

    expect(screen.getByText(/recording.deviceSwitched/)).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })

    expect(screen.queryByText(/recording.deviceSwitched/)).not.toBeInTheDocument()
  })

  it('loads and renders audio device options', async () => {
    const fetchMock = vi.mocked(global.fetch)
    fetchMock.mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : ''
      if (url.endsWith('/api/audio/devices')) {
        return {
          ok: true,
          json: async () => ({
            devices: [
              { id: 'loop-1', name: 'Speakers (Loopback)', is_loopback: true, channels: 2 },
              { id: 'mic-1', name: 'Microphone Array', is_loopback: false, channels: 1 },
            ],
          }),
        } as Response
      }
      return { ok: true, json: async () => ({}) } as Response
    })

    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByRole('option', { name: 'Speakers (Loopback)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Microphone Array' })).toBeInTheDocument()
  })

  it('calls switch-device API when a device select changes', async () => {
    const fetchMock = vi.mocked(global.fetch)
    fetchMock.mockImplementation(async (input, _init) => {
      const url = typeof input === 'string' ? input : ''
      if (url.endsWith('/api/audio/devices')) {
        return {
          ok: true,
          json: async () => ({
            devices: [{ id: 'loop-1', name: 'Speakers (Loopback)', is_loopback: true, channels: 2 }],
          }),
        } as Response
      }
      return { ok: true, json: async () => ({ device_name: 'Speakers (Loopback)' }) } as Response
    })

    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    const selects = document.querySelectorAll('select')
    fireEvent.change(selects[0], { target: { value: 'loop-1' } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    const calls = fetchMock.mock.calls.filter(
      ([input]) => typeof input === 'string' && input.endsWith('/api/record/switch-device'),
    )
    expect(calls.length).toBe(1)
    const body = JSON.parse((calls[0][1] as RequestInit).body as string)
    expect(body.device_type).toBe('loopback')
    expect(body.device_id).toBe('loop-1')
  })

  it('polls signal status and renders RMS values', async () => {
    const fetchMock = vi.mocked(global.fetch)
    fetchMock.mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : ''
      if (url.endsWith('/api/audio/signal-status')) {
        return { ok: true, json: async () => ({ loopback_rms: 0.5, mic_rms: 0.25 }) } as Response
      }
      return { ok: true, json: async () => ({}) } as Response
    })

    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    expect(screen.getByText('0.5000')).toBeInTheDocument()
    expect(screen.getByText('0.2500')).toBeInTheDocument()
  })

  it('collapses and expands the device panel', async () => {
    renderRecording()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    expect(screen.getByText('audio.systemAudio')).toBeInTheDocument()
    expect(screen.getByText('audio.microphone')).toBeInTheDocument()

    fireEvent.click(screen.getByText(/recording.audioDevices/))

    expect(screen.queryByText('audio.systemAudio')).not.toBeInTheDocument()
    expect(screen.queryByText('audio.microphone')).not.toBeInTheDocument()
  })
})
