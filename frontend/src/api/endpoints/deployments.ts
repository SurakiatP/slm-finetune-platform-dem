import { api, pageQuery } from '@/api/client'
import type { Deployment, JobStatus, Page } from '@/api/types'

const BASE = '/api/v1/deployments'

export function listDeployments(params: { status?: JobStatus; limit?: number; offset?: number } = {}): Promise<Page<Deployment>> {
  return api.get(`${BASE}${pageQuery(params)}`)
}

export function createDeployment(model_artifact_id: string, idempotencyKey: string): Promise<Deployment> {
  return api.post(BASE, { model_artifact_id }, { idempotencyKey })
}

export function stopDeployment(id: string): Promise<Deployment> {
  return api.post(`${BASE}/${id}/stop`)
}
