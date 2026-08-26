import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getSession: vi.fn(),
  getJobProgress: vi.fn(),
}))

vi.mock('@/integrations/supabase/client', () => ({
  supabase: { auth: { getSession: mocks.getSession } },
}))
vi.mock('@/api/endpoints/jobs', () => ({ getJobProgress: mocks.getJobProgress }))

import { jobSocketUrl } from '@/api/ws'
import { useJobProgress } from '@/hooks/useJobProgress'

class FakeWebSocket {
  static OPEN = 1
  static instances: FakeWebSocket[] = []

  readyState = 0
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  constructor(readonly url: string, readonly protocols?: string | string[]) {
    FakeWebSocket.instances.push(this)
  }

  close() {}
}

describe('useJobProgress', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    FakeWebSocket.instances = []
    mocks.getSession.mockResolvedValue({ data: { session: { access_token: 'jwt-token' } } })
    mocks.getJobProgress.mockRejectedValue(new Error('no snapshot'))
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
  })

  afterEach(() => vi.useRealTimers())

  it('encodes job ids in same-origin WebSocket URLs', () => {
    expect(jobSocketUrl('job id')).toContain('/ws/jobs/job%20id')
  })

  it('offers the bearer subprotocol and distinguishes authorization failures', async () => {
    const { result, unmount } = renderHook(() => useJobProgress('job-1'))
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1))
    const socket = FakeWebSocket.instances[0]
    expect(socket.protocols).toEqual(['bearer', 'jwt-token'])

    act(() => socket.onopen?.(new Event('open')))
    expect(result.current.socketOpen).toBe(true)

    act(() => socket.onclose?.({ code: 4401 } as CloseEvent))
    expect(result.current.connectionError).toBe('unauthorized')
    unmount()
  })

  it('distinguishes forbidden sockets from unauthenticated sockets', async () => {
    const { result, unmount } = renderHook(() => useJobProgress('job-2'))
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1))

    act(() => FakeWebSocket.instances[0].onclose?.({ code: 4403 } as CloseEvent))
    expect(result.current.connectionError).toBe('forbidden')
    unmount()
  })

  it('hydrates the latest progress from REST when the socket has no replay', async () => {
    mocks.getJobProgress.mockResolvedValue({
      type: 'training_progress',
      job_id: 'job-3',
      timestamp: '2026-08-24T00:00:00Z',
      epoch: 1,
      epochs_total: 3,
      step: 20,
      steps_total: 60,
      train_loss: 0.8,
      eval_loss: null,
      learning_rate: 0.0002,
      samples_per_second: 5,
      gpu_memory_mb: 8000,
    })

    const { result, unmount } = renderHook(() => useJobProgress('job-3'))
    await waitFor(() => expect(result.current.training?.step).toBe(20))
    expect(result.current.lossHistory).toEqual([
      { step: 20, train_loss: 0.8, eval_loss: null },
    ])
    unmount()
  })

  it('reconnects after a transient network close', async () => {
    vi.useFakeTimers()
    const { result, unmount } = renderHook(() => useJobProgress('job-4'))
    await act(async () => {
      await Promise.resolve()
    })
    expect(FakeWebSocket.instances).toHaveLength(1)

    act(() => FakeWebSocket.instances[0].onclose?.({ code: 1006 } as CloseEvent))
    expect(result.current.connectionError).toBe('network')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(FakeWebSocket.instances).toHaveLength(2)

    unmount()
    vi.useRealTimers()
  })
})
