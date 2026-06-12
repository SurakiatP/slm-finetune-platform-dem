import { api } from '@/api/client'
import type {
  Evaluation,
  EvaluationAccepted,
  EvaluationCompareRequest,
  EvaluationCompareResponse,
  EvaluationCreate,
} from '@/api/types'

const BASE = '/api/v1/evaluations'

export function startEvaluation(body: EvaluationCreate): Promise<EvaluationAccepted> {
  return api.post(BASE, body)
}

export function getEvaluation(id: string): Promise<Evaluation> {
  return api.get(`${BASE}/${id}`)
}

export function compareEvaluations(
  body: EvaluationCompareRequest,
): Promise<EvaluationCompareResponse> {
  return api.post(`${BASE}/compare`, body)
}
