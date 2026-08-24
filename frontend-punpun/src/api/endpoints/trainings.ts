import { api, pageQuery } from '@/api/client'
import type {
  JobStatus,
  MlflowUrlResponse,
  Page,
  Training,
  TrainingJobAccepted,
  TrainingLossHistory,
  TrainingMetrics,
  TrainingRequest,
} from '@/api/types'

const BASE = '/api/v1/trainings'

export function listTrainings(
  params: { project_id?: string; status?: JobStatus; limit?: number; offset?: number } = {},
): Promise<Page<Training>> {
  return api.get(`${BASE}${pageQuery(params)}`)
}

export function getTraining(id: string): Promise<Training> {
  return api.get(`${BASE}/${id}`)
}

export function startTraining(body: TrainingRequest): Promise<TrainingJobAccepted> {
  return api.post(BASE, body)
}

export function cancelTraining(id: string): Promise<{ message: string }> {
  return api.post(`${BASE}/${id}/cancel`)
}

/** Hard-deletes a terminal (completed/failed/cancelled) training run. 409s if
 *  the run is still pending/running — cancel it first. */
export function deleteTraining(id: string): Promise<void> {
  return api.delete(`${BASE}/${id}`)
}

export function getMlflowUrl(id: string): Promise<MlflowUrlResponse> {
  return api.get(`${BASE}/${id}/mlflow-url`)
}

/**
 * Full metric history (all keys) plus HPO child-run summary — superset of
 * /loss-history, which stays as the lightweight chart feed.
 */
export function getTrainingMetrics(id: string): Promise<TrainingMetrics> {
  return api.get(`${BASE}/${id}/metrics`)
}

export function getLossHistory(id: string): Promise<TrainingLossHistory> {
  return api.get(`${BASE}/${id}/loss-history`)
}
