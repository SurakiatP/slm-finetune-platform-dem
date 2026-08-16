import type { ErrorBody } from '@/api/types'

/** Base URL for the API; empty in dev so the Vite proxy handles /api. */
const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

/** `VITE_MOCK=1` (see `npm run dev:mock`) routes every request through the
 *  in-memory mock store instead of a real backend — see src/mocks/mockEngine.ts.
 *  Lazily imported so the mock module (and its seed data) never ships in a
 *  normal build. */
const isMockMode = import.meta.env.VITE_MOCK === '1'
let mockFetchPromise: Promise<typeof import('@/mocks/mockEngine').mockFetch> | null = null

function doFetch(path: string, init?: RequestInit): Promise<Response> {
  if (!isMockMode) return fetch(`${API_BASE}${path}`, init)
  mockFetchPromise ??= import('@/mocks/mockEngine').then((m) => m.mockFetch)
  return mockFetchPromise.then((mockFetch) => mockFetch(path, init))
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly extra: Record<string, unknown> | null

  constructor(status: number, body: ErrorBody) {
    super(body.detail)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code ?? null
    this.extra = body.extra ?? null
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let body: ErrorBody = { detail: `${res.status} ${res.statusText}` }
  try {
    const json = (await res.json()) as Partial<ErrorBody>
    if (typeof json.detail === 'string') {
      body = { detail: json.detail, code: json.code, extra: json.extra }
    } else if (json.detail !== undefined) {
      // FastAPI validation errors put an array in `detail`.
      body = { detail: JSON.stringify(json.detail), code: 'validation_error' }
    }
  } catch {
    // Non-JSON error body; keep the status line.
  }
  return new ApiError(res.status, body)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // No auth system — requests go out exactly as built, no Authorization header.
  const res = await doFetch(path, init)
  if (!res.ok) throw await parseError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  get<T>(path: string): Promise<T> {
    return request<T>(path)
  },

  post<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  },

  patch<T>(path: string, body: unknown): Promise<T> {
    return request<T>(path, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },

  delete<T = void>(path: string): Promise<T> {
    return request<T>(path, { method: 'DELETE' })
  },

  postForm<T>(path: string, form: FormData): Promise<T> {
    return request<T>(path, { method: 'POST', body: form })
  },
}

export function pageQuery(params: Record<string, string | number | undefined | null>): string {
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') qs.set(key, String(value))
  }
  const s = qs.toString()
  return s ? `?${s}` : ''
}
