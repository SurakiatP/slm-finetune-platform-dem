import { Activity, Boxes, Database, FlaskConical, FolderKanban, Plus } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'

import type { Training } from '@/api/types'
import { DataTable, type Column } from '@/components/data/DataTable'
import { KpiCard } from '@/components/data/KpiCard'
import { StatusBadge } from '@/components/data/StatusBadge'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { useDatasets, useModels, useProjects, useTrainings } from '@/hooks/queries'
import { formatDuration, formatNumber, formatRelativeTime } from '@/lib/format'

export default function DashboardPage() {
  const { data: projects } = useProjects({ limit: 1 })
  const { data: datasets } = useDatasets(undefined, { limit: 1 })
  const { data: models } = useModels(undefined, { limit: 1 })
  const { data: running } = useTrainings({ status: 'running', limit: 10 }, { refetchInterval: 5_000 })
  const { data: pending } = useTrainings({ status: 'pending', limit: 10 }, { refetchInterval: 10_000 })
  const { data: recent, isLoading: recentLoading } = useTrainings({ limit: 8 }, { refetchInterval: 15_000 })
  const navigate = useNavigate()

  const activeJobs = [...(running?.items ?? []), ...(pending?.items ?? [])]

  const recentColumns: Column<Training>[] = [
    {
      key: 'name',
      header: 'Training',
      render: (t) => <span className="font-medium text-body">{t.training_name ?? t.id.slice(0, 8)}</span>,
    },
    { key: 'status', header: 'Status', render: (t) => <StatusBadge status={t.status} /> },
    {
      key: 'mode',
      header: 'Mode',
      render: (t) => <Badge tone={t.mode === 'hpo' ? 'violet' : 'neutral'}>{t.mode}</Badge>,
    },
    {
      key: 'metric',
      header: 'Best metric',
      render: (t) => <span className="font-mono text-xs">{formatNumber(t.best_metric_value)}</span>,
    },
    {
      key: 'created',
      header: 'Created',
      render: (t) => <span className="font-mono text-xs text-body-muted">{formatRelativeTime(t.created_at)}</span>,
    },
  ]

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Synthetic data generation, QLoRA fine-tuning, and evaluation for small language models."
        actions={
          <Link
            to="/projects"
            className="inline-flex h-10 items-center gap-2 rounded-md bg-accent px-4 text-sm font-semibold text-bg transition-colors hover:bg-accent-hover"
          >
            <Plus className="h-4 w-4" aria-hidden />
            New project
          </Link>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard label="Projects" value={projects?.total ?? '…'} icon={FolderKanban} />
        <KpiCard label="Datasets" value={datasets?.total ?? '…'} icon={Database} />
        <KpiCard label="Model artifacts" value={models?.total ?? '…'} icon={Boxes} />
        <KpiCard
          label="Active jobs"
          value={activeJobs.length}
          icon={Activity}
          sub={running?.total ? `${running.total} running` : 'idle'}
        />
      </div>

      {activeJobs.length > 0 && (
        <Card className="mt-6">
          <CardHeader title="Active trainings" description="Live status refreshes every few seconds." />
          <CardBody className="space-y-2">
            {activeJobs.map((t) => (
              <Link
                key={t.id}
                to={`/projects/${t.project_id}/trainings/${t.id}`}
                className="flex items-center justify-between gap-3 rounded-md border border-line/60 bg-bg p-3 transition-colors hover:border-body-muted"
              >
                <span className="flex min-w-0 items-center gap-3">
                  <FlaskConical className="h-4 w-4 shrink-0 text-body-muted" aria-hidden />
                  <span className="truncate text-sm font-medium text-body">
                    {t.training_name ?? t.id.slice(0, 8)}
                  </span>
                  <Badge tone={t.mode === 'hpo' ? 'violet' : 'neutral'}>{t.mode}</Badge>
                </span>
                <span className="flex shrink-0 items-center gap-3">
                  <span className="font-mono text-xs text-body-muted">
                    {formatDuration(t.started_at, null)}
                  </span>
                  <StatusBadge status={t.status} />
                </span>
              </Link>
            ))}
          </CardBody>
        </Card>
      )}

      <Card className="mt-6">
        <CardHeader
          title="Recent trainings"
          actions={
            <Link to="/models" className="text-xs text-info hover:underline">
              View models →
            </Link>
          }
        />
        <CardBody>
          <DataTable
            columns={recentColumns}
            rows={recent?.items ?? []}
            rowKey={(t) => t.id}
            loading={recentLoading}
            onRowClick={(t) => navigate(`/projects/${t.project_id}/trainings/${t.id}`)}
            emptyState={
              <EmptyState
                icon={FlaskConical}
                title="No trainings yet"
                description="Create a project, build a dataset, and start your first fine-tuning run."
                action={
                  <Link to="/projects" className="text-sm font-medium text-accent hover:underline">
                    Go to projects →
                  </Link>
                }
              />
            }
          />
        </CardBody>
      </Card>
    </>
  )
}
