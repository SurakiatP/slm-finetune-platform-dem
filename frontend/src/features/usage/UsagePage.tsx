import { ArrowUpDown, Coins, MessageSquareText } from 'lucide-react'

import type { UsageRollupItem } from '@/api/types'
import { DataTable, type Column } from '@/components/data/DataTable'
import { KpiCard } from '@/components/data/KpiCard'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingBlock } from '@/components/ui/Spinner'
import { useUsageSummary } from '@/hooks/queries'
import { formatNumber, formatUsd } from '@/lib/format'

/** Month + year for a UTC calendar month, from an ISO period boundary. */
function utcMonthLabel(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' })
}

/** Fallback shown before the summary has loaded: today's UTC calendar month. */
function currentUtcMonthLabel(): string {
  return new Date().toLocaleString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' })
}

const columns: Column<UsageRollupItem>[] = [
  {
    key: 'model',
    header: 'Model',
    render: (r) => <span className="font-mono text-xs text-body">{r.model}</span>,
  },
  {
    key: 'stage',
    header: 'Stage',
    render: (r) => <Badge tone="neutral">{r.stage}</Badge>,
  },
  {
    key: 'prompt_tokens',
    header: 'Prompt tokens',
    render: (r) => <span className="font-mono text-xs text-body-muted">{formatNumber(r.prompt_tokens)}</span>,
  },
  {
    key: 'completion_tokens',
    header: 'Completion tokens',
    render: (r) => <span className="font-mono text-xs text-body-muted">{formatNumber(r.completion_tokens)}</span>,
  },
  {
    key: 'cost_usd',
    header: 'Cost',
    render: (r) => <span className="font-mono text-xs text-body">{formatUsd(r.cost_usd)}</span>,
  },
]

export default function UsagePage() {
  const { data, isLoading } = useUsageSummary()

  const periodLabel = data ? utcMonthLabel(data.period_start) : currentUtcMonthLabel()

  return (
    <>
      <PageHeader
        title="Usage & cost"
        description={`OpenRouter token usage and cost for the current UTC calendar month (${periodLabel}).`}
      />

      {isLoading || !data ? (
        <LoadingBlock label="Loading usage" />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <KpiCard label="Prompt tokens" value={formatNumber(data.prompt_tokens)} icon={ArrowUpDown} />
            <KpiCard label="Completion tokens" value={formatNumber(data.completion_tokens)} icon={MessageSquareText} />
            <KpiCard
              label="Cost"
              value={formatUsd(data.cost_usd)}
              icon={Coins}
              sub={data.has_unpriced_usage ? <Badge tone="amber">unpriced rows</Badge> : undefined}
            />
          </div>

          {data.has_unpriced_usage && (
            <p className="mt-2 text-xs text-warn">
              Some rows had no configured price for their model, so the cost total above is a floor, not the true
              total.
            </p>
          )}

          <div className="mt-6">
            <DataTable
              columns={columns}
              rows={data.items}
              rowKey={(r) => `${r.model}::${r.stage}`}
              emptyState={
                <EmptyState
                  icon={Coins}
                  title="No usage yet"
                  description="Rows appear here once SDG or evaluation calls hit OpenRouter."
                />
              }
            />
          </div>

          <p className="mt-4 text-xs text-body-muted">
            With auth disabled, this page shows the anonymous usage bucket; with auth enabled, it shows the
            signed-in caller&apos;s own usage.
          </p>
        </>
      )}
    </>
  )
}
