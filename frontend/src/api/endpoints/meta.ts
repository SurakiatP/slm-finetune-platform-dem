import { api } from '@/api/client'
import type { BaseModelInfo, SdgPipelineModels, TaskType, TaskTypeInfo } from '@/api/types'

export function listTaskTypes(): Promise<TaskTypeInfo[]> {
  return api.get('/api/v1/tasks')
}

export function getTaskExample(taskType: TaskType): Promise<Record<string, unknown>> {
  return api.get(`/api/v1/tasks/${taskType}/example`)
}

export function listBaseModels(): Promise<BaseModelInfo[]> {
  return api.get('/api/v1/base-models')
}

export function getSdgPipelineModels(): Promise<SdgPipelineModels> {
  return api.get('/api/v1/sdg-pipeline')
}
