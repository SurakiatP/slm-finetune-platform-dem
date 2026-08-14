import { Coins } from 'lucide-react'
import { useState } from 'react'

import type { UsageEvent } from '@/api/types'
import { DataTable, type Column } from '@/components/data/DataTable'
import { Pagination } from '@/components/data/Pagination'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { useProjectContext } from '@/features/shared/useProjectContext'
import { useProjectUsage } from '@/hooks/queries'
import { formatDateTime, formatNumber, formatRelativeTime, formatUsd, shortId } from '@/lib/format'

const PAGE_SIZE = 25

export default function ProjectUsagePage() {
  const { project } = useProjectContext()
  const [offset, setOffset] = useState(0)
  const { data, isLoading } = useProjectUsage(project.id, { limit: PAGE_SIZE, offset })

  const columns: Column<UsageEvent>[] = [
    {
      key: 'when',
      header: 'When',
      render: (e) => (
        <span className="font-mono text-xs text-body-muted" title={formatDateTime(e.created_at)}>
          {formatRelativeTime(e.created_at)}
        </span>
      ),
    },
    {
      key: 'stage',
      header: 'Stage',
      render: (e) => <span className="text-body">{e.stage}</span>,
    },
    {
      key: 'model',
      header: 'Model',
      render: (e) => (
        <span className="block max-w-[16rem] truncate font-mono text-xs text-body-muted" title={e.model}>
          {e.model}
        </span>
      ),
    },
    {
      key: 'prompt_tokens',
      header: 'Prompt tokens',
      render: (e) => <span className="font-mono text-xs">{formatNumber(e.prompt_tokens)}</span>,
    },
    {
      key: 'completion_tokens',
      header: 'Completion tokens',
      render: (e) => <span className="font-mono text-xs">{formatNumber(e.completion_tokens)}</span>,
    },
    {
      key: 'cost',
      header: 'Cost',
      render: (e) =>
        e.cost_usd === null ? (
          <span className="flex items-center gap-1.5">
            <span className="font-mono text-xs text-body-muted">—</span>
            <Badge tone="amber">unpriced</Badge>
          </span>
        ) : (
          <span className="font-mono text-xs">{formatUsd(e.cost_usd)}</span>
        ),
    },
    {
      key: 'outcome',
      header: 'Outcome',
      render: (e) => <Badge tone={e.outcome === 'success' ? 'green' : 'red'}>{e.outcome}</Badge>,
    },
    {
      key: 'job',
      header: 'Job',
      render: (e) => <span className="font-mono text-xs text-body-muted">{shortId(e.job_id)}</span>,
    },
  ]

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="sr-only">Usage</h2>
        <p className="text-xs text-body-muted">
          OpenRouter calls billed to this project's synthetic data generation and evaluation runs.
        </p>
      </div>

      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(e) => e.id}
        loading={isLoading}
        emptyState={
          <EmptyState
            icon={Coins}
            title="No usage yet"
            description="Rows appear here once SDG generation or evaluation runs make calls to OpenRouter for this project."
          />
        }
      />

      {data && (
        <Pagination total={data.total} limit={data.limit} offset={data.offset} onOffsetChange={setOffset} />
      )}
    </>
  )
}
