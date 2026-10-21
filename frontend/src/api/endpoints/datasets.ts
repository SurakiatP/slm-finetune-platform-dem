import { ApiError, api, pageQuery } from '@/api/client'
import type {
  Dataset,
  DatasetDownloadUrl,
  DatasetInsights,
  DatasetPreview,
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

export function deleteDataset(id: string): Promise<void> {
  return api.delete(`${BASE}/${id}`)
}

/** Plain href for streaming download — use in an <a> tag, not fetch. */
export function datasetDownloadUrl(id: string): string {
  return `${BASE}/${id}/download`
}

/**
 * Stream the dataset JSONL through the API (same-origin, Bearer-authenticated)
 * and hand it to the browser as a file save. Used when presigned URLs are
 * unavailable (`download-url` -> 503: no public storage host configured).
 * PDF-seeded datasets have nothing to stream and answer 409.
 */
export async function downloadDatasetViaApi(id: string): Promise<void> {
  const { blob, filename } = await api.getBlob(`${BASE}/${id}/download`)
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename ?? `dataset-${id}.jsonl`
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000)
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

export function generateDataset(body: SDGRequest, idempotencyKey?: string): Promise<SDGJobAccepted> {
  return api.post(`${BASE}/generate`, body, { idempotencyKey })
}

export function getDatasetInsights(id: string): Promise<DatasetInsights> {
  return api.get(`${BASE}/${id}/insights`)
}


/**
 * Download a dataset: prefer the presigned URL, and fall back to the
 * API-streamed download when the backend reports presigning is unavailable (503).
 */
export async function downloadDataset(id: string): Promise<void> {
  try {
    const { url } = await getDatasetDownloadUrl(id)
    window.open(url, '_blank', 'noopener,noreferrer')
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      await downloadDatasetViaApi(id)
      return
    }
    throw err
  }
}
