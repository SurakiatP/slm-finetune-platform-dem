import { api, pageQuery } from '@/api/client'
import type { Page, Template, TemplateRatingResponse, TemplateSort } from '@/api/types'

const BASE = '/api/v1/templates'

/** Backend-owned marketplace catalogue. Search and filters are server-side so
 * the visible availability flags and ratings never drift from the Engine. */
export function listTemplates(
  params: {
    category?: string
    featured?: boolean
    search?: string
    sort?: TemplateSort
    include_unavailable?: boolean
    limit?: number
    offset?: number
  } = {},
): Promise<Page<Template>> {
  return api.get(`${BASE}${pageQuery(params)}`)
}

/** Sets the current user's rating. The Engine validates the 1–5 range. */
export function rateTemplate(id: string, rating: number): Promise<TemplateRatingResponse> {
  return api.put(`${BASE}/${id}/rating`, { rating })
}
