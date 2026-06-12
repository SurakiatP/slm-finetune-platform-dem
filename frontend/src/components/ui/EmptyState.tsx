import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line/60 px-6 py-14 text-center">
      <Icon className="h-8 w-8 text-body-muted/50" aria-hidden />
      <h3 className="text-sm font-semibold text-body">{title}</h3>
      {description && <p className="max-w-sm text-xs text-body-muted">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}
