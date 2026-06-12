import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, ChevronLeft, ExternalLink } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Link, useParams } from 'react-router-dom'

import { cancelTraining } from '@/api/endpoints/trainings'
import { isTerminalStatus } from '@/api/types'
import { JsonViewer } from '@/components/data/JsonViewer'
import { StatusBadge } from '@/components/data/StatusBadge'
import { JobProgressPanel } from '@/components/jobs/JobProgressPanel'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody } from '@/components/ui/Card'
import { LoadingBlock } from '@/components/ui/Spinner'
import { useToast } from '@/components/ui/toast-context'
import { useMlflowUrl, useTraining, queryKeys } from '@/hooks/queries'
import { useJobProgress, jobRefetchInterval } from '@/hooks/useJobProgress'
import { formatDuration, formatNumber } from '@/lib/format'

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

      <div className="grid gap-4 lg:grid-cols-2">
        <JsonViewer data={training.config_json} title="Training config" />
        {training.best_params_json && <JsonViewer data={training.best_params_json} title="Best params" defaultOpen />}
      </div>
    </div>
  )
}
