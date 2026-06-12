import { ChevronLeft, ChevronRight } from 'lucide-react'

import { Button } from '@/components/ui/Button'

interface PaginationProps {
  total: number
  limit: number
  offset: number
  onOffsetChange: (offset: number) => void
}

export function Pagination({ total, limit, offset, onOffsetChange }: PaginationProps) {
  if (total <= limit) return null
  const page = Math.floor(offset / limit) + 1
  const pages = Math.ceil(total / limit)

  return (
    <nav aria-label="Pagination" className="flex items-center justify-between pt-3">
      <span className="font-mono text-xs text-body-muted">
        {offset + 1}–{Math.min(offset + limit, total)} of {total}
      </span>
      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          disabled={offset === 0}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
          aria-label="Previous page"
        >
          <ChevronLeft className="h-4 w-4" aria-hidden />
        </Button>
        <span className="font-mono text-xs text-body-muted">
          {page}/{pages}
        </span>
        <Button
          variant="secondary"
          size="sm"
          disabled={offset + limit >= total}
          onClick={() => onOffsetChange(offset + limit)}
          aria-label="Next page"
        >
          <ChevronRight className="h-4 w-4" aria-hidden />
        </Button>
      </div>
    </nav>
  )
}
