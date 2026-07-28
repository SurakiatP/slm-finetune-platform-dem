import { useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { Link } from 'react-router-dom'

import { JobProgressPanel } from '@/components/jobs/JobProgressPanel'
import type { SdgJobRef } from '@/features/datasets/GenerateDatasetModal'
import { useProjectContext } from '@/features/shared/useProjectContext'
import { useDataset, queryKeys } from '@/hooks/queries'
import { useJobProgress, jobRefetchInterval } from '@/hooks/useJobProgress'

/** Inline live progress for the SDG job just submitted from this page. */
export function ActiveSdgJobCard({ job, onDismiss }: { job: SdgJobRef; onDismiss: () => void }) {
  const { project } = useProjectContext()
  const queryClient = useQueryClient()

  const progress = useJobProgress(job.jobId, {
    onTerminal: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets(project.id) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.dataset(job.datasetId) })
    },
  })

  const terminal = progress.completed !== null || progress.failed !== null
  const { data: dataset } = useDataset(job.datasetId, {
    refetchInterval: jobRefetchInterval(terminal, progress.socketOpen),
  })
  // dataset.status is the authoritative source; fall back to the WS's own
  // running/pending signal only for the brief window before the first
  // dataset fetch resolves.
  const status = dataset?.status ?? (progress.sdg ? 'running' : 'pending')

  return (
    <div className="relative">
      <JobProgressPanel
        title={`Generating "${dataset?.name ?? 'dataset'}"`}
        progress={progress}
        status={status}
        successAction={
          <Link
            to={job.datasetId}
            className="inline-flex h-8 items-center rounded-md border border-line bg-surface px-3 text-xs text-body transition-colors hover:bg-surface-2"
          >
            View dataset
          </Link>
        }
      />
      <button
        type="button"
        aria-label="Dismiss progress panel"
        onClick={onDismiss}
        className="absolute right-2 top-2 cursor-pointer rounded p-1 text-body-muted transition-colors hover:bg-surface-2 hover:text-body"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  )
}
