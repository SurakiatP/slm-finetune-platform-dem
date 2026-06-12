import { api, pageQuery } from '@/api/client'
import type { Page, Project, ProjectCreate, ProjectUpdate } from '@/api/types'

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
