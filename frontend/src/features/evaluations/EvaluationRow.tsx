import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, ChevronDown, ChevronRight } from 'lucide-react'
import { useState } from 'react'

import { cancelEvaluation } from '@/api/endpoints/evaluations'
import { isTerminalStatus, type Evaluation } from '@/api/types'
import { ConfusionMatrix } from '@/components/charts/ConfusionMatrix'
import { MetricBars } from '@/components/charts/MetricBars'
import { JsonViewer } from '@/components/data/JsonViewer'
import { StatusBadge } from '@/components/data/StatusBadge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody } from '@/components/ui/Card'
import { useToast } from '@/components/ui/toast-context'
import { formatDuration, formatNumber, formatRelativeTime, shortId } from '@/lib/format'
import { extractConfusionMatrix, scalarMetrics } from '@/lib/metrics'

interface EvaluationRowProps {
  evaluation: Evaluation
  selected: boolean
  onToggleSelect: () => void
}

/** One row from the project's evaluation list — the list is authoritative, no per-row fetch. */
export function EvaluationRow({ evaluation, selected, onToggleSelect }: EvaluationRowProps) {
  const [expanded, setExpanded] = useState(false)
  const toast = useToast()
  const queryClient = useQueryClient()
  const evaluationId = evaluation.id

  const cancelMutation = useMutation({
    mutationFn: () => cancelEvaluation(evaluationId),
    onSuccess: () => {
      toast.success('Cancellation requested')
      void queryClient.invalidateQueries({ queryKey: ['evaluations'] })
    },
    onError: (err) => toast.error(err.message),
  })

  const terminal = isTerminalStatus(evaluation.status)
  const cancellable = evaluation.status === 'running' || evaluation.status === 'pending'
  const baseMetrics = evaluation.metrics_json ? scalarMetrics(evaluation.metrics_json) : {}
  // The judge score lives on its own column (not in metrics_json); fold it into
  // the bar list so every metric renders uniformly with its range.
  const metrics =
    evaluation.llm_judge_score !== null
      ? { ...baseMetrics, llm_judge_score: evaluation.llm_judge_score }
      : baseMetrics
  const confusion = evaluation.metrics_json ? extractConfusionMatrix(evaluation.metrics_json) : null

  return (
    <Card>
      <div className="flex items-center gap-3 p-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          disabled={evaluation.status !== 'completed'}
          aria-label={`Select evaluation ${shortId(evaluationId)} for comparison`}
          className="h-4 w-4 shrink-0 accent-[#22C55E] disabled:opacity-40"
        />
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left"
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4 shrink-0 text-body-muted" aria-hidden />
          ) : (
            <ChevronRight className="h-4 w-4 shrink-0 text-body-muted" aria-hidden />
          )}
          <span className="font-mono text-xs text-body">{shortId(evaluationId)}</span>
          <StatusBadge status={evaluation.status} />
          {evaluation.llm_judge_score !== null && (
            <span className="font-mono text-xs text-body-muted">
              judge {formatNumber(evaluation.llm_judge_score, 2)}/5
            </span>
          )}
          <span className="ml-auto hidden font-mono text-[11px] text-body-muted sm:inline">
            {terminal
              ? formatDuration(evaluation.started_at, evaluation.ended_at)
              : formatRelativeTime(evaluation.created_at)}
          </span>
        </button>
        {cancellable && (
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

      {expanded && (
        <CardBody className="space-y-4 border-t border-line/60">
          {evaluation.error_message && (
            <p role="alert" className="rounded-md border border-danger/40 bg-danger-muted p-3 text-xs text-body">
              {evaluation.error_message}
            </p>
          )}

          {Object.keys(metrics).length > 0 ? (
            <div className="space-y-1.5">
              <MetricBars metrics={metrics} />
              <p className="text-[11px] text-body-muted">
                Bars scale to each metric&apos;s range (rightmost column): ratios 0–1 and the LLM
                judge 1–5 are higher-is-better; <span className="font-mono">count</span> values
                (n, skipped/out-of-set rows) are totals, not proportions.
              </p>
            </div>
          ) : (
            !evaluation.error_message && (
              <p className="text-xs text-body-muted">
                {terminal ? 'No scalar metrics reported.' : 'Metrics appear when the evaluation completes.'}
              </p>
            )
          )}

          {confusion && (
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-body-muted">
                Confusion matrix
              </h4>
              <ConfusionMatrix matrix={confusion.matrix} labels={confusion.labels} />
            </div>
          )}

          {evaluation.metrics_json && <JsonViewer data={evaluation.metrics_json} title="Raw metrics" />}

          <dl className="grid grid-cols-2 gap-3 font-mono text-[11px] text-body-muted sm:grid-cols-4">
            <div>
              <dt>model artifact</dt>
              <dd className="mt-0.5 text-body">{shortId(evaluation.model_artifact_id)}</dd>
            </div>
            <div>
              <dt>dataset</dt>
              <dd className="mt-0.5 text-body">{shortId(evaluation.dataset_id)}</dd>
            </div>
            {evaluation.llm_judge_model && (
              <div className="col-span-2">
                <dt>judge model</dt>
                <dd className="mt-0.5 text-body">{evaluation.llm_judge_model}</dd>
              </div>
            )}
          </dl>
        </CardBody>
      )}
    </Card>
  )
}
