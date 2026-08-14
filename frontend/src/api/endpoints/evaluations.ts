import { api, pageQuery } from '@/api/client'
import type {
  Evaluation,
  EvaluationAccepted,
  EvaluationCompareRequest,
  EvaluationCompareResponse,
  EvaluationCreate,
  JobStatus,
  Page,
} from '@/api/types'

const BASE = '/api/v1/evaluations'

export function startEvaluation(body: EvaluationCreate): Promise<EvaluationAccepted> {
  return api.post(BASE, body)
}

/** Newest-first; server-side filters by model artifact, dataset, and/or status. */
export function listEvaluations(
  params: {
    model_artifact_id?: string
    dataset_id?: string
    status?: JobStatus
    limit?: number
    offset?: number
  } = {},
): Promise<Page<Evaluation>> {
  return api.get(`${BASE}${pageQuery(params)}`)
}

export function getEvaluation(id: string): Promise<Evaluation> {
  return api.get(`${BASE}/${id}`)
}

/** Idempotent — cancelling an already-terminal evaluation just echoes its current status. */
export function cancelEvaluation(id: string): Promise<{ evaluation_id: string; status: JobStatus }> {
  return api.post(`${BASE}/${id}/cancel`)
}

export function compareEvaluations(
  body: EvaluationCompareRequest,
): Promise<EvaluationCompareResponse> {
  return api.post(`${BASE}/compare`, body)
}
