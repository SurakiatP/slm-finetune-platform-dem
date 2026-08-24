import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import * as datasets from '@/api/endpoints/datasets'
import * as evaluations from '@/api/endpoints/evaluations'
import * as inference from '@/api/endpoints/inference'
import * as meta from '@/api/endpoints/meta'
import * as models from '@/api/endpoints/models'
import * as projects from '@/api/endpoints/projects'
import * as trainings from '@/api/endpoints/trainings'
import * as usage from '@/api/endpoints/usage'
import type {
  ArtifactFormat,
  AuditEvent,
  Dataset,
  DatasetUpdate,
  Evaluation,
  EvaluationCompareRequest,
  EvaluationCreate,
  JobStatus,
  ModelArtifact,
  ModelExportRequest,
  Page,
  Project,
  ProjectCreate,
  ProjectUpdate,
  SDGRequest,
  TaskType,
  Training,
  TrainingMetrics,
  TrainingRequest,
  UsageEvent,
  UsageSummaryResponse,
} from '@/api/types'

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
  evaluations: ['evaluations'] as const,
  inferenceModels: ['inference', 'models'] as const,
  taskTypes: ['meta', 'tasks'] as const,
  taskExample: (taskType: TaskType) => ['meta', 'tasks', taskType, 'example'] as const,
  baseModels: ['meta', 'base-models'] as const,
  sdgPipelineModels: ['meta', 'sdg-pipeline'] as const,
  usageSummary: ['usage', 'summary'] as const,
  projectActivity: (id: string) => ['projects', id, 'activity'] as const,
  projectUsage: (id: string) => ['projects', id, 'usage'] as const,
  trainingMetrics: (id: string) => ['trainings', 'metrics', id] as const,
}

// --- Projects: queries -------------------------------------------------------

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

export function useProjectActivity(projectId: string, page: { limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: [...queryKeys.projectActivity(projectId), page],
    queryFn: (): Promise<Page<AuditEvent>> => projects.getProjectActivity(projectId, page),
  })
}

export function useProjectUsage(projectId: string, page: { limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: [...queryKeys.projectUsage(projectId), page],
    queryFn: (): Promise<Page<UsageEvent>> => projects.getProjectUsage(projectId, page),
  })
}

// --- Projects: mutations ------------------------------------------------------

export function useCreateProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ProjectCreate): Promise<Project> => projects.createProject(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects })
    },
  })
}

export function useUpdateProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ProjectUpdate }): Promise<Project> =>
      projects.updateProject(id, body),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects })
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(id) })
    },
  })
}

export function useDeleteProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string): Promise<void> => projects.deleteProject(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects })
    },
  })
}

// --- Datasets: queries --------------------------------------------------------

export function useDatasets(
  projectId?: string,
  page: { limit?: number; offset?: number } = {},
  opts: { refetchInterval?: RefetchInterval<Page<Dataset>> } = {},
) {
  return useQuery({
    queryKey: [...queryKeys.datasets(projectId), page],
    queryFn: () => datasets.listDatasets({ project_id: projectId, ...page }),
    refetchInterval: opts.refetchInterval,
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

// --- Datasets: mutations -------------------------------------------------------

export function useUploadSeedDataset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Parameters<typeof datasets.uploadSeedDataset>[0]) =>
      datasets.uploadSeedDataset(input),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets(variables.project_id) })
    },
  })
}

export function useGenerateDataset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: SDGRequest) => datasets.generateDataset(body),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets(variables.project_id) })
    },
  })
}

/** Idempotent cancel of a running SDG generation job. */
export function useCancelDatasetGeneration() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => datasets.cancelDatasetGeneration(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: ['datasets'] })
      void queryClient.invalidateQueries({ queryKey: queryKeys.dataset(id) })
    },
  })
}

export function useDeleteDataset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string): Promise<void> => datasets.deleteDataset(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['datasets'] })
    },
  })
}

export function useUpdateDataset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DatasetUpdate }): Promise<Dataset> =>
      datasets.updateDataset(id, body),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: ['datasets'] })
      void queryClient.invalidateQueries({ queryKey: queryKeys.dataset(id) })
    },
  })
}

/** Mint a presigned MinIO URL for the dataset's stored object (one-shot, not cached). */
export function useDatasetDownloadUrl() {
  return useMutation({
    // Accepts a bare id (attachment download, the historical shape) or
    // `{ id, disposition: 'inline' }` for a view-in-browser URL.
    mutationFn: (input: string | { id: string; disposition?: 'attachment' | 'inline' }) =>
      typeof input === 'string'
        ? datasets.getDatasetDownloadUrl(input)
        : datasets.getDatasetDownloadUrl(input.id, input.disposition),
  })
}

