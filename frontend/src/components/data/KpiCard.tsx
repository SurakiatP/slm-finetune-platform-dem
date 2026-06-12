import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

import { Card } from '@/components/ui/Card'

interface KpiCardProps {
  label: string
  value: ReactNode
  icon: LucideIcon
  sub?: ReactNode
}

export function KpiCard({ label, value, icon: Icon, sub }: KpiCardProps) {
  return (
    <Card className="flex items-start justify-between p-4">
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-body-muted">{label}</p>
        <p className="mt-1 font-mono text-2xl font-semibold text-body">{value}</p>
        {sub && <p className="mt-0.5 text-xs text-body-muted">{sub}</p>}
      </div>
      <Icon className="h-5 w-5 text-body-muted/60" aria-hidden />
    </Card>
  )
}
