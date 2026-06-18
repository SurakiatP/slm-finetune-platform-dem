import { formatNumber } from '@/lib/format'
import { metricMeta } from '@/lib/metrics'

/**
 * Horizontal bars for evaluation metrics. Each metric is scaled by its kind:
 *   - ratio  (0–1): bar = value
 *   - score5 (1–5): bar = value / 5, value shown as "x.xx / 5"
 *   - count      : no proportional bar (not a fraction) — number only
 * A muted range column states each metric's value range (0–1, 1–5, or count).
 * Hover a metric name for a one-line description.
 */
export function MetricBars({ metrics }: { metrics: Record<string, number> }) {
  const entries = Object.entries(metrics)
  if (entries.length === 0) return null

  return (
    <dl className="space-y-2">
      {entries.map(([name, value]) => {
        const meta = metricMeta(name, value)
        const fill =
          meta.kind === 'ratio' ? value : meta.kind === 'score5' ? value / 5 : null
        const valueText =
          meta.kind === 'score5' ? `${formatNumber(value, 2)} / 5` : formatNumber(value)
        return (
          <div
            key={name}
            className="grid grid-cols-[minmax(7rem,9rem)_1fr_4.5rem_3rem] items-center gap-2 sm:gap-3"
          >
            <dt className="truncate font-mono text-xs text-body-muted" title={meta.desc}>
              {name}
            </dt>
            <dd className="h-2 overflow-hidden rounded-full bg-surface-2">
              {fill !== null && (
                <div
                  className="h-full rounded-full bg-accent"
                  style={{ width: `${Math.min(100, Math.max(0, fill * 100))}%` }}
                  aria-hidden
                />
              )}
            </dd>
            <dd className="text-right font-mono text-xs text-body">{valueText}</dd>
            <dd
              className="text-right font-mono text-[10px] text-body-muted"
              title="value range"
            >
              {meta.range}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}
