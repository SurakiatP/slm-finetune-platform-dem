import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ChatCompletionResponse,
  CompletionResponse,
  EvaluationAccepted,
  ModelArtifact,
  ModelDownloadUrl,
  Page,
  Project,
  SDGJobAccepted,
  SeedUploadResponse,
  TrainingJobAccepted,
  UsageSummaryResponse,
} from '@/api/types'
import { mockFetch } from '@/mocks/mockEngine'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const responsePromise = mockFetch(path, init)
  await vi.advanceTimersByTimeAsync(120)
  const response = await responsePromise
  expect(response.ok).toBe(true)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

const jsonRequest = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

describe('mock Engine user flow', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it('runs project → seed/SDG → training → export → evaluation → inference', async () => {
    const project = await request<Project>('/api/v1/projects', jsonRequest({
      name: 'Flow project',
      task_type: 'qa',
    }))

    const form = new FormData()
    form.set('project_id', project.id)
    form.set('task_type', 'qa')
    form.set('file', new File(['{"question":"Q","answer":"A"}\n'], 'seed.jsonl'))
    const seed = await request<SeedUploadResponse>('/api/v1/datasets/upload-seed', {
      method: 'POST',
      body: form,
    })

    const sdg = await request<SDGJobAccepted>('/api/v1/datasets/generate', jsonRequest({
      sdg_mode: 'with_seed',
      project_id: project.id,
      task_type: 'qa',
      task_description: 'Answer grounded questions',
      num_samples: 20,
      seed_dataset_id: seed.dataset_id,
    }))
    expect(sdg.status).toBe('running')

    const training = await request<TrainingJobAccepted>('/api/v1/trainings', jsonRequest({
      mode: 'manual',
      project_id: project.id,
      dataset_id: seed.dataset_id,
      base_model: 'Qwen/Qwen2.5-0.5B-Instruct',
      training_name: `flow-${project.id}`,
    }))
    expect(training.websocket_url).toContain(training.job_id)

    await vi.advanceTimersByTimeAsync(8_000)
    const models = await request<Page<ModelArtifact>>(`/api/v1/models?project_id=${project.id}`)
    expect(models.items).toHaveLength(1)
    const model = models.items[0]

    await request('/api/v1/models/' + model.id + '/export', jsonRequest({ format: 'lora' }))
    const download = await request<ModelDownloadUrl>(`/api/v1/models/${model.id}/download-url?format=lora`)
    expect(download.format).toBe('lora')
    expect(download.files.length).toBeGreaterThan(1)

    const evaluation = await request<EvaluationAccepted>('/api/v1/evaluations', jsonRequest({
      model_artifact_id: model.id,
      dataset_id: seed.dataset_id,
      use_llm_judge: true,
    }))
    expect(evaluation.status).toBe('running')

    const chat = await request<ChatCompletionResponse>('/api/v1/inference/chat/completions', jsonRequest({
      model: model.name,
      messages: [{ role: 'user', content: 'Hello' }],
    }))
    expect(chat.choices[0].message.content).toContain('Hello')

    const completion = await request<CompletionResponse>('/api/v1/inference/completions', jsonRequest({
      model: model.name,
      prompt: 'Complete this',
    }))
    expect(completion.object).toBe('text_completion')

    const usage = await request<UsageSummaryResponse>('/api/v1/usage')
    expect(usage.has_unpriced_usage).toBe(true)

    const deleteInUsePromise = mockFetch(`/api/v1/datasets/${seed.dataset_id}`, { method: 'DELETE' })
    await vi.advanceTimersByTimeAsync(120)
    const deleteInUse = await deleteInUsePromise
    expect(deleteInUse.status).toBe(409)

    await request<void>(`/api/v1/projects/${project.id}`, { method: 'DELETE' })
    const orphan = await request<{ project_id: string | null }>(`/api/v1/datasets/${seed.dataset_id}`)
    expect(orphan.project_id).toBeNull()
  })
})
