import { FlaskConical, Plus } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import type { Training } from '@/api/types'
import { DataTable, type Column } from '@/components/data/DataTable'
import { Pagination } from '@/components/data/Pagination'
import { StatusBadge } from '@/components/data/StatusBadge'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { useProjectContext } from '@/features/shared/useProjectContext'
import { NewTrainingModal } from '@/features/trainings/NewTrainingModal'
import { useTrainings } from '@/hooks/queries'
import { formatDuration, formatNumber, formatRelativeTime } from '@/lib/format'

const PAGE_SIZE = 20

export default function TrainingListPage() {
  const { project } = useProjectContext()
  const [offset, setOffset] = useState(0)
  // Refresh periodically — rows flip from running to completed without user action.
  const { data, isLoading } = useTrainings(
    { project_id: project.id, limit: PAGE_SIZE, offset },
    { refetchInterval: 10_000 },
  )
  const [newOpen, setNewOpen] = useState(false)
  const navigate = useNavigate()

  const columns: Column<Training>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (t) => (
        <span className="font-medium text-body">{t.training_name ?? t.id.slice(0, 8)}</span>
      ),
    },
    { key: 'status', header: 'Status', render: (t) => <StatusBadge status={t.status} /> },
    {
      key: 'mode',
      header: 'Mode',
      render: (t) => <Badge tone={t.mode === 'hpo' ? 'violet' : 'neutral'}>{t.mode}</Badge>,
    },
    {
      key: 'base_model',
      header: 'Base model',
      className: 'max-w-56',
      render: (t) => (
        <span className="block truncate font-mono text-xs text-body-muted" title={t.base_model}>
          {t.base_model.replace(/^unsloth\//, '')}
        </span>
      ),
    },
    {
      key: 'metric',
      header: 'Best metric',
      render: (t) => <span className="font-mono text-xs">{formatNumber(t.best_metric_value)}</span>,
    },
    {
      key: 'duration',
      header: 'Duration',
      render: (t) => (
        <span className="font-mono text-xs text-body-muted">{formatDuration(t.started_at, t.ended_at)}</span>
      ),
    },
    {
      key: 'created',
      header: 'Created',
      render: (t) => <span className="font-mono text-xs text-body-muted">{formatRelativeTime(t.created_at)}</span>,
    },
  ]

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-body-muted">
          Fine-tune with fixed hyperparameters, or let Optuna search for the best ones.
        </p>
        <Button size="sm" onClick={() => setNewOpen(true)}>
          <Plus className="h-3.5 w-3.5" aria-hidden />
          New training
        </Button>
      </div>

      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(t) => t.id}
        loading={isLoading}
        onRowClick={(t) => navigate(t.id)}
        emptyState={
          <EmptyState
            icon={FlaskConical}
            title="No trainings yet"
            description="Pick a dataset and start a fine-tuning run. Progress streams live while the GPU worker trains."
            action={
              <Button onClick={() => setNewOpen(true)}>
                <Plus className="h-4 w-4" aria-hidden />
                New training
              </Button>
            }
          />
        }
      />

      {data && (
        <Pagination total={data.total} limit={data.limit} offset={data.offset} onOffsetChange={setOffset} />
      )}

      <NewTrainingModal project={project} open={newOpen} onClose={() => setNewOpen(false)} />
    </>
  )
}
