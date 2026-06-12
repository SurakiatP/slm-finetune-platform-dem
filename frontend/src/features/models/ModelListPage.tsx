import { Boxes } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import type { ModelArtifact, Project } from '@/api/types'
import { DataTable, type Column } from '@/components/data/DataTable'
import { Pagination } from '@/components/data/Pagination'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { Select } from '@/components/ui/Select'
import { useModels, useProjects } from '@/hooks/queries'
import { formatRelativeTime } from '@/lib/format'

const PAGE_SIZE = 20

export function FormatBadges({ model }: { model: ModelArtifact }) {
  return (
    <span className="flex flex-wrap gap-1">
      {model.lora_adapter_uri && <Badge tone="neutral">lora</Badge>}
      {model.gguf_uri && <Badge tone="green">gguf</Badge>}
      {model.safetensors_uri && <Badge tone="sky">safetensors</Badge>}
    </span>
  )
}

export default function ModelListPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const projectId = searchParams.get('project_id') ?? undefined
  const offset = Number(searchParams.get('offset') ?? 0)
  const { data, isLoading } = useModels(projectId, { limit: PAGE_SIZE, offset })
  const { data: projects } = useProjects({ limit: 200 })
  const navigate = useNavigate()

  const columns: Column<ModelArtifact>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (m) => <span className="font-medium text-body">{m.name}</span>,
    },
    {
      key: 'base',
      header: 'Base model',
      className: 'max-w-56',
      render: (m) => (
        <span className="block truncate font-mono text-xs text-body-muted" title={m.base_model}>
          {m.base_model.replace(/^unsloth\//, '')}
        </span>
      ),
    },
    { key: 'formats', header: 'Formats', render: (m) => <FormatBadges model={m} /> },
    {
      key: 'size',
      header: 'Size',
      render: (m) => (
        <span className="font-mono text-xs text-body-muted">
          {m.size_mb !== null ? `${m.size_mb.toFixed(0)} MB` : '—'}
        </span>
      ),
    },
    {
      key: 'ollama',
      header: 'Ollama tag',
      render: (m) =>
        m.ollama_model_tag ? (
          <span className="font-mono text-xs text-accent">{m.ollama_model_tag}</span>
        ) : (
          <span className="text-xs text-body-muted">not served</span>
        ),
    },
    {
      key: 'created',
      header: 'Created',
      render: (m) => <span className="font-mono text-xs text-body-muted">{formatRelativeTime(m.created_at)}</span>,
    },
  ]

  return (
    <>
      <PageHeader
        title="Models"
        description="Fine-tuned artifacts from completed trainings. Export to GGUF to serve via Ollama."
        actions={
          <label className="flex items-center gap-2 text-xs text-body-muted">
            Project
            <Select
              value={projectId ?? ''}
              onChange={(e) => setSearchParams(e.target.value ? { project_id: e.target.value } : {})}
              className="w-56"
              aria-label="Filter by project"
            >
              <option value="">All projects</option>
              {(projects?.items ?? []).map((p: Project) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </label>
        }
      />

      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(m) => m.id}
        loading={isLoading}
        onRowClick={(m) => navigate(`/models/${m.id}`)}
        emptyState={
          <EmptyState
            icon={Boxes}
            title="No model artifacts yet"
            description="Complete a training run and its LoRA adapter will appear here."
          />
        }
      />

      {data && (
        <Pagination
          total={data.total}
          limit={data.limit}
          offset={data.offset}
          onOffsetChange={(o) => {
            const next: Record<string, string> = {}
            if (projectId) next.project_id = projectId
            if (o > 0) next.offset = String(o)
            setSearchParams(next)
          }}
        />
      )}
    </>
  )
}
