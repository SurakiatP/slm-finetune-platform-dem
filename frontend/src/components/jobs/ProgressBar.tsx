import { cn } from '@/lib/cn'

interface ProgressBarProps {
  value: number
  max: number
  label: string
  className?: string
}

export function ProgressBar({ value, max, label, className }: ProgressBarProps) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0
  return (
    <div className={className}>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-body-muted">{label}</span>
        <span className="font-mono text-body-muted">
          {value.toLocaleString()}/{max.toLocaleString()}
        </span>
      </div>
      <div
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
        className="h-1.5 overflow-hidden rounded-full bg-surface-2"
      >
        <div
          className={cn('h-full rounded-full bg-accent transition-[width] duration-300')}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}
