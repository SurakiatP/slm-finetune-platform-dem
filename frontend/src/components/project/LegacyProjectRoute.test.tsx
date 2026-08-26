import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ getProject: vi.fn(), listProjects: vi.fn() }))
vi.mock('@/api/endpoints/projects', () => ({
  getProject: mocks.getProject,
  listProjects: mocks.listProjects,
}))

import { LegacyProjectRoute } from '@/components/project/LegacyProjectRoute'

describe('LegacyProjectRoute', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('redirects a legacy external id to the canonical Engine project id', async () => {
    mocks.getProject.mockImplementation((id: string) =>
      id === 'engine-id' ? Promise.resolve({ id }) : Promise.reject(new Error('not found')),
    )
    mocks.listProjects.mockResolvedValue({ items: [{ id: 'engine-id' }], total: 1, limit: 1, offset: 0 })

    render(
      <MemoryRouter initialEntries={['/projects/legacy-id/insights']}>
        <Routes>
          <Route path="/projects/:id" element={<LegacyProjectRoute />}>
            <Route path="insights" element={<div>canonical project page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('canonical project page')).toBeInTheDocument()
    expect(mocks.listProjects).toHaveBeenCalledWith({ external_project_id: 'legacy-id', limit: 1 })
  })
})

