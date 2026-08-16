import { api, pageQuery } from '@/api/client'
import type { AuditEvent, Page, Project, ProjectCreate, ProjectUpdate, UsageEvent } from '@/api/types'

const BASE = '/api/v1/projects'

export function listProjects(params: { limit?: number; offset?: number } = {}): Promise<Page<Project>> {
  return api.get(`${BASE}${pageQuery(params)}`)
}

export function getProject(id: string): Promise<Project> {
  return api.get(`${BASE}/${id}`)
}

export function createProject(body: ProjectCreate): Promise<Project> {
  return api.post(BASE, body)
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
