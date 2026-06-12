import type { DatasetSource } from '@/api/types'
import { Badge, type BadgeTone } from '@/components/ui/Badge'

const sourceTones: Record<DatasetSource, BadgeTone> = {
  seed: 'amber',
  sdg: 'sky',
  merged: 'violet',
}

export function SourceBadge({ source }: { source: DatasetSource }) {
  return <Badge tone={sourceTones[source]}>{source}</Badge>
}
