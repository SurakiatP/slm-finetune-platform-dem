import type { HPOProgressMsg } from '@/api/types'
import { ProgressBar } from '@/components/jobs/ProgressBar'
import { TrainingProgressView } from '@/components/jobs/TrainingProgressView'
import { Badge } from '@/components/ui/Badge'
import type { LossPoint, TrialPoint } from '@/hooks/useJobProgress'
import { formatNumber } from '@/lib/format'

export function HpoProgressView({
  progress,
  trials,
  lossHistory,
}: {
  progress: HPOProgressMsg
  trials: TrialPoint[]
  lossHistory: LossPoint[]
}) {
  return (
    <div className="space-y-4">
      <ProgressBar value={progress.trial_number + 1} max={progress.trials_total} label="Trials" />

      <dl className="grid grid-cols-2 gap-3">
        <div className="rounded-md bg-bg p-2.5 font-mono text-xs">
          <dt className="text-body-muted">best value</dt>
          <dd className="mt-0.5 text-base text-accent">{formatNumber(progress.best_value)}</dd>
        </div>
        <div className="rounded-md bg-bg p-2.5 font-mono text-xs">
          <dt className="text-body-muted">best params</dt>
          <dd className="mt-0.5 break-all text-[11px] leading-relaxed text-body">
            {progress.best_params
              ? Object.entries(progress.best_params)
                  .map(([k, v]) => `${k}=${typeof v === 'number' ? formatNumber(v, 4) : String(v)}`)
                  .join('  ')
              : '—'}
          </dd>
        </div>
      </dl>

      {trials.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-line/60">
          <table className="w-full text-left font-mono text-xs">
            <caption className="sr-only">Completed HPO trials</caption>
            <thead>
              <tr className="border-b border-line/60 bg-bg text-body-muted">
                <th scope="col" className="px-3 py-1.5 font-medium">trial</th>
                <th scope="col" className="px-3 py-1.5 font-medium">value</th>
                <th scope="col" className="px-3 py-1.5 font-medium">state</th>
              </tr>
            </thead>
            <tbody>
              {[...trials].reverse().map((t) => (
                <tr key={t.trial_number} className="border-b border-line/40 last:border-0">
                  <td className="px-3 py-1.5 text-body-muted">#{t.trial_number}</td>
                  <td className="px-3 py-1.5">{formatNumber(t.value)}</td>
                  <td className="px-3 py-1.5">
                    {t.pruned ? <Badge tone="amber">pruned</Badge> : <Badge tone="green">complete</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {progress.inner_progress && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-body-muted">
            Current trial #{progress.trial_number}
          </h4>
          <TrainingProgressView progress={progress.inner_progress} lossHistory={lossHistory} />
        </div>
      )}
    </div>
  )
}
