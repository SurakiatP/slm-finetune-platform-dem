import type { WSMessage } from '@/api/types'

/**
 * Build the WebSocket URL from the current origin so the Vite dev proxy
 * (and any reverse proxy in production) carries the connection. The
 * `websocket_url` returned by the API is intentionally ignored — it points
 * at the backend host directly and would bypass the proxy.
 */
export function jobSocketUrl(jobId: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/ws/jobs/${jobId}`
}

const WS_TYPES = new Set([
  'sdg_progress',
  'training_progress',
  'hpo_progress',
  'completed',
  'failed',
])

export function parseWSMessage(raw: string): WSMessage | null {
  try {
    const msg = JSON.parse(raw) as { type?: string; job_id?: string }
    if (typeof msg.type === 'string' && WS_TYPES.has(msg.type) && typeof msg.job_id === 'string') {
      return msg as WSMessage
    }
  } catch {
    // Malformed frame — ignore; REST polling remains the source of truth.
  }
  return null
}

export function isTerminalMessage(msg: WSMessage): msg is Extract<WSMessage, { type: 'completed' | 'failed' }> {
  return msg.type === 'completed' || msg.type === 'failed'
}
