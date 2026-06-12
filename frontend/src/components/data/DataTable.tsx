import type { ReactNode } from 'react'

import { cn } from '@/lib/cn'

export interface Column<T> {
  key: string
  header: ReactNode
  render: (row: T) => ReactNode
  className?: string
}

interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  onRowClick?: (row: T) => void
  loading?: boolean
  emptyState?: ReactNode
}

export function DataTable<T>({ columns, rows, rowKey, onRowClick, loading, emptyState }: DataTableProps<T>) {
  if (!loading && rows.length === 0 && emptyState) {
    return <>{emptyState}</>
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-line/60">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line/60 bg-surface">
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={cn('px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-body-muted', col.className)}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: 4 }, (_, i) => (
                <tr key={i} className="border-b border-line/40 last:border-0">
                  {columns.map((col) => (
                    <td key={col.key} className="px-4 py-3">
                      <div className="h-4 w-3/4 animate-pulse rounded bg-surface-2" />
                    </td>
                  ))}
                </tr>
              ))
            : rows.map((row) => (
                <tr
                  key={rowKey(row)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cn(
                    'border-b border-line/40 transition-colors last:border-0',
                    onRowClick && 'cursor-pointer hover:bg-surface-2/60',
                  )}
                >
                  {columns.map((col) => (
                    <td key={col.key} className={cn('px-4 py-3', col.className)}>
                      {col.render(row)}
                    </td>
                  ))}
                </tr>
              ))}
        </tbody>
      </table>
    </div>
  )
}
