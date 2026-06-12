import { api, pageQuery } from '@/api/client'
import type {
  JobStatus,
  MlflowUrlResponse,
  Page,
  Training,
  TrainingJobAccepted,
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
  return api.delete(`${BASE}/${id}`)
}

export function getMlflowUrl(id: string): Promise<MlflowUrlResponse> {
  return api.get(`${BASE}/${id}/mlflow-url`)
}
