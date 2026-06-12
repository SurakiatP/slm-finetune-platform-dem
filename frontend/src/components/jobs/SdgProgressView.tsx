import { Check } from 'lucide-react'

import type { SDGPhase, SDGProgressMsg } from '@/api/types'
import { ProgressBar } from '@/components/jobs/ProgressBar'
import { cn } from '@/lib/cn'

const PHASES: SDGPhase[] = ['generating', 'validating', 'deduplicating', 'persisting']

export function SdgProgressView({ progress }: { progress: SDGProgressMsg }) {
  const phaseIndex = PHASES.indexOf(progress.phase)

  return (
    <div className="space-y-4">
      <ol className="flex flex-wrap items-center gap-2" aria-label="SDG phases">
        {PHASES.map((phase, i) => {
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
              {i < PHASES.length - 1 && <span className="text-body-muted/40" aria-hidden>→</span>}
            </li>
          )
        })}
      </ol>

      <ProgressBar
        value={progress.samples_generated}
        max={progress.samples_target}
        label="Samples generated"
      />

      <dl className="grid grid-cols-3 gap-3 font-mono text-xs">
        <div className="rounded-md bg-bg p-2.5">
          <dt className="text-body-muted">valid</dt>
          <dd className="mt-0.5 text-base text-accent">{progress.samples_valid.toLocaleString()}</dd>
        </div>
        <div className="rounded-md bg-bg p-2.5">
          <dt className="text-body-muted">rejected</dt>
          <dd className="mt-0.5 text-base text-warn">{progress.samples_rejected.toLocaleString()}</dd>
        </div>
        <div className="rounded-md bg-bg p-2.5">
          <dt className="text-body-muted">duplicates</dt>
          <dd className="mt-0.5 text-base text-body-muted">{progress.duplicates_removed.toLocaleString()}</dd>
        </div>
      </dl>
    </div>
  )
}
