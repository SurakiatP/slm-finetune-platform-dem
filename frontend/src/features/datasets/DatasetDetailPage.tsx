import { ChevronLeft, Download } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { datasetDownloadUrl } from '@/api/endpoints/datasets'
import { JsonlPreview } from '@/components/data/JsonlPreview'
import { JsonViewer } from '@/components/data/JsonViewer'
import { SourceBadge } from '@/components/data/SourceBadge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { LoadingBlock } from '@/components/ui/Spinner'
import { useDataset, useDatasetPreview } from '@/hooks/queries'
import { formatBytes, formatDateTime } from '@/lib/format'

export default function DatasetDetailPage() {
  const { datasetId } = useParams<{ datasetId: string }>()
  const { data: dataset, isLoading } = useDataset(datasetId!)
  const { data: preview } = useDatasetPreview(datasetId!, 20, !!dataset && dataset.num_samples > 0)

  if (isLoading || !dataset) return <LoadingBlock label="Loading dataset" />

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
          </h2>
        </div>
        <a
          href={datasetDownloadUrl(dataset.id)}
          download
          className="inline-flex h-8 cursor-pointer items-center gap-2 rounded-md border border-line bg-surface px-3 text-xs text-body transition-colors hover:bg-surface-2"
        >
          <Download className="h-3.5 w-3.5" aria-hidden />
          Download JSONL
        </a>
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
              No rows yet — generation may still be running.
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
