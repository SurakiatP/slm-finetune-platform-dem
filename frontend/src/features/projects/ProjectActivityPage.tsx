import { History } from 'lucide-react'
import { useState } from 'react'

import type { AuditEvent } from '@/api/types'
import { DataTable, type Column } from '@/components/data/DataTable'
import { JsonViewer } from '@/components/data/JsonViewer'
import { Pagination } from '@/components/data/Pagination'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { useProjectContext } from '@/features/shared/useProjectContext'
import { useProjectActivity } from '@/hooks/queries'
import { formatDateTime, formatRelativeTime, shortId } from '@/lib/format'

const PAGE_SIZE = 25

export default function ProjectActivityPage() {
  const { project } = useProjectContext()
  const [offset, setOffset] = useState(0)
  const { data, isLoading } = useProjectActivity(project.id, { limit: PAGE_SIZE, offset })

  const columns: Column<AuditEvent>[] = [
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
      key: 'action',
      header: 'Action',
      render: (e) => <span className="font-mono text-xs text-body">{e.action}</span>,
    },
    {
      key: 'resource',
      header: 'Resource',
      render: (e) => (
        <span className="text-xs text-body-muted">
          {e.resource_type} <span className="font-mono">{shortId(e.resource_id)}</span>
        </span>
      ),
    },
    {
      key: 'outcome',
      header: 'Outcome',
      render: (e) => <Badge tone={e.outcome === 'success' ? 'green' : 'red'}>{e.outcome}</Badge>,
    },
    {
      key: 'actor',
      header: 'Actor',
      render: (e) => (
        <span className="font-mono text-xs text-body-muted">{e.actor_id ? shortId(e.actor_id) : 'anonymous'}</span>
      ),
    },
    {
      key: 'metadata',
      header: 'Details',
      render: (e) =>
        e.metadata ? <JsonViewer data={e.metadata} title="Metadata" /> : <span className="text-xs text-body-muted">—</span>,
    },
  ]

  return (
    <>
      <div className="mb-4">
        <h2 className="sr-only">Activity</h2>
        <p className="text-xs text-body-muted">Audit log of actions taken on this project.</p>
      </div>

      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(e) => e.id}
        loading={isLoading}
        emptyState={
          <EmptyState
            icon={History}
            title="No activity yet"
            description="Actions taken on this project — datasets, trainings, exports, evaluations — will appear here."
          />
        }
      />

      {data && (
        <Pagination total={data.total} limit={data.limit} offset={data.offset} onOffsetChange={setOffset} />
      )}
    </>
  )
}
