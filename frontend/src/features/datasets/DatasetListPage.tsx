import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, Database, Download, Plus, Sparkles, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { cancelDatasetGeneration, deleteDataset, getDatasetDownloadUrl } from '@/api/endpoints/datasets'
import { isTerminalStatus, type Dataset } from '@/api/types'
import { DataTable, type Column } from '@/components/data/DataTable'
import { Pagination } from '@/components/data/Pagination'
import { SourceBadge } from '@/components/data/SourceBadge'
import { StatusBadge } from '@/components/data/StatusBadge'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { useToast } from '@/components/ui/toast-context'
import { ActiveSdgJobCard } from '@/features/datasets/ActiveSdgJobCard'
import { GenerateDatasetModal, type SdgJobRef } from '@/features/datasets/GenerateDatasetModal'
import { UploadSeedModal } from '@/features/datasets/UploadSeedModal'
import { useProjectContext } from '@/features/shared/useProjectContext'
import { useDatasets, queryKeys } from '@/hooks/queries'
import { formatBytes, formatRelativeTime } from '@/lib/format'

const PAGE_SIZE = 20

export default function DatasetListPage() {
  const { project } = useProjectContext()
  const [offset, setOffset] = useState(0)
  const { data, isLoading } = useDatasets(
    project.id,
    { limit: PAGE_SIZE, offset },
    { refetchInterval: (query) => (query.state.data?.items.some((d) => !isTerminalStatus(d.status)) ? 5_000 : false) },
  )
  const [uploadOpen, setUploadOpen] = useState(false)
  const [generateOpen, setGenerateOpen] = useState(false)
  const [activeJob, setActiveJob] = useState<SdgJobRef | null>(null)
  const [toDelete, setToDelete] = useState<Dataset | null>(null)
  const [toCancel, setToCancel] = useState<Dataset | null>(null)
  const navigate = useNavigate()
  const toast = useToast()
  const queryClient = useQueryClient()

  const downloadMutation = useMutation({
    mutationFn: (id: string) => getDatasetDownloadUrl(id),
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
    mutationFn: (id: string) => cancelDatasetGeneration(id),
    onSuccess: () => {
      toast.success('Cancellation requested')
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets(project.id) })
      if (toCancel) void queryClient.invalidateQueries({ queryKey: queryKeys.dataset(toCancel.id) })
      setToCancel(null)
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const columns: Column<Dataset>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (d) => <span className="font-medium text-body">{d.name}</span>,
    },
    {
      key: 'source',
      header: 'Source',
      render: (d) => (
        <span className="flex gap-1">
          <SourceBadge source={d.source} />
          {d.parent_dataset_id && <Badge tone="violet">holdout</Badge>}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (d) => <StatusBadge status={d.status} />,
    },
    {
      key: 'samples',
      header: 'Rows',
      render: (d) => <span className="font-mono text-xs">{d.num_samples.toLocaleString()}</span>,
    },
    {
      key: 'size',
      header: 'Size',
      render: (d) => <span className="font-mono text-xs text-body-muted">{formatBytes(d.size_bytes)}</span>,
    },
    {
      key: 'created',
      header: 'Created',
      render: (d) => <span className="font-mono text-xs text-body-muted">{formatRelativeTime(d.created_at)}</span>,
    },
    {
      key: 'actions',
      header: <span className="sr-only">Actions</span>,
      className: 'w-24 text-right',
      render: (d) => (
        <span className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          <button
            type="button"
            aria-label={`Download ${d.name}`}
            onClick={() => downloadMutation.mutate(d.id)}
            disabled={downloadMutation.isPending && downloadMutation.variables === d.id}
            className="cursor-pointer rounded p-1.5 text-body-muted transition-colors hover:bg-surface-2 hover:text-body disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Download className="h-4 w-4" aria-hidden />
          </button>
          {(d.status === 'pending' || d.status === 'running') && (
            <button
              type="button"
              aria-label={`Cancel generation for ${d.name}`}
              onClick={() => setToCancel(d)}
              className="cursor-pointer rounded p-1.5 text-body-muted transition-colors hover:bg-danger-muted hover:text-danger"
            >
              <Ban className="h-4 w-4" aria-hidden />
            </button>
          )}
          <button
            type="button"
            aria-label={`Delete ${d.name}`}
            onClick={() => setToDelete(d)}
            className="cursor-pointer rounded p-1.5 text-body-muted transition-colors hover:bg-danger-muted hover:text-danger"
          >
            <Trash2 className="h-4 w-4" aria-hidden />
          </button>
        </span>
      ),
    },
  ]

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="sr-only">Datasets</h2>
        <p className="text-xs text-body-muted">
          Upload seed examples or generate synthetic data with a teacher model.
        </p>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={() => setUploadOpen(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden />
            Upload seed
          </Button>
          <Button size="sm" onClick={() => setGenerateOpen(true)}>
            <Sparkles className="h-3.5 w-3.5" aria-hidden />
            Generate (SDG)
          </Button>
        </div>
      </div>

      {activeJob && (
        <div className="mb-4">
          <ActiveSdgJobCard job={activeJob} onDismiss={() => setActiveJob(null)} />
        </div>
      )}

      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(d) => d.id}
        loading={isLoading}
        onRowClick={(d) => navigate(d.id)}
        emptyState={
          <EmptyState
            icon={Database}
            title="No datasets yet"
            description="Start by uploading 5–50 seed examples, or generate synthetic data from a task description."
            action={
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => setUploadOpen(true)}>
                  Upload seed
                </Button>
                <Button onClick={() => setGenerateOpen(true)}>
                  <Sparkles className="h-4 w-4" aria-hidden />
                  Generate (SDG)
                </Button>
              </div>
            }
          />
        }
      />

      {data && (
        <Pagination total={data.total} limit={data.limit} offset={data.offset} onOffsetChange={setOffset} />
      )}

      <UploadSeedModal project={project} open={uploadOpen} onClose={() => setUploadOpen(false)} />
      <GenerateDatasetModal
        project={project}
        open={generateOpen}
        onClose={() => setGenerateOpen(false)}
        onJobStarted={setActiveJob}
      />

      <ConfirmDialog
        open={toDelete !== null}
        onClose={() => setToDelete(null)}
        onConfirm={() => {
          if (!toDelete) return
          deleteDataset(toDelete.id)
            .then(() => {
              toast.success(`Dataset "${toDelete.name}" deleted`)
              void queryClient.invalidateQueries({ queryKey: queryKeys.datasets(project.id) })
            })
            .catch((err: Error) => toast.error(err.message))
            .finally(() => setToDelete(null))
        }}
        title={`Delete "${toDelete?.name}"?`}
        body={<p>Trainings that already used this dataset keep their results, but the rows are gone for good.</p>}
        confirmLabel="Delete dataset"
      />

      <ConfirmDialog
        open={toCancel !== null}
        onClose={() => setToCancel(null)}
        onConfirm={() => {
          if (!toCancel) return
          cancelMutation.mutate(toCancel.id)
        }}
        title={`Cancel generation for "${toCancel?.name}"?`}
        body={<p>The dataset will stop generating and keep whatever rows were produced so far.</p>}
        confirmLabel="Cancel generation"
        loading={cancelMutation.isPending}
      />
    </>
  )
}
