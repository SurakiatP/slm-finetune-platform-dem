import { api, pageQuery } from '@/api/client'
import type {
  ArtifactFormat,
  JobStatus,
  ModelArtifact,
  ModelDownloadUrl,
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

export function deleteModel(id: string): Promise<void> {
  return api.delete(`${BASE}/${id}`)
}

export function exportModel(id: string, body: ModelExportRequest): Promise<ModelExportAccepted> {
  return api.post(`${BASE}/${id}/export`, body)
}

/** Plain href for streaming download — use in an <a> tag, not fetch. */
export function modelDownloadUrl(id: string, format: ArtifactFormat = 'gguf'): string {
  return `${BASE}/${id}/download?format=${format}`
}

/**
 * Idempotent cancel of an in-progress model export. 409s if no export was
 * ever requested (`export_status` is null); already-terminal exports just
 * report their current status rather than erroring.
 */
export function cancelModelExport(id: string): Promise<{ artifact_id: string; status: JobStatus }> {
  return api.post(`${BASE}/${id}/export/cancel`)
}

/**
 * Mint presigned MinIO URL(s) for a previously-exported artifact. This is
 * the ONLY path serving `safetensors`/`lora` formats — the streaming
 * `GET /{id}/download` endpoint 400s on those (they're multi-file
 * directories, not a single stream-able object).
 */
export function getModelDownloadUrl(
  id: string,
  format: ArtifactFormat = 'gguf',
): Promise<ModelDownloadUrl> {
  return api.get(`${BASE}/${id}/download-url${pageQuery({ format })}`)
}
