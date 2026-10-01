import { api, pageQuery } from '@/api/client'
import type { AnalyticsResponse } from '@/api/types'

export function getAnalytics(params: { from?: string; to?: string; project_id?: string } = {}): Promise<AnalyticsResponse> {
  return api.get(`/api/v1/analytics${pageQuery(params)}`)
}
