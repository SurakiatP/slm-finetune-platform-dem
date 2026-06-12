import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FolderKanban, Plus } from 'lucide-react'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { createProject } from '@/api/endpoints/projects'
import type { Project, ProjectCreate, TaskType } from '@/api/types'
import { DataTable, type Column } from '@/components/data/DataTable'
import { Pagination } from '@/components/data/Pagination'
import { TaskTypeBadge } from '@/components/data/TaskTypeBadge'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field } from '@/components/ui/Field'
import { Input, Textarea } from '@/components/ui/Input'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/toast-context'
import { useProjects, useTaskTypes, queryKeys } from '@/hooks/queries'
import { cn } from '@/lib/cn'
import { formatRelativeTime } from '@/lib/format'

const PAGE_SIZE = 20

export default function ProjectListPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const offset = Number(searchParams.get('offset') ?? 0)
  const { data, isLoading } = useProjects({ limit: PAGE_SIZE, offset })
  const [createOpen, setCreateOpen] = useState(false)
  const navigate = useNavigate()

  const columns: Column<Project>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (p) => <span className="font-medium text-body">{p.name}</span>,
    },
    {
      key: 'task',
      header: 'Task type',
      render: (p) => <TaskTypeBadge taskType={p.task_type} />,
    },
    {
      key: 'description',
      header: 'Description',
      className: 'max-w-md',
      render: (p) => (
        <span className="line-clamp-1 text-body-muted">{p.description ?? '—'}</span>
      ),
    },
    {
      key: 'created',
      header: 'Created',
      render: (p) => <span className="font-mono text-xs text-body-muted">{formatRelativeTime(p.created_at)}</span>,
    },
  ]

  return (
    <>
      <PageHeader
        title="Projects"
        description="Each project owns its datasets, trainings, and evaluations for one task type."
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            New project
          </Button>
        }
      />

      <DataTable
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(p) => p.id}
        loading={isLoading}
        onRowClick={(p) => navigate(`/projects/${p.id}/datasets`)}
        emptyState={
          <EmptyState
            icon={FolderKanban}
            title="No projects yet"
            description="Create a project to start generating data and fine-tuning models."
            action={
              <Button onClick={() => setCreateOpen(true)}>
                <Plus className="h-4 w-4" aria-hidden />
                New project
              </Button>
            }
          />
        }
      />

      {data && (
        <Pagination
          total={data.total}
          limit={data.limit}
          offset={data.offset}
          onOffsetChange={(o) => setSearchParams(o > 0 ? { offset: String(o) } : {})}
        />
      )}

      <CreateProjectModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </>
  )
}

function CreateProjectModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [taskType, setTaskType] = useState<TaskType | null>(null)
  const { data: taskTypes } = useTaskTypes()
  const queryClient = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()

  const mutation = useMutation({
    mutationFn: (body: ProjectCreate) => createProject(body),
    onSuccess: (project) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects })
      toast.success(`Project "${project.name}" created`)
      onClose()
      navigate(`/projects/${project.id}/datasets`)
    },
    onError: (err) => toast.error(err.message),
  })

  const canSubmit = name.trim().length > 0 && taskType !== null

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New project"
      description="Task type is permanent — create a new project to switch tasks."
      widthClass="max-w-xl"
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (!canSubmit || !taskType) return
          mutation.mutate({ name: name.trim(), description: description.trim() || null, task_type: taskType })
        }}
      >
        <Field label="Name" required>
          {(id) => (
            <Input
              id={id}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              placeholder="support-ticket-router"
              required
            />
          )}
        </Field>

        <Field label="Description">
          {(id) => (
            <Textarea
              id={id}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={2000}
              placeholder="What should the fine-tuned model do?"
            />
          )}
        </Field>

        <fieldset>
          <legend className="mb-1.5 block text-xs font-medium text-body-muted">
            Task type <span className="text-danger" aria-hidden>*</span>
          </legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {(taskTypes ?? []).map((t) => (
              <label
                key={t.task_type}
                className={cn(
                  'cursor-pointer rounded-lg border p-3 transition-colors',
                  taskType === t.task_type
                    ? 'border-accent bg-accent-muted'
                    : 'border-line/60 bg-bg hover:border-body-muted',
                )}
              >
                <input
                  type="radio"
                  name="task_type"
                  value={t.task_type}
                  checked={taskType === t.task_type}
                  onChange={() => setTaskType(t.task_type)}
                  className="sr-only"
                />
                <TaskTypeBadge taskType={t.task_type} />
                <p className="mt-2 text-xs leading-snug text-body-muted">{t.description}</p>
              </label>
            ))}
            {!taskTypes && <p className="text-xs text-body-muted">Loading task types…</p>}
          </div>
        </fieldset>

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={!canSubmit} loading={mutation.isPending}>
            Create project
          </Button>
        </div>
      </form>
    </Modal>
  )
}
