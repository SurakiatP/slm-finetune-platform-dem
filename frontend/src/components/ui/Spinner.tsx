import { Loader2 } from 'lucide-react'

import { cn } from '@/lib/cn'

export function Spinner({ className, label = 'Loading' }: { className?: string; label?: string }) {
  return (
    <span role="status" aria-label={label} className="inline-flex">
      <Loader2 className={cn('h-5 w-5 animate-spin text-body-muted', className)} aria-hidden />
    </span>
  )
}

/** Full-width centered loading block for page/section loads. */
export function LoadingBlock({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-body-muted">
      <Spinner label={label} />
      <span className="text-sm">{label}…</span>
    </div>
  )
}
