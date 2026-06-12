import { ChevronDown, ChevronRight, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { isTerminalStatus } from '@/api/types'
import { ConfusionMatrix } from '@/components/charts/ConfusionMatrix'
import { MetricBars } from '@/components/charts/MetricBars'
import { JsonViewer } from '@/components/data/JsonViewer'
import { StatusBadge } from '@/components/data/StatusBadge'
import { Card, CardBody } from '@/components/ui/Card'
import { useEvaluation } from '@/hooks/queries'
import { formatDuration, formatNumber, formatRelativeTime, shortId } from '@/lib/format'
import { extractConfusionMatrix, scalarMetrics } from '@/lib/metrics'

interface EvaluationRowProps {
  evaluationId: string
  selected: boolean
  onToggleSelect: () => void
  onForget: () => void
}

/** One registry entry, hydrated from GET /evaluations/{id}; polls while running. */
export function EvaluationRow({ evaluationId, selected, onToggleSelect, onForget }: EvaluationRowProps) {
  const [expanded, setExpanded] = useState(false)
  // No WS subscription here — evaluation jobs report no incremental progress,
  // so a simple poll until terminal is enough.
  const { data: evaluation, isError } = useEvaluation(evaluationId, {
    refetchInterval: (query) =>
      query.state.status === 'error' ||
      (query.state.data && isTerminalStatus(query.state.data.status))
        ? false
        : 5_000,
  })

  if (isError) {
    return (
      <Card className="flex items-center justify-between p-3 text-xs text-body-muted">
        <span>
          Evaluation <span className="font-mono">{shortId(evaluationId)}</span> no longer exists on the server.
        </span>
        <button
          type="button"
          onClick={onForget}
          className="cursor-pointer rounded p-1.5 text-body-muted transition-colors hover:bg-danger-muted hover:text-danger"
          aria-label="Forget evaluation"
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </button>
      </Card>
    )
  }

  if (!evaluation) {
    return <Card className="h-14 animate-pulse" />
  }

  const terminal = isTerminalStatus(evaluation.status)
  const metrics = evaluation.metrics_json ? scalarMetrics(evaluation.metrics_json) : {}
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
              judge {formatNumber(evaluation.llm_judge_score, 2)}
            </span>
          )}
          <span className="ml-auto hidden font-mono text-[11px] text-body-muted sm:inline">
            {terminal
              ? formatDuration(evaluation.started_at, evaluation.ended_at)
              : formatRelativeTime(evaluation.created_at)}
          </span>
        </button>
        <button
          type="button"
          onClick={onForget}
          className="cursor-pointer rounded p-1.5 text-body-muted transition-colors hover:bg-danger-muted hover:text-danger"
          aria-label={`Forget evaluation ${shortId(evaluationId)}`}
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </button>
      </div>

      {expanded && (
        <CardBody className="space-y-4 border-t border-line/60">
          {evaluation.error_message && (
            <p role="alert" className="rounded-md border border-danger/40 bg-danger-muted p-3 text-xs text-body">
              {evaluation.error_message}
            </p>
          )}

          {Object.keys(metrics).length > 0 ? (
            <MetricBars metrics={metrics} />
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
