import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, ChevronLeft, ExternalLink } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Link, useParams } from 'react-router-dom'

import { cancelTraining } from '@/api/endpoints/trainings'
import { isTerminalStatus, type HpoChildSummary, type MetricPoint } from '@/api/types'
import { DataTable, type Column } from '@/components/data/DataTable'
import { JsonViewer } from '@/components/data/JsonViewer'
import { StatusBadge } from '@/components/data/StatusBadge'
import { JobProgressPanel } from '@/components/jobs/JobProgressPanel'
import { LossCurveChart } from '@/components/jobs/LossCurveChart'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { LoadingBlock } from '@/components/ui/Spinner'
import { useToast } from '@/components/ui/toast-context'
import { useLossHistory, useMlflowUrl, useTraining, useTrainingMetrics, queryKeys } from '@/hooks/queries'
import { useJobProgress, jobRefetchInterval, type LossPoint } from '@/hooks/useJobProgress'
import { formatDuration, formatNumber, shortId } from '@/lib/format'

interface MetricSummaryRow {
  key: string
  count: number
  latestValue: number | null
  latestStep: number | null
}

export default function TrainingDetailPage() {
  const { trainingId } = useParams<{ trainingId: string }>()
  const queryClient = useQueryClient()
  const toast = useToast()

  // REST is authoritative; WS enriches with per-step detail. The socket state
  // lives in a ref so the lazily-evaluated refetchInterval callback can read it.
  const socketOpenRef = useRef(false)
  const { data: training, isLoading } = useTraining(trainingId!, {
    refetchInterval: (query) => {
      const t = query.state.data
      if (t && isTerminalStatus(t.status)) return false
      return jobRefetchInterval(false, socketOpenRef.current)
    },
  })
  const terminal = training ? isTerminalStatus(training.status) : false

  const progress = useJobProgress(terminal ? null : (training?.celery_task_id ?? null), {
    onTerminal: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.training(trainingId!) })
      void queryClient.invalidateQueries({ queryKey: ['trainings'] })
      void queryClient.invalidateQueries({ queryKey: ['models'] })
    },
  })

  useEffect(() => {
    socketOpenRef.current = progress.socketOpen
  }, [progress.socketOpen])

  const { data: mlflow } = useMlflowUrl(trainingId!, !!training?.mlflow_run_id)

  // The WS only streams while this page is open — backfill the curve from
  // MLflow whenever the socket history is empty (revisits, finished runs).
  const { data: storedLoss } = useLossHistory(
    trainingId!,
    !!training?.mlflow_run_id && progress.lossHistory.length === 0,
  )
  const lossHistory: LossPoint[] =
    progress.lossHistory.length > 0
      ? progress.lossHistory
      : mergeLossHistory(storedLoss?.train_loss ?? [], storedLoss?.eval_loss ?? [])

  const { data: trainingMetrics, isLoading: metricsLoading } = useTrainingMetrics(
    trainingId!,
    !!training?.mlflow_run_id,
  )

  const cancelMutation = useMutation({
    mutationFn: () => cancelTraining(trainingId!),
    onSuccess: () => {
      toast.success('Cancellation requested')
      void queryClient.invalidateQueries({ queryKey: queryKeys.training(trainingId!) })
    },
    onError: (err) => toast.error(err.message),
  })

  if (isLoading || !training) return <LoadingBlock label="Loading training" />

  const modelArtifactId = progress.completed?.model_artifact_id

  const metricRows: MetricSummaryRow[] = trainingMetrics
    ? Object.entries(trainingMetrics.metrics).map(([key, points]) => ({
        key,
        count: points.length,
        latestValue: points.length > 0 ? points[points.length - 1].value : null,
        latestStep: points.length > 0 ? points[points.length - 1].step : null,
      }))
    : []

  const sortedHpoChildren: HpoChildSummary[] | null = trainingMetrics?.hpo_children
    ? [...trainingMetrics.hpo_children].sort((a, b) => {
        if (a.final_eval_loss == null && b.final_eval_loss == null) return 0
        if (a.final_eval_loss == null) return 1
        if (b.final_eval_loss == null) return -1
        return a.final_eval_loss - b.final_eval_loss
      })
    : null
  const bestHpoRunId =
    sortedHpoChildren?.find((child) => child.final_eval_loss != null)?.run_id ?? null

  const hasMetricsData =
    !!trainingMetrics && (metricRows.length > 0 || (sortedHpoChildren?.length ?? 0) > 0)
  const showMetricsCard = metricsLoading || hasMetricsData

  const metricColumns: Column<MetricSummaryRow>[] = [
    { key: 'name', header: 'Metric', render: (row) => <span className="font-mono text-xs">{row.key}</span> },
    { key: 'count', header: 'Points', render: (row) => row.count },
    { key: 'latest', header: 'Latest value', render: (row) => formatNumber(row.latestValue) },
    { key: 'step', header: 'Latest step', render: (row) => row.latestStep ?? '—' },
  ]

  const hpoColumns: Column<HpoChildSummary>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (row) => (
        <span className="inline-flex items-center gap-1.5">
          {row.name}
          {row.run_id === bestHpoRunId && <Badge tone="green">best</Badge>}
        </span>
      ),
    },
    { key: 'run_id', header: 'Run ID', render: (row) => <span className="font-mono text-xs">{shortId(row.run_id)}</span> },
    { key: 'final_eval_loss', header: 'Final eval loss', render: (row) => formatNumber(row.final_eval_loss) },
    {
      key: 'params',
      header: 'Params',
      render: (row) => (
        <div className="flex flex-wrap gap-1">
          {Object.entries(row.params).map(([k, v]) => (
            <span
              key={k}
              className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-body-muted"
            >
              {k}={v}
            </span>
          ))}
        </div>
      ),
    },
  ]

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
            Trainings
          </Link>
          <h2 className="flex flex-wrap items-center gap-2 text-lg font-semibold text-body">
            {training.training_name ?? training.id.slice(0, 8)}
            <Badge tone={training.mode === 'hpo' ? 'violet' : 'neutral'}>{training.mode}</Badge>
            <StatusBadge status={training.status} />
          </h2>
        </div>
        <div className="flex items-center gap-2">
          {mlflow?.mlflow_url && (
            <a
              href={mlflow.mlflow_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-line bg-surface px-3 text-xs text-body transition-colors hover:bg-surface-2"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              MLflow run
            </a>
          )}
          {(training.status === 'running' || training.status === 'pending') && (
            <Button
              variant="danger"
              size="sm"
              onClick={() => cancelMutation.mutate()}
              loading={cancelMutation.isPending}
            >
              <Ban className="h-3.5 w-3.5" aria-hidden />
              Cancel
            </Button>
          )}
        </div>
      </div>

      <Card>
        <CardBody>
          <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs text-body-muted">Base model</dt>
              <dd className="mt-0.5 truncate font-mono text-xs" title={training.base_model}>
                {training.base_model.replace(/^unsloth\//, '')}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-body-muted">Duration</dt>
              <dd className="mt-0.5 font-mono text-xs">{formatDuration(training.started_at, training.ended_at)}</dd>
            </div>
            <div>
              <dt className="text-xs text-body-muted">Best metric</dt>
              <dd className="mt-0.5 font-mono text-xs text-accent">{formatNumber(training.best_metric_value)}</dd>
            </div>
            <div>
              <dt className="text-xs text-body-muted">Dataset</dt>
              <dd className="mt-0.5">
                <Link
                  to={`../../datasets/${training.dataset_id}`}
                  relative="path"
                  className="font-mono text-xs text-info hover:underline"
                >
                  {training.dataset_id.slice(0, 8)}…
                </Link>
              </dd>
            </div>
          </dl>
        </CardBody>
      </Card>

      <JobProgressPanel
        title={training.mode === 'hpo' ? 'HPO progress' : 'Training progress'}
        progress={progress}
        status={training.status}
        errorMessage={training.error_message}
        successAction={
          modelArtifactId && (
            <Link
              to={`/models/${modelArtifactId}`}
              className="inline-flex h-8 items-center rounded-md border border-line bg-surface px-3 text-xs text-body transition-colors hover:bg-surface-2"
            >
              View model artifact
            </Link>
          )
        }
      />

      {!progress.training && !progress.hpo && lossHistory.length > 0 && (
        <Card>
          <CardHeader title="Loss curve" description="Replayed from MLflow metric history." />
          <CardBody>
            <LossCurveChart data={lossHistory} />
          </CardBody>
        </Card>
      )}

      {showMetricsCard && (
        <Card>
          <CardHeader title="Metrics" description="Full metric history recorded to MLflow." />
          <CardBody className="space-y-4">
            <DataTable
              columns={metricColumns}
              rows={metricRows}
              rowKey={(row) => row.key}
              loading={metricsLoading}
            />
            {sortedHpoChildren && (
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-body-muted">HPO trials</h3>
                <DataTable columns={hpoColumns} rows={sortedHpoChildren} rowKey={(row) => row.run_id} />
              </div>
            )}
          </CardBody>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <JsonViewer data={training.config_json} title="Training config" />
        {training.best_params_json && <JsonViewer data={training.best_params_json} title="Best params" defaultOpen />}
      </div>
    </div>
  )
}

function mergeLossHistory(train: MetricPoint[], evals: MetricPoint[]): LossPoint[] {
  const byStep = new Map<number, LossPoint>()
  for (const p of train) byStep.set(p.step, { step: p.step, train_loss: p.value, eval_loss: null })
  for (const p of evals) {
    const existing = byStep.get(p.step)
    if (existing) existing.eval_loss = p.value
    else byStep.set(p.step, { step: p.step, train_loss: null, eval_loss: p.value })
  }
  return [...byStep.values()].sort((a, b) => a.step - b.step)
}
