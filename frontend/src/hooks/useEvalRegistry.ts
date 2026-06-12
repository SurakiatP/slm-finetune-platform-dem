import { useCallback, useState } from 'react'

/**
 * The backend has no `GET /evaluations` list endpoint (only POST / GET by id /
 * compare), so evaluation ids created from this browser are remembered in
 * localStorage per project. Known gap — replace with a real list endpoint when
 * the backend grows one.
 */
const storageKey = (projectId: string) => `slm-platform:evals:${projectId}`

function load(projectId: string): string[] {
  try {
    const raw = localStorage.getItem(storageKey(projectId))
    const parsed = raw ? (JSON.parse(raw) as unknown) : []
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

export function useEvalRegistry(projectId: string) {
  const [ids, setIds] = useState<string[]>(() => load(projectId))

  const add = useCallback(
    (id: string) => {
      setIds((prev) => {
        const next = prev.includes(id) ? prev : [id, ...prev]
        localStorage.setItem(storageKey(projectId), JSON.stringify(next))
        return next
      })
    },
    [projectId],
  )

  const remove = useCallback(
    (id: string) => {
      setIds((prev) => {
        const next = prev.filter((x) => x !== id)
        localStorage.setItem(storageKey(projectId), JSON.stringify(next))
        return next
      })
    },
    [projectId],
  )

  return { ids, add, remove }
}
