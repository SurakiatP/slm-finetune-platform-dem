import type { TrainingProgressMsg } from '@/api/types'
import { LossCurveChart } from '@/components/jobs/LossCurveChart'
import { ProgressBar } from '@/components/jobs/ProgressBar'
import type { LossPoint } from '@/hooks/useJobProgress'
import { formatNumber } from '@/lib/format'

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-bg p-2.5 font-mono text-xs">
      <dt className="text-body-muted">{label}</dt>
      <dd className="mt-0.5 text-base text-body">{value}</dd>
    </div>
  )
}

export function TrainingProgressView({
  progress,
  lossHistory,
}: {
  progress: TrainingProgressMsg
  lossHistory: LossPoint[]
}) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <ProgressBar value={progress.step} max={progress.steps_total} label="Steps" />
        <ProgressBar value={progress.epoch} max={progress.epochs_total} label="Epochs" />
      </div>

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="train loss" value={formatNumber(progress.train_loss)} />
        <StatTile label="eval loss" value={formatNumber(progress.eval_loss)} />
        <StatTile
          label="lr"
          value={progress.learning_rate !== null ? progress.learning_rate.toExponential(2) : '—'}
        />
        <StatTile
          label="GPU mem"
          value={progress.gpu_memory_mb !== null ? `${(progress.gpu_memory_mb / 1024).toFixed(1)} GB` : '—'}
        />
      </dl>

      <LossCurveChart data={lossHistory} />
    </div>
  )
}
