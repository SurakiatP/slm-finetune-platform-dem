import { api } from '@/api/client'
import type { WSMessage } from '@/api/types'

const BASE = '/api/v1/jobs'

/** Latest progress frame Redis holds for a job — same discriminated union the WS pushes; 404 when no snapshot exists yet. */
export function getJobProgress(jobId: string): Promise<WSMessage> {
  return api.get(`${BASE}/${jobId}/progress`)
}
