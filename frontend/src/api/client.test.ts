import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getSession: vi.fn(),
  fetch: vi.fn(),
}))

vi.mock('@/integrations/supabase/client', () => ({
  supabase: { auth: { getSession: mocks.getSession } },
}))

import { api, ApiError } from '@/api/client'

describe('Engine API client', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.getSession.mockResolvedValue({ data: { session: { access_token: 'jwt-token' } } })
    vi.stubGlobal('fetch', mocks.fetch)
  })

  it('attaches the Supabase JWT and parses JSON', async () => {
    mocks.fetch.mockResolvedValue(new Response(JSON.stringify({ status: 'ok' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    await expect(api.get<{ status: string }>('/api/v1/projects')).resolves.toEqual({ status: 'ok' })
    const [, init] = mocks.fetch.mock.calls[0] as [string, RequestInit]
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer jwt-token')
  })

  it('calls public probes without requiring a session', async () => {
    mocks.getSession.mockResolvedValue({ data: { session: null } })
    mocks.fetch.mockResolvedValue(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))

    await expect(api.get<{ status: string }>('/health')).resolves.toEqual({ status: 'ok' })
    const [, init] = mocks.fetch.mock.calls[0] as [string, RequestInit]
    expect(mocks.getSession).not.toHaveBeenCalled()
    expect(new Headers(init.headers).get('Authorization')).toBeNull()
  })

  it('does not set a multipart content type and handles 204', async () => {
    mocks.fetch.mockResolvedValue(new Response(null, { status: 204 }))
    const form = new FormData()
    form.set('file', new File(['row'], 'seed.jsonl'))

    await expect(api.postForm('/api/v1/datasets/upload-seed', form)).resolves.toBeUndefined()
    const [, init] = mocks.fetch.mock.calls[0] as [string, RequestInit]
    expect(new Headers(init.headers).get('Content-Type')).toBeNull()
  })

  it('normalizes FastAPI validation arrays and preserves request ids', async () => {
    mocks.fetch.mockResolvedValue(new Response(JSON.stringify({ detail: [{ loc: ['body'], msg: 'invalid' }] }), {
      status: 422,
      headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-1' },
    }))

    const error = await api.post('/api/v1/trainings', {}).catch((value: unknown) => value)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 422, code: 'validation_error', requestId: 'req-1' })
  })

  it('fails locally when no authenticated session exists', async () => {
    mocks.getSession.mockResolvedValue({ data: { session: null } })
    const error = await api.get('/api/v1/projects').catch((value: unknown) => value)
    expect(error).toMatchObject({ status: 401, code: 'missing_session' })
    expect(mocks.fetch).not.toHaveBeenCalled()
  })

  it.each([
    [401, 'token_expired'],
    [409, 'resource_in_use'],
  ])('preserves structured Engine errors for status %i', async (status, code) => {
    mocks.fetch.mockResolvedValue(new Response(JSON.stringify({
      detail: 'Engine rejected the request',
      code,
      extra: { resource_id: 'resource-1' },
    }), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }))

    const error = await api.delete('/api/v1/datasets/resource-1').catch((value: unknown) => value)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status,
      code,
      message: 'Engine rejected the request',
      extra: { resource_id: 'resource-1' },
    })
  })

  it('sends an explicit idempotency key on submit requests', async () => {
    mocks.fetch.mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await api.post('/api/v1/evaluations', {}, { idempotencyKey: 'idem-1' })
    const [, init] = mocks.fetch.mock.calls[0] as [string, RequestInit]
    expect(new Headers(init.headers).get('Idempotency-Key')).toBe('idem-1')
  })
})
