import { api } from '@/api/client'
import type { UsageSummaryResponse } from '@/api/types'

const BASE = '/api/v1/usage'

/**
 * The caller's own cross-project usage/cost rollup for the current UTC
 * calendar month, grouped by (model, stage).
 *
 * With auth disabled (no auth-provider env vars configured, i.e.
 * `AUTH_REQUIRED=false`), the backend resolves the actor to `None` and this rolls up only the
 * anonymous bucket (`UsageEvent.actor_id IS NULL`) rather than every
 * user's spend — see api/routers/usage.py:38-54.
 */
export function getUsageSummary(): Promise<UsageSummaryResponse> {
  return api.get(BASE)
}
