import { formatNumber } from '@/lib/format'

/**
 * Horizontal bars for 0–1 scalar metrics (accuracy, F1, ROUGE…).
 * Values are always shown as text — the bar is reinforcement, not the only signal.
 */
export function MetricBars({ metrics }: { metrics: Record<string, number> }) {
  const entries = Object.entries(metrics)
  if (entries.length === 0) return null
  const max = Math.max(1, ...entries.map(([, v]) => v))

  return (
    <dl className="space-y-2">
      {entries.map(([name, value]) => (
        <div key={name} className="grid grid-cols-[10rem_1fr_4.5rem] items-center gap-3">
          <dt className="truncate font-mono text-xs text-body-muted" title={name}>
            {name}
          </dt>
          <dd className="h-2 overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${Math.min(100, (value / max) * 100)}%` }}
              aria-hidden
            />
          </dd>
          <dd className="text-right font-mono text-xs text-body">{formatNumber(value)}</dd>
        </div>
      ))}
    </dl>
  )
}
