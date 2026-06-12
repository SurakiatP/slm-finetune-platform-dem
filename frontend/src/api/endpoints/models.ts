import { api, pageQuery } from '@/api/client'
import type {
  ArtifactFormat,
  ModelArtifact,
  ModelExportAccepted,
  ModelExportRequest,
  Page,
} from '@/api/types'

const BASE = '/api/v1/models'

export function listModels(
  params: { project_id?: string; limit?: number; offset?: number } = {},
): Promise<Page<ModelArtifact>> {
  return api.get(`${BASE}${pageQuery(params)}`)
}

export function getModel(id: string): Promise<ModelArtifact> {
  return api.get(`${BASE}/${id}`)
}

export function exportModel(id: string, body: ModelExportRequest): Promise<ModelExportAccepted> {
  return api.post(`${BASE}/${id}/export`, body)
}

/** Plain href for streaming download — use in an <a> tag, not fetch. */
export function modelDownloadUrl(id: string, format: ArtifactFormat = 'gguf'): string {
  return `${BASE}/${id}/download?format=${format}`
}