// --- Trainings: queries ---------------------------------------------------------

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

export function useTrainingMetrics(id: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.trainingMetrics(id),
    queryFn: (): Promise<TrainingMetrics> => trainings.getTrainingMetrics(id),
    enabled,
    staleTime: 30_000,
  })
}

// --- Trainings: mutations ---------------------------------------------------------

export function useStartTraining() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: TrainingRequest) => trainings.startTraining(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['trainings'] })
    },
  })
}

export function useCancelTraining() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => trainings.cancelTraining(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.training(id) })
      void queryClient.invalidateQueries({ queryKey: ['trainings'] })
    },
  })
}

/** Hard-deletes a terminal training run. */
export function useDeleteTraining() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string): Promise<void> => trainings.deleteTraining(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: ['trainings'] })
      void queryClient.invalidateQueries({ queryKey: queryKeys.training(id) })
    },
  })
}

// --- Models: queries -------------------------------------------------------------

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

// --- Models: mutations -------------------------------------------------------------

export function useExportModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ModelExportRequest }) => models.exportModel(id, body),
    onSuccess: (_data, { id }) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.model(id) })
      void queryClient.invalidateQueries({ queryKey: ['models'] })
    },
  })
}

/** Idempotent cancel of an in-progress model export. */
export function useCancelModelExport() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => models.cancelModelExport(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.model(id) })
      void queryClient.invalidateQueries({ queryKey: ['models'] })
    },
  })
}

export function useDeleteModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string): Promise<void> => models.deleteModel(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: ['models'] })
      void queryClient.invalidateQueries({ queryKey: queryKeys.model(id) })
    },
  })
}

/** Mint presigned MinIO URL(s) for a previously-exported artifact (one-shot, not cached). */
export function useModelDownloadUrl() {
  return useMutation({
    mutationFn: ({ id, format = 'gguf' }: { id: string; format?: ArtifactFormat }) =>
      models.getModelDownloadUrl(id, format),
  })
}

// --- Evaluations: queries ----------------------------------------------------------

export function useEvaluation(id: string, opts: { refetchInterval?: RefetchInterval<Evaluation>; enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.evaluation(id),
    queryFn: () => evaluations.getEvaluation(id),
    refetchInterval: opts.refetchInterval,
    enabled: opts.enabled,
  })
}

export function useEvaluations(
  params: { model_artifact_id?: string; dataset_id?: string; status?: JobStatus; limit?: number; offset?: number } = {},
  opts: { refetchInterval?: RefetchInterval<Page<Evaluation>>; enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: [...queryKeys.evaluations, params],
    queryFn: () => evaluations.listEvaluations(params),
    refetchInterval: opts.refetchInterval,
    enabled: opts.enabled,
  })
}

// --- Evaluations: mutations ----------------------------------------------------------

export function useStartEvaluation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: EvaluationCreate) => evaluations.startEvaluation(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.evaluations })
    },
  })
}

/** Idempotent — cancelling an already-terminal evaluation just echoes its current status. */
export function useCancelEvaluation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => evaluations.cancelEvaluation(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.evaluation(id) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.evaluations })
    },
  })
}

export function useCompareEvaluations() {
  return useMutation({
    mutationFn: (body: EvaluationCompareRequest) => evaluations.compareEvaluations(body),
  })
}

// --- Usage --------------------------------------------------------------------

export function useUsageSummary() {
  return useQuery({
    queryKey: queryKeys.usageSummary,
    queryFn: (): Promise<UsageSummaryResponse> => usage.getUsageSummary(),
    staleTime: 60_000,
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

/** Worked example payload for one task type — used to preview the expected sample shape. */
export function useTaskExample(taskType: TaskType | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.taskExample(taskType ?? 'classification'),
    queryFn: () => meta.getTaskExample(taskType as TaskType),
    enabled: enabled && taskType !== undefined,
  })
}

export function useBaseModels() {
  return useQuery({
    queryKey: queryKeys.baseModels,
    queryFn: meta.listBaseModels,
    staleTime: Infinity,
  })
}

export function useSdgPipelineModels() {
  return useQuery({
    queryKey: queryKeys.sdgPipelineModels,
    queryFn: meta.getSdgPipelineModels,
    staleTime: Infinity,
  })
}
