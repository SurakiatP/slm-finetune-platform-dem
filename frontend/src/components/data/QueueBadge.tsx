import { Loader2, ListOrdered } from 'lucide-react'

import { Badge } from '@/components/ui/Badge'
import type { QueueState } from '@/api/types'

/** Project-level GPU queue standing, from the `queue_state`/`queue_position`
 *  trio the backend adds to detail GET responses (train/export/eval share one
 *  GPU, worker concurrency 1). The fields are only populated while the
 *  project has in-flight GPU work and only on detail endpoints — list rows
 *  and terminal rows carry nulls, and this component renders nothing for
 *  them, so it is safe to drop in unconditionally next to a StatusBadge. */
export function QueueBadge({
  queueState,
  queuePosition,
}: {
  queueState: QueueState | null | undefined
  queuePosition: number | null | undefined
}) {
  if (queueState === 'processing') {
    return (
      <Badge tone="sky">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        GPU active
      </Badge>
    )
  }
  if (queueState === 'queued' && queuePosition != null) {
    return (
      <Badge tone="amber">
        <ListOrdered className="h-3 w-3" aria-hidden />
        Queue #{queuePosition}
      </Badge>
    )
  }
  return null
}
