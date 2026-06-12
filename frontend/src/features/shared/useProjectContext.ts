import { useOutletContext } from 'react-router-dom'

import type { Project } from '@/api/types'

/** Access the project provided by ProjectLayout's <Outlet context>. */
export function useProjectContext(): { project: Project } {
  return useOutletContext<{ project: Project }>()
}
