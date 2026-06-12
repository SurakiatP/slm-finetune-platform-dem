import { ChevronDown } from 'lucide-react'
import { forwardRef, type SelectHTMLAttributes } from 'react'

import { cn } from '@/lib/cn'

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...rest }, ref) {
    return (
      <div className={cn('relative', className)}>
        <select
          ref={ref}
          className={cn(
            'h-10 w-full cursor-pointer appearance-none rounded-md border border-line bg-bg px-3 pr-9 text-sm text-body',
            'transition-colors duration-150 hover:border-body-muted focus:border-accent',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
          {...rest}
        >
          {children}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-body-muted"
          aria-hidden
        />
      </div>
    )
  },
)
