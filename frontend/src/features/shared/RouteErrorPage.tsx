import { AlertTriangle } from 'lucide-react'
import { Link, useRouteError } from 'react-router-dom'

export function RouteErrorPage() {
  const error = useRouteError()
  const message = error instanceof Error ? error.message : 'Something went wrong while rendering this page.'

  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg p-6">
      <div className="max-w-md rounded-lg border border-danger/40 bg-surface p-6 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-danger" aria-hidden />
        <h1 className="mt-3 text-lg font-semibold text-body">Unexpected error</h1>
        <p className="mt-2 break-words font-mono text-xs text-body-muted">{message}</p>
        <Link
          to="/"
          reloadDocument
          className="mt-4 inline-flex h-9 items-center rounded-md bg-accent px-4 text-sm font-semibold text-bg transition-colors hover:bg-accent-hover"
        >
          Back to dashboard
        </Link>
      </div>
    </main>
  )
}
