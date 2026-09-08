import { useEffect, useState } from 'react'
import { Navigate, Outlet, useLocation, useParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'

import { getProject, listProjects } from '@/api/endpoints/projects'

const LEGACY_STORAGE_KEY = 'slm_engine_meta'

function mappedEngineId(legacyId: string): string | null {
  try {
    const store = JSON.parse(localStorage.getItem(LEGACY_STORAGE_KEY) ?? '{}') as Record<
      string,
      { engineProjectId?: string }
    >
    return store[legacyId]?.engineProjectId ?? null
  } catch {
    return null
  }
}

export function LegacyProjectRoute() {
  const { id } = useParams<{ id: string }>()
  const location = useLocation()
  const [canonicalId, setCanonicalId] = useState<string | null>(null)
  const [state, setState] = useState<'checking' | 'ready' | 'missing'>('checking')

  useEffect(() => {
    let active = true
    setState('checking')
    setCanonicalId(null)

    if (!id) {
      setState('missing')
      return () => { active = false }
    }

    const localId = mappedEngineId(id)
    if (localId && localId !== id) {
      setCanonicalId(localId)
      return () => { active = false }
    }

    void getProject(id)
      .then(() => {
        if (active) setState('ready')
      })
      .catch(async () => {
        try {
          const page = await listProjects({ external_project_id: id, limit: 1 })
          if (!active) return
          if (page.items[0]) setCanonicalId(page.items[0].id)
          else setState('missing')
        } catch {
          if (active) setState('missing')
        }
      })

    return () => { active = false }
  }, [id])

  if (canonicalId && id) {
    const nextPath = location.pathname.replace(`/projects/${id}`, `/projects/${canonicalId}`)
    return <Navigate to={`${nextPath}${location.search}${location.hash}`} replace />
  }

  if (state === 'checking') {
    return (
      <div className="flex min-h-48 items-center justify-center" aria-label="Resolving project">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (state === 'missing') {
    return (
      <div className="mx-auto max-w-xl py-16 text-center">
        <h1 className="text-xl font-semibold">Project is not available in the Engine</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          This may be a legacy Supabase-only project that has no Engine mapping and cannot be
          migrated automatically.
        </p>
      </div>
    )
  }

  return <Outlet />
}

