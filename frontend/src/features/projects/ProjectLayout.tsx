import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, NavLink, Navigate, Outlet, useNavigate, useParams } from 'react-router-dom'

import { deleteProject, updateProject } from '@/api/endpoints/projects'
import type { Project } from '@/api/types'
import { QueueBadge } from '@/components/data/QueueBadge'
import { TaskTypeBadge } from '@/components/data/TaskTypeBadge'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Field } from '@/components/ui/Field'
import { Input, Textarea } from '@/components/ui/Input'
import { Modal } from '@/components/ui/Modal'
import { LoadingBlock } from '@/components/ui/Spinner'
import { useToast } from '@/components/ui/toast-context'
import { useProject, queryKeys } from '@/hooks/queries'
import { cn } from '@/lib/cn'

const tabs = [
  { to: 'datasets', label: 'Datasets' },
  { to: 'trainings', label: 'Trainings' },
  { to: 'evaluations', label: 'Evaluations' },
  { to: 'activity', label: 'Activity' },
  { to: 'usage', label: 'Usage' },
]

export default function ProjectLayout() {
  const { projectId } = useParams<{ projectId: string }>()
  const { data: project, isLoading, isError } = useProject(projectId!)
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  if (isLoading) return <LoadingBlock label="Loading project" />
  if (isError || !project) return <Navigate to="/projects" replace />

  return (
    <>
      <PageHeader
        eyebrow={
          <Link
            to="/projects"
            className="mb-1 inline-flex items-center gap-1 text-xs text-body-muted transition-colors hover:text-body"
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
            Projects
          </Link>
        }
        title={
          <>
            {project.name}
            <TaskTypeBadge taskType={project.task_type} />
            <QueueBadge queueState={project.queue_state} queuePosition={project.queue_position} />
          </>
        }
        description={
          <>
            {project.description}
            {project.external_project_id && (
              <span className="ml-2 font-mono text-[11px] text-body-muted">
                external id: {project.external_project_id}
              </span>
            )}
          </>
        }
        actions={
          <>
            <Button variant="secondary" size="sm" onClick={() => setEditOpen(true)}>
              <Pencil className="h-3.5 w-3.5" aria-hidden />
              Edit
            </Button>
            <Button variant="danger" size="sm" onClick={() => setDeleteOpen(true)}>
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
              Delete
            </Button>
          </>
        }
      />

      <nav aria-label="Project sections" className="mb-6 flex gap-1 border-b border-line/60">
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            className={({ isActive }) =>
              cn(
                '-mb-px border-b-2 px-4 py-2 text-sm transition-colors',
                isActive
                  ? 'border-accent font-medium text-accent'
                  : 'border-transparent text-body-muted hover:border-line hover:text-body',
              )
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>

      <Outlet context={{ project }} />

      <EditProjectModal project={project} open={editOpen} onClose={() => setEditOpen(false)} />
      <DeleteProjectDialog project={project} open={deleteOpen} onClose={() => setDeleteOpen(false)} />
    </>
  )
}

function EditProjectModal({ project, open, onClose }: { project: Project; open: boolean; onClose: () => void }) {
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description ?? '')
  const queryClient = useQueryClient()
  const toast = useToast()

  const mutation = useMutation({
    mutationFn: () =>
      updateProject(project.id, { name: name.trim(), description: description.trim() || null }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects })
      toast.success('Project updated')
      onClose()
    },
    onError: (err) => toast.error(err.message),
  })

  return (
    <Modal open={open} onClose={onClose} title="Edit project">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (name.trim()) mutation.mutate()
        }}
      >
        <Field label="Name" required>
          {(id) => (
            <Input id={id} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required />
          )}
        </Field>
        <Field label="Description">
          {(id) => (
            <Textarea
              id={id}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={2000}
            />
          )}
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={!name.trim()} loading={mutation.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function DeleteProjectDialog({ project, open, onClose }: { project: Project; open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()

  const mutation = useMutation({
    mutationFn: () => deleteProject(project.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects })
      toast.success(`Project "${project.name}" deleted`)
      navigate('/projects')
    },
    onError: (err) => toast.error(err.message),
  })

  return (
    <ConfirmDialog
      open={open}
      onClose={onClose}
      onConfirm={() => mutation.mutate()}
      loading={mutation.isPending}
      title={`Delete "${project.name}"?`}
      body={
        <p>
          This permanently deletes the project <strong className="text-body">and all of its datasets,
          trainings, and evaluations</strong>. Model artifacts trained in this project are removed too.
          This cannot be undone.
        </p>
      }
      confirmLabel="Delete project"
    />
  )
}
