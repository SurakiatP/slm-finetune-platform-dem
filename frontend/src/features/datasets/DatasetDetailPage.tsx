import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, ChevronLeft, Download } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { isTerminalStatus } from '@/api/types'
import { cancelDatasetGeneration, getDatasetDownloadUrl } from '@/api/endpoints/datasets'
import { JsonlPreview } from '@/components/data/JsonlPreview'
import { JsonViewer } from '@/components/data/JsonViewer'
import { SourceBadge } from '@/components/data/SourceBadge'
import { StatusBadge } from '@/components/data/StatusBadge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { LoadingBlock } from '@/components/ui/Spinner'
import { useToast } from '@/components/ui/toast-context'
import { DatasetLiveProgress } from '@/features/datasets/DatasetLiveProgress'
import { useDataset, useDatasetPreview, queryKeys } from '@/hooks/queries'
import { formatBytes, formatDateTime } from '@/lib/format'

export default function DatasetDetailPage() {
  const { datasetId } = useParams<{ datasetId: string }>()
  const toast = useToast()
  const queryClient = useQueryClient()
  const { data: dataset, isLoading } = useDataset(datasetId!, {
    refetchInterval: (query) => (query.state.data && isTerminalStatus(query.state.data.status) ? false : 3_000),
  })
  const { data: preview } = useDatasetPreview(datasetId!, 20, !!dataset && dataset.num_samples > 0)

  const downloadMutation = useMutation({
    mutationFn: () => getDatasetDownloadUrl(datasetId!),
    onSuccess: (res) => {
      const a = document.createElement('a')
      a.href = res.url
      a.download = res.filename
      document.body.appendChild(a)
      a.click()
      a.remove()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const cancelMutation = useMutation({
    mutationFn: () => cancelDatasetGeneration(datasetId!),
    onSuccess: () => {
      toast.success('Cancellation requested')
      void queryClient.invalidateQueries({ queryKey: queryKeys.dataset(datasetId!) })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  if (isLoading || !dataset) return <LoadingBlock label="Loading dataset" />

  // WS channel key for the SDG job == the Celery task id, persisted on the row.
  // Only meaningful while generation is in flight (the WS has no replay).
  const sdgJobId =
    dataset.source === 'sdg' &&
    !isTerminalStatus(dataset.status) &&
    typeof dataset.generation_metadata?.celery_task_id === 'string'
      ? dataset.generation_metadata.celery_task_id
      : null

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link
            to=".."
            relative="path"
            className="mb-1 inline-flex items-center gap-1 text-xs text-body-muted transition-colors hover:text-body"
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
            Datasets
          </Link>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-body">
            {dataset.name}
            <SourceBadge source={dataset.source} />
            <StatusBadge status={dataset.status} />
          </h2>
        </div>
        <div className="flex items-center gap-2">
          {dataset.source === 'sdg' && !isTerminalStatus(dataset.status) && (
            <Button
              variant="danger"
              size="sm"
              onClick={() => cancelMutation.mutate()}
              loading={cancelMutation.isPending}
            >
              <Ban className="h-3.5 w-3.5" aria-hidden />
              Cancel generation
            </Button>
          )}
          <button
            type="button"
            onClick={() => downloadMutation.mutate()}
            disabled={downloadMutation.isPending}
            className="inline-flex h-8 cursor-pointer items-center gap-2 rounded-md border border-line bg-surface px-3 text-xs text-body transition-colors hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
            Download JSONL
          </button>
        </div>
      </div>

      <Card>
        <CardBody>
          <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs text-body-muted">Rows</dt>
              <dd className="mt-0.5 font-mono">{dataset.num_samples.toLocaleString()}</dd>
            </div>
            <div>
              <dt className="text-xs text-body-muted">Size</dt>
              <dd className="mt-0.5 font-mono">{formatBytes(dataset.size_bytes)}</dd>
            </div>
            <div>
              <dt className="text-xs text-body-muted">Created</dt>
              <dd className="mt-0.5 font-mono text-xs">{formatDateTime(dataset.created_at)}</dd>
            </div>
            <div>
              <dt className="text-xs text-body-muted">Storage</dt>
              <dd className="mt-0.5 truncate font-mono text-xs text-body-muted" title={dataset.storage_uri ?? undefined}>
                {dataset.storage_uri ?? '—'}
              </dd>
            </div>
          </dl>
        </CardBody>
      </Card>

      {sdgJobId && <DatasetLiveProgress dataset={dataset} jobId={sdgJobId} />}

      {dataset.status === 'failed' && dataset.error_message && (
        <Card>
          <CardBody>
            <p className="text-xs text-red-400">{dataset.error_message}</p>
          </CardBody>
        </Card>
      )}

      {dataset.generation_metadata && (
        <JsonViewer data={dataset.generation_metadata} title="Generation metadata" />
      )}

      <Card>
        <CardHeader
          title="Preview"
          description={
            preview ? `First ${preview.samples.length} of ${preview.total} rows` : 'First rows of the dataset'
          }
        />
        <CardBody>
          {dataset.num_samples === 0 ? (
            <p className="py-6 text-center text-xs text-body-muted">
              {dataset.status === 'failed'
                ? 'Generation failed — no rows were produced.'
                : dataset.status === 'pending' || dataset.status === 'running'
                  ? 'No rows yet — generation is still running.'
                  : 'No rows.'}
            </p>
          ) : preview ? (
            <JsonlPreview taskType={preview.task_type} samples={preview.samples} />
          ) : (
            <LoadingBlock label="Loading preview" />
          )}
        </CardBody>
      </Card>
    </div>
  )
}
