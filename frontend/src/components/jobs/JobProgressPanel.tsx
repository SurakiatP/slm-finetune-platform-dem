import { AlertTriangle, CheckCircle2, Radio } from 'lucide-react'
import type { ReactNode } from 'react'

import type { JobStatus } from '@/api/types'
import { StatusBadge } from '@/components/data/StatusBadge'
import { HpoProgressView } from '@/components/jobs/HpoProgressView'
import { SdgProgressView } from '@/components/jobs/SdgProgressView'
import { TrainingProgressView } from '@/components/jobs/TrainingProgressView'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import type { JobProgressState } from '@/hooks/useJobProgress'

interface JobProgressPanelProps {
  /** Live WS state from useJobProgress. */
  progress: JobProgressState
  /** Authoritative status from the REST resource. */
  status: JobStatus
  /** REST error_message fallback for failures the socket missed. */
  errorMessage?: string | null
  title?: string
  /** Rendered inside the success block (e.g. "View model" link). */
  successAction?: ReactNode
}

export function JobProgressPanel({
  progress,
  status,
  errorMessage,
  title = 'Job progress',
  successAction,
}: JobProgressPanelProps) {
  const failedError = progress.failed?.error ?? (status === 'failed' ? errorMessage : null)
  const isCompleted = status === 'completed' || progress.completed !== null

  return (
    <Card>
      <CardHeader
        title={title}
        actions={
          <span className="flex items-center gap-2">
            {status === 'running' && (
              <span
                className="flex items-center gap-1 font-mono text-[11px] text-body-muted"
                title={progress.socketOpen ? 'Live WebSocket connected' : 'Polling (socket offline)'}
              >
                <Radio
                  className={progress.socketOpen ? 'h-3 w-3 text-accent' : 'h-3 w-3 text-warn'}
                  aria-hidden
                />
                {progress.socketOpen ? 'live' : 'polling'}
              </span>
            )}
            <StatusBadge status={status} />
          </span>
        }
      />
      <CardBody className="space-y-4">
        {failedError && (
          <div role="alert" className="rounded-md border border-danger/40 bg-danger-muted p-3 text-sm">
            <p className="flex items-center gap-2 font-semibold text-danger">
              <AlertTriangle className="h-4 w-4" aria-hidden />
              {progress.failed?.error_type ?? 'Job failed'}
            </p>
            <p className="mt-1 break-words text-body">{failedError}</p>
            {progress.failed?.traceback && (
              <pre className="scrollbar-thin mt-2 max-h-48 overflow-auto rounded bg-bg p-2 font-mono text-xs text-body-muted">
                {progress.failed.traceback}
              </pre>
            )}
          </div>
        )}

        {isCompleted && (
          <div className="flex items-center justify-between gap-3 rounded-md border border-accent/30 bg-accent-muted p-3 text-sm">
            <p className="flex items-center gap-2 font-medium text-accent">
              <CheckCircle2 className="h-4 w-4" aria-hidden />
              Job completed
            </p>
            {successAction}
          </div>
        )}

        {progress.hpo ? (
          <HpoProgressView progress={progress.hpo} trials={progress.trials} lossHistory={progress.lossHistory} />
        ) : progress.training ? (
          <TrainingProgressView progress={progress.training} lossHistory={progress.lossHistory} />
        ) : progress.sdg ? (
          <SdgProgressView progress={progress.sdg} />
        ) : (
          !failedError &&
          !isCompleted && (
            <p className="py-4 text-center text-xs text-body-muted">
              {status === 'pending' ? 'Waiting for a worker to pick up the job…' : 'Waiting for progress updates…'}
            </p>
          )
        )}
      </CardBody>
    </Card>
  )
}
