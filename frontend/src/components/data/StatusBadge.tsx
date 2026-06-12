import { Ban, CheckCircle2, Clock, Loader2, XCircle } from 'lucide-react'

import type { JobStatus } from '@/api/types'
import { Badge, type BadgeTone } from '@/components/ui/Badge'

const statusConfig: Record<JobStatus, { tone: BadgeTone; icon: typeof Clock; spin?: boolean }> = {
  pending: { tone: 'neutral', icon: Clock },
  running: { tone: 'green', icon: Loader2, spin: true },
  completed: { tone: 'green', icon: CheckCircle2 },
  failed: { tone: 'red', icon: XCircle },
  cancelled: { tone: 'neutral', icon: Ban },
}

export function StatusBadge({ status }: { status: JobStatus }) {
  const { tone, icon: Icon, spin } = statusConfig[status]
  return (
    <Badge tone={tone} icon={<Icon className={spin ? 'h-3 w-3 animate-spin' : 'h-3 w-3'} aria-hidden />}>
      {status}
    </Badge>
  )
}
