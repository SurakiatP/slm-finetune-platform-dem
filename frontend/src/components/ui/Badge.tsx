import type { HTMLAttributes, ReactNode } from 'react'

import { cn } from '@/lib/cn'

export type BadgeTone = 'neutral' | 'green' | 'red' | 'amber' | 'sky' | 'violet'

const toneClasses: Record<BadgeTone, string> = {
  neutral: 'bg-surface-2 text-body-muted border-line/60',
  green: 'bg-accent-muted text-accent border-accent/30',
  red: 'bg-danger-muted text-danger border-danger/30',
  amber: 'bg-warn-muted text-warn border-warn/30',
  sky: 'bg-info-muted text-info border-info/30',
  violet: 'bg-violet-muted text-violet border-violet/30',
}

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
  icon?: ReactNode
}

export function Badge({ tone = 'neutral', icon, className, children, ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[11px] font-medium',
        toneClasses[tone],
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
    </span>
  )
}
