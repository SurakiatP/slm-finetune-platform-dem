import { lazy } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import { AuthProvider, useAuth } from '@/auth/AuthProvider'
import { LoginPage } from '@/auth/LoginPage'
import { AppShell } from '@/components/layout/AppShell'
import { ToastProvider } from '@/components/ui/Toast'
import { NotFoundPage } from '@/features/shared/NotFoundPage'
import { RouteErrorPage } from '@/features/shared/RouteErrorPage'

const DashboardPage = lazy(() => import('@/features/dashboard/DashboardPage'))
const ProjectListPage = lazy(() => import('@/features/projects/ProjectListPage'))
const ProjectLayout = lazy(() => import('@/features/projects/ProjectLayout'))
const DatasetListPage = lazy(() => import('@/features/datasets/DatasetListPage'))
const DatasetDetailPage = lazy(() => import('@/features/datasets/DatasetDetailPage'))
const TrainingListPage = lazy(() => import('@/features/trainings/TrainingListPage'))
const TrainingDetailPage = lazy(() => import('@/features/trainings/TrainingDetailPage'))
const EvaluationListPage = lazy(() => import('@/features/evaluations/EvaluationListPage'))
const ModelListPage = lazy(() => import('@/features/models/ModelListPage'))
const ModelDetailPage = lazy(() => import('@/features/models/ModelDetailPage'))
const PlaygroundPage = lazy(() => import('@/features/playground/PlaygroundPage'))
const UsagePage = lazy(() => import('@/features/usage/UsagePage'))
const ProjectActivityPage = lazy(() => import('@/features/projects/ProjectActivityPage'))
const ProjectUsagePage = lazy(() => import('@/features/projects/ProjectUsagePage'))

const router = createBrowserRouter([
  {
    element: <AppShell />,
    errorElement: <RouteErrorPage />,
    children: [
      { path: '/', element: <DashboardPage /> },
      { path: '/projects', element: <ProjectListPage /> },
      {
        path: '/projects/:projectId',
        element: <ProjectLayout />,
        children: [
          { path: 'datasets', element: <DatasetListPage /> },
          { path: 'datasets/:datasetId', element: <DatasetDetailPage /> },
          { path: 'trainings', element: <TrainingListPage /> },
          { path: 'trainings/:trainingId', element: <TrainingDetailPage /> },
          { path: 'evaluations', element: <EvaluationListPage /> },
          { path: 'activity', element: <ProjectActivityPage /> },
          { path: 'usage', element: <ProjectUsagePage /> },
        ],
      },
      { path: '/models', element: <ModelListPage /> },
      { path: '/models/:modelId', element: <ModelDetailPage /> },
      { path: '/playground', element: <PlaygroundPage /> },
      { path: '/usage', element: <UsagePage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

/**
 * Auth gate: with VITE_SUPABASE_* unset this renders the router
 * immediately (auth-less dev mode). With them set, the router is withheld
 * until a Supabase session exists — so no query ever fires unauthenticated
 * and the 401→login flicker never happens.
 */
function Gate() {
  const { enabled, session, loading } = useAuth()
  if (enabled && loading) return null
  if (enabled && !session) return <LoginPage />
  return <RouterProvider router={router} />
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Gate />
      </ToastProvider>
    </AuthProvider>
  )
}
