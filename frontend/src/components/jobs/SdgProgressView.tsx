import { Check } from 'lucide-react'

import type { SDGPhase, SDGProgressMsg } from '@/api/types'
import { ProgressBar } from '@/components/jobs/ProgressBar'
import { cn } from '@/lib/cn'

// Phase 9 orchestrator sequence; legacy Phase 4 emitters use the second list.
const PHASE9: SDGPhase[] = ['format_detection', 'meta_prompting', 'generating', 'judging', 'dedup', 'persisting']
const LEGACY: SDGPhase[] = ['generating', 'validating', 'deduplicating', 'persisting']

function phaseList(current: SDGPhase): SDGPhase[] {
  return LEGACY.includes(current) && !PHASE9.includes(current) ? LEGACY : PHASE9
}

export function SdgProgressView({ progress }: { progress: SDGProgressMsg }) {
  const phases = phaseList(progress.phase)
  const phaseIndex = phases.indexOf(progress.phase)

  const counters: { label: string; value: number; tone: string }[] = [
    { label: 'valid', value: progress.samples_valid, tone: 'text-accent' },
    { label: 'rejected', value: progress.samples_rejected, tone: 'text-warn' },
    { label: 'duplicates', value: progress.duplicates_removed, tone: 'text-body-muted' },
  ]
  if (progress.judge_rejected !== null && progress.judge_rejected !== undefined) {
    counters.push({ label: 'judge rejected', value: progress.judge_rejected, tone: 'text-warn' })
  }
  if (progress.dedup_rejected !== null && progress.dedup_rejected !== undefined) {
    counters.push({ label: 'near-dup removed', value: progress.dedup_rejected, tone: 'text-body-muted' })
  }

  return (
    <div className="space-y-4">
      <ol className="flex flex-wrap items-center gap-2" aria-label="SDG phases">
        {phases.map((phase, i) => {
          const done = i < phaseIndex
          const active = i === phaseIndex
          return (
            <li key={phase} className="flex items-center gap-2">
              <span
                className={cn(
                  'flex items-center gap-1 rounded-full border px-2.5 py-0.5 font-mono text-[11px]',
                  done && 'border-accent/30 bg-accent-muted text-accent',
                  active && 'border-accent bg-accent-muted font-semibold text-accent',
                  !done && !active && 'border-line/60 text-body-muted',
                )}
                aria-current={active ? 'step' : undefined}
              >
                {done && <Check className="h-3 w-3" aria-hidden />}
                {phase}
              </span>
              {i < phases.length - 1 && <span className="text-body-muted/40" aria-hidden>→</span>}
            </li>
          )
        })}
      </ol>

      <ProgressBar
        value={progress.samples_generated}
        max={progress.samples_target}
        label={
          progress.current_loop !== null && progress.current_loop !== undefined
            ? `Samples generated (loop ${progress.current_loop + 1})`
            : 'Samples generated'
        }
      />

      <dl className="grid grid-cols-3 gap-3 font-mono text-xs sm:grid-cols-5">
        {counters.map((c) => (
          <div key={c.label} className="rounded-md bg-bg p-2.5">
            <dt className="text-body-muted">{c.label}</dt>
            <dd className={cn('mt-0.5 text-base', c.tone)}>{c.value.toLocaleString()}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
