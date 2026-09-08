import { api } from '@/api/client'
import type { UsageSummaryResponse } from '@/api/types'

const BASE = '/api/v1/usage'

/**
 * The caller's own cross-project usage/cost rollup for the current UTC
 * calendar month, grouped by (model, stage).
 *
 * The API client always sends the current Supabase JWT, so this matches the
 * per-actor budget enforced by the backend in production.
 */
export function getUsageSummary(): Promise<UsageSummaryResponse> {
  return api.get(BASE)
}
