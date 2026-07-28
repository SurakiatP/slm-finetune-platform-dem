import { useQueryClient } from '@tanstack/react-query'

import type { Dataset } from '@/api/types'
import { JobProgressPanel } from '@/components/jobs/JobProgressPanel'
import { queryKeys } from '@/hooks/queries'
import { useJobProgress } from '@/hooks/useJobProgress'

/**
 * Live SDG generation progress for the dataset detail page, driven by the
 * job-progress WebSocket. The WS channel key is the Celery task id, which is
 * persisted on the dataset row as `generation_metadata.celery_task_id`
 * (== the `job_id` the worker publishes on), so it's recoverable from just
 * the datasetId — no extra backend call needed.
 *
 * Only mount this while the dataset is non-terminal: the WS has no replay, so
 * connecting after generation already finished would show nothing. The parent
 * gates on that; when the socket reports terminal we invalidate the dataset +
 * preview queries so the rows/preview appear immediately (faster than the
 * parent's REST poll).
 */
export function DatasetLiveProgress({ dataset, jobId }: { dataset: Dataset; jobId: string }) {
  const queryClient = useQueryClient()
  const progress = useJobProgress(jobId, {
    onTerminal: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.dataset(dataset.id) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasetPreview(dataset.id) })
    },
  })

  return (
    <JobProgressPanel
      title="Generation progress"
      progress={progress}
      status={dataset.status}
      errorMessage={dataset.error_message}
    />
  )
}
