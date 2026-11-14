import { api, pageQuery } from '@/api/client'
import type { AuditEvent, Page, Project, ProjectCreate, ProjectUpdate, UsageEvent } from '@/api/types'

const BASE = '/api/v1/projects'

export function listProjects(
  params: { limit?: number; offset?: number; external_project_id?: string } = {},
): Promise<Page<Project>> {
  return api.get(`${BASE}${pageQuery(params)}`)
}

export function getProject(id: string): Promise<Project> {
  return api.get(`${BASE}/${id}`)
}

/**
 * Template-based creation requires an Idempotency-Key (1-200 chars). Reuse the
 * same key when retrying the same request; a new request body needs a new key.
 */
export function createProject(body: ProjectCreate, idempotencyKey?: string): Promise<Project> {
  return api.post(BASE, body, { idempotencyKey })
}

export function updateProject(id: string, body: ProjectUpdate): Promise<Project> {
  return api.patch(`${BASE}/${id}`, body)
}

export function deleteProject(id: string): Promise<void> {
  return api.delete(`${BASE}/${id}`)
}

/** Audit trail for one project, newest first. Server caps `limit` at 200. */
export function getProjectActivity(
  id: string,
  params: { limit?: number; offset?: number } = {},
): Promise<Page<AuditEvent>> {
  return api.get(`${BASE}/${id}/activity${pageQuery(params)}`)
}

/** Raw usage/cost event log for one project, newest first. Server caps `limit` at 200. */
export function getProjectUsage(
  id: string,
  params: { limit?: number; offset?: number } = {},
): Promise<Page<UsageEvent>> {
  return api.get(`${BASE}/${id}/usage${pageQuery(params)}`)
}
