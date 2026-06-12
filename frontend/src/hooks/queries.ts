import { useQuery } from '@tanstack/react-query'

import * as datasets from '@/api/endpoints/datasets'
import * as evaluations from '@/api/endpoints/evaluations'
import * as inference from '@/api/endpoints/inference'
import * as meta from '@/api/endpoints/meta'
import * as models from '@/api/endpoints/models'
import * as projects from '@/api/endpoints/projects'
import * as trainings from '@/api/endpoints/trainings'
import type { Dataset, Evaluation, JobStatus, ModelArtifact, Page, Training } from '@/api/types'

/**
 * Narrow structural type for TanStack's refetchInterval callback — lets pages
 * stop polling based on the fetched resource (e.g. once a job is terminal).
 */
export type RefetchInterval<T> =
  | number
  | false
  | ((query: { state: { data: T | undefined; status: 'pending' | 'error' | 'success' } }) =>
      number | false | undefined)

/** Centralised query keys so mutations can invalidate consistently. */
export const queryKeys = {
  projects: ['projects'] as const,
  project: (id: string) => ['projects', id] as const,
  datasets: (projectId?: string) => ['datasets', { projectId: projectId ?? null }] as const,
  dataset: (id: string) => ['datasets', 'detail', id] as const,
  datasetPreview: (id: string) => ['datasets', 'preview', id] as const,
  trainings: (projectId?: string, status?: JobStatus) =>
    ['trainings', { projectId: projectId ?? null, status: status ?? null }] as const,
  training: (id: string) => ['trainings', 'detail', id] as const,
  mlflowUrl: (id: string) => ['trainings', 'mlflow-url', id] as const,
  models: (projectId?: string) => ['models', { projectId: projectId ?? null }] as const,
  model: (id: string) => ['models', 'detail', id] as const,
  evaluation: (id: string) => ['evaluations', 'detail', id] as const,
  inferenceModels: ['inference', 'models'] as const,
  taskTypes: ['meta', 'tasks'] as const,
  baseModels: ['meta', 'base-models'] as const,
}

// --- Projects ---------------------------------------------------------------

export function useProjects(params: { limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: [...queryKeys.projects, params],
    queryFn: () => projects.listProjects(params),
  })
}

export function useProject(id: string) {
  return useQuery({
    queryKey: queryKeys.project(id),
    queryFn: () => projects.getProject(id),
  })
}

// --- Datasets ---------------------------------------------------------------

export function useDatasets(projectId?: string, page: { limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: [...queryKeys.datasets(projectId), page],
    queryFn: () => datasets.listDatasets({ project_id: projectId, ...page }),
  })
}

export function useDataset(id: string, opts: { refetchInterval?: RefetchInterval<Dataset> } = {}) {
  return useQuery({
    queryKey: queryKeys.dataset(id),
    queryFn: () => datasets.getDataset(id),
    refetchInterval: opts.refetchInterval,
  })
}

export function useDatasetPreview(id: string, limit = 20, enabled = true) {
  return useQuery({
    queryKey: [...queryKeys.datasetPreview(id), limit],
    queryFn: () => datasets.previewDataset(id, limit),
    enabled,
  })
}

// --- Trainings ----------------------------------------------------------------

export function useTrainings(
  params: { project_id?: string; status?: JobStatus; limit?: number; offset?: number } = {},
  opts: { refetchInterval?: RefetchInterval<Page<Training>> } = {},
) {
  return useQuery({
    queryKey: [...queryKeys.trainings(params.project_id, params.status), { limit: params.limit, offset: params.offset }],
    queryFn: () => trainings.listTrainings(params),
    refetchInterval: opts.refetchInterval,
  })
}

export function useTraining(id: string, opts: { refetchInterval?: RefetchInterval<Training> } = {}) {
  return useQuery({
    queryKey: queryKeys.training(id),
    queryFn: () => trainings.getTraining(id),
    refetchInterval: opts.refetchInterval,
  })
}

export function useMlflowUrl(id: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.mlflowUrl(id),
    queryFn: () => trainings.getMlflowUrl(id),
    enabled,
  })
}

/** MLflow-backed loss series — backfills the chart since the WS has no replay. */
export function useLossHistory(id: string, enabled = true) {
  return useQuery({
    queryKey: ['trainings', 'loss-history', id],
    queryFn: () => trainings.getLossHistory(id),
    enabled,
    staleTime: 30_000,
  })
}

// --- Models -------------------------------------------------------------------

export function useModels(projectId?: string, page: { limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: [...queryKeys.models(projectId), page],
    queryFn: () => models.listModels({ project_id: projectId, ...page }),
  })
}

export function useModel(id: string, opts: { refetchInterval?: RefetchInterval<ModelArtifact> } = {}) {
  return useQuery({
    queryKey: queryKeys.model(id),
    queryFn: () => models.getModel(id),
    refetchInterval: opts.refetchInterval,
  })
}

// --- Evaluations ----------------------------------------------------------------

export function useEvaluation(id: string, opts: { refetchInterval?: RefetchInterval<Evaluation>; enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.evaluation(id),
    queryFn: () => evaluations.getEvaluation(id),
    refetchInterval: opts.refetchInterval,
    enabled: opts.enabled,
  })
}

// --- Inference / metadata --------------------------------------------------------

export function useInferenceModels() {
  return useQuery({
    queryKey: queryKeys.inferenceModels,
    queryFn: inference.listInferenceModels,
  })
}

export function useTaskTypes() {
  return useQuery({
    queryKey: queryKeys.taskTypes,
    queryFn: meta.listTaskTypes,
    staleTime: Infinity,
  })
}

export function useBaseModels() {
  return useQuery({
    queryKey: queryKeys.baseModels,
    queryFn: meta.listBaseModels,
    staleTime: Infinity,
  })
}
