import { lazy } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'

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
        ],
      },
      { path: '/models', element: <ModelListPage /> },
      { path: '/models/:modelId', element: <ModelDetailPage /> },
      { path: '/playground', element: <PlaygroundPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

export default function App() {
  return (
    <ToastProvider>
      <RouterProvider router={router} />
    </ToastProvider>
  )
}
