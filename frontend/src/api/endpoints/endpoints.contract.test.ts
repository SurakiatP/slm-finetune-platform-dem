import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    postForm: vi.fn(),
  },
}))

vi.mock('@/api/client', () => ({
  api: mocks.api,
  pageQuery: (params: Record<string, string | number | undefined | null>) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
    })
    const value = query.toString()
    return value ? `?${value}` : ''
  },
}))

import * as datasets from '@/api/endpoints/datasets'
import * as evaluations from '@/api/endpoints/evaluations'
import * as inference from '@/api/endpoints/inference'
import * as jobs from '@/api/endpoints/jobs'
import * as meta from '@/api/endpoints/meta'
import * as models from '@/api/endpoints/models'
import * as projects from '@/api/endpoints/projects'
import * as system from '@/api/endpoints/system'
import * as trainings from '@/api/endpoints/trainings'
import * as usage from '@/api/endpoints/usage'

describe('OpenAPI endpoint wrappers', () => {
  beforeEach(() => vi.clearAllMocks())

  it('covers project CRUD, legacy lookup, activity, and usage', () => {
    projects.listProjects({ external_project_id: 'legacy', limit: 1 })
    projects.getProject('p1')
    projects.createProject({ name: 'P', task_type: 'qa' })
    projects.updateProject('p1', { name: 'P2' })
    projects.deleteProject('p1')
    projects.getProjectActivity('p1', { limit: 20 })
    projects.getProjectUsage('p1', { offset: 20 })

    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/projects?external_project_id=legacy&limit=1')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/projects/p1')
    expect(mocks.api.post).toHaveBeenCalledWith('/api/v1/projects', { name: 'P', task_type: 'qa' })
    expect(mocks.api.patch).toHaveBeenCalledWith('/api/v1/projects/p1', { name: 'P2' })
    expect(mocks.api.delete).toHaveBeenCalledWith('/api/v1/projects/p1')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/projects/p1/activity?limit=20')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/projects/p1/usage?offset=20')
  })

  it('covers dataset upload, generation, preview, downloads, cancel, and delete', () => {
    const file = new File(['{}'], 'seed.jsonl')
    datasets.listDatasets({ project_id: 'p1' })
    datasets.getDataset('d1')
    datasets.previewDataset('d1', 10)
    datasets.uploadSeedDataset({ project_id: 'p1', task_type: 'qa', file })
    datasets.generateDataset({
      sdg_mode: 'with_seed', project_id: 'p1', task_type: 'qa', task_description: 'Answer questions',
      num_samples: 10, seed_dataset_id: 'seed1',
    }, 'idem')
    datasets.getDatasetDownloadUrl('d1', 'inline')
    datasets.cancelDatasetGeneration('d1')
    datasets.deleteDataset('d1')

    expect(datasets.datasetDownloadUrl('d1')).toBe('/api/v1/datasets/d1/download')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/datasets?project_id=p1')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/datasets/d1/preview?limit=10')
    expect(mocks.api.postForm).toHaveBeenCalledWith('/api/v1/datasets/upload-seed', expect.any(FormData))
    expect(mocks.api.post).toHaveBeenCalledWith('/api/v1/datasets/generate', expect.objectContaining({ seed_dataset_id: 'seed1' }), { idempotencyKey: 'idem' })
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/datasets/d1/download-url?disposition=inline')
    expect(mocks.api.post).toHaveBeenCalledWith('/api/v1/datasets/d1/cancel')
    expect(mocks.api.delete).toHaveBeenCalledWith('/api/v1/datasets/d1')
  })

  it('covers training and model lifecycle endpoints', () => {
    trainings.listTrainings({ project_id: 'p1', status: 'running' })
    trainings.getTraining('t1')
    trainings.startTraining({ mode: 'manual', project_id: 'p1', dataset_id: 'd1' }, 'idem')
    trainings.cancelTraining('t1')
    trainings.getMlflowUrl('t1')
    trainings.getTrainingMetrics('t1')
    trainings.getLossHistory('t1')
    models.listModels({ project_id: 'p1' })
    models.getModel('m1')
    models.exportModel('m1', { format: 'gguf', quantization: 'q4_k_m' })
    models.cancelModelExport('m1')
    models.getModelDownloadUrl('m1', 'lora')

    expect(mocks.api.post).toHaveBeenCalledWith('/api/v1/trainings', expect.objectContaining({ mode: 'manual' }), { idempotencyKey: 'idem' })
    expect(mocks.api.delete).toHaveBeenCalledWith('/api/v1/trainings/t1')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/trainings/t1/mlflow-url')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/trainings/t1/metrics')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/trainings/t1/loss-history')
    expect(mocks.api.post).toHaveBeenCalledWith('/api/v1/models/m1/export', { format: 'gguf', quantization: 'q4_k_m' })
    expect(mocks.api.post).toHaveBeenCalledWith('/api/v1/models/m1/export/cancel')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/models/m1/download-url?format=lora')
    expect(models.modelDownloadUrl('m1')).toBe('/api/v1/models/m1/download?format=gguf')
  })

  it('covers evaluations, inference, metadata, jobs, usage, and system probes', () => {
    evaluations.startEvaluation({ model_artifact_id: 'm1', dataset_id: 'd1', use_llm_judge: true }, 'idem')
    evaluations.listEvaluations({ status: 'completed' })
    evaluations.getEvaluation('e1')
    evaluations.cancelEvaluation('e1')
    evaluations.compareEvaluations({ evaluation_ids: ['e1', 'e2'] })
    inference.chatCompletions({ model: 'tag', messages: [{ role: 'user', content: 'hi' }] })
    inference.completions({ model: 'tag', prompt: 'hi' })
    inference.listInferenceModels()
    jobs.getJobProgress('j1')
    meta.listTaskTypes()
    meta.getTaskExample('classification')
    meta.listBaseModels()
    meta.getSdgPipelineModels()
    usage.getUsageSummary()
    system.getHealth()
    system.getReadiness()

    expect(mocks.api.post).toHaveBeenCalledWith('/api/v1/evaluations', expect.objectContaining({ use_llm_judge: true }), { idempotencyKey: 'idem' })
    expect(mocks.api.post).toHaveBeenCalledWith('/api/v1/evaluations/compare', { evaluation_ids: ['e1', 'e2'] })
    expect(mocks.api.post).toHaveBeenCalledWith('/api/v1/inference/chat/completions', expect.any(Object))
    expect(mocks.api.post).toHaveBeenCalledWith('/api/v1/inference/completions', expect.any(Object))
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/inference/models')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/jobs/j1/progress')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/tasks')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/base-models')
    expect(mocks.api.get).toHaveBeenCalledWith('/api/v1/usage')
    expect(mocks.api.get).toHaveBeenCalledWith('/health')
    expect(mocks.api.get).toHaveBeenCalledWith('/ready')
  })
})
