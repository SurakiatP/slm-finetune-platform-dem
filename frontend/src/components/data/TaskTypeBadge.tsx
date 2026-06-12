import { MessageCircleQuestion, Tags, Wrench } from 'lucide-react'

import type { TaskType } from '@/api/types'
import { Badge, type BadgeTone } from '@/components/ui/Badge'

const taskConfig: Record<TaskType, { tone: BadgeTone; icon: typeof Tags; label: string }> = {
  classification: { tone: 'sky', icon: Tags, label: 'classification' },
  tool_calling: { tone: 'violet', icon: Wrench, label: 'tool_calling' },
  qa: { tone: 'green', icon: MessageCircleQuestion, label: 'qa' },
}

export function TaskTypeBadge({ taskType }: { taskType: TaskType }) {
  const { tone, icon: Icon, label } = taskConfig[taskType]
  return (
    <Badge tone={tone} icon={<Icon className="h-3 w-3" aria-hidden />}>
      {label}
    </Badge>
  )
}
