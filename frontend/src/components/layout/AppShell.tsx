import { Suspense } from 'react'
import { Outlet } from 'react-router-dom'

import { Sidebar } from '@/components/layout/Sidebar'
import { LoadingBlock } from '@/components/ui/Spinner'

export function AppShell() {
  return (
    <div className="flex min-h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-[70] focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:text-bg"
      >
        Skip to main content
      </a>
      <Sidebar />
      <main id="main" className="min-w-0 flex-1">
        <div className="mx-auto max-w-6xl px-6 py-6 max-md:px-4">
          <Suspense fallback={<LoadingBlock />}>
            <Outlet />
          </Suspense>
        </div>
      </main>
    </div>
  )
}
