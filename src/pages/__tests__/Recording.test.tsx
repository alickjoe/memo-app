import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

// --- WebSocket mock ---
class MockWebSocket {
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: ((_err: unknown) => void) | null = null
  close = vi.fn()
  send = vi.fn()
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
})
