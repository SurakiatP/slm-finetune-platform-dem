import { api } from '@/api/client'
import type { HealthResponse, ReadinessResponse } from '@/api/types'

export function getHealth(): Promise<HealthResponse> {
  return api.get('/health')
}

export function getReadiness(): Promise<ReadinessResponse> {
  return api.get('/ready')
}

