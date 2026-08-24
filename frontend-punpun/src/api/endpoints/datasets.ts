import { api, pageQuery } from '@/api/client'
import type {
  Dataset,
  DatasetDownloadUrl,
  DatasetInsights,
  DatasetPreview,
  DatasetUpdate,
  JobStatus,
  Page,
  SDGJobAccepted,
  SDGRequest,
  SeedUploadResponse,
  TaskType,
} from '@/api/types'

const BASE = '/api/v1/datasets'

export function listDatasets(
  params: { project_id?: string; limit?: number; offset?: number } = {},
): Promise<Page<Dataset>> {
  return api.get(`${BASE}${pageQuery(params)}`)
}

export function getDataset(id: string): Promise<Dataset> {
  return api.get(`${BASE}/${id}`)
}

export function previewDataset(id: string, limit = 20): Promise<DatasetPreview> {
  return api.get(`${BASE}/${id}/preview${pageQuery({ limit })}`)
}

export function getDatasetInsights(id: string): Promise<DatasetInsights> {
  return api.get(`${BASE}/${id}/insights`)
}

export function deleteDataset(id: string): Promise<void> {
  return api.delete(`${BASE}/${id}`)
}

export function updateDataset(id: string, body: DatasetUpdate): Promise<Dataset> {
  return api.patch(`${BASE}/${id}`, body)
}

/** Plain href for streaming download — use in an <a> tag, not fetch. */
export function datasetDownloadUrl(id: string): string {
  return `${BASE}/${id}/download`
}

/**
 * Idempotent cancel of a running SDG generation job. Already-terminal
 * datasets (including plain SEED uploads, which are always COMPLETED)
 * just report their current status rather than erroring.
 */
export function cancelDatasetGeneration(
  id: string,
): Promise<{ dataset_id: string; status: JobStatus }> {
  return api.post(`${BASE}/${id}/cancel`)
}

/**
 * Mint a presigned MinIO URL for the dataset's stored object. This is the
 * only download surface for PDF-seeded datasets — the streaming
 * `GET /{id}/download` endpoint has no object to stream for those.
 *
 * `disposition: 'inline'` mints a view-in-browser URL (renders the PDF/JSONL
 * in the tab instead of saving it); the disposition is part of the signed
 * query, so it must be chosen at mint time.
 */
export function getDatasetDownloadUrl(
  id: string,
  disposition: 'attachment' | 'inline' = 'attachment',
): Promise<DatasetDownloadUrl> {
  return api.get(
    `${BASE}/${id}/download-url${pageQuery({ disposition: disposition === 'inline' ? disposition : undefined })}`,
  )
}

export function uploadSeedDataset(input: {
  project_id: string
  task_type: TaskType
  file: File
  name?: string
}): Promise<SeedUploadResponse> {
  const form = new FormData()
  form.set('project_id', input.project_id)
  form.set('task_type', input.task_type)
  form.set('file', input.file)
  if (input.name) form.set('name', input.name)
  return api.postForm(`${BASE}/upload-seed`, form)
}

export function generateDataset(body: SDGRequest): Promise<SDGJobAccepted> {
  return api.post(`${BASE}/generate`, body)
}

/**
 * Upload a dataset directly (source `'uploaded'`) — distinct from
 * `uploadSeedDataset`'s `'seed'` source, which is reserved for SDG seed
 * material. The backend returns the same `SeedUploadResponse` shape.
 */
export function uploadDataset(input: {
  project_id: string
  task_type: TaskType
  file: File
  name?: string
}): Promise<SeedUploadResponse> {
  const form = new FormData()
  form.set('project_id', input.project_id)
  form.set('task_type', input.task_type)
  form.set('file', input.file)
  if (input.name) form.set('name', input.name)
  return api.postForm(`${BASE}/upload`, form)
}
