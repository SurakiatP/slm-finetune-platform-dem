/**
 * In-memory mock of the Engine REST API, used when `VITE_MOCK=1` (see
 * `npm run dev:mock`). Lets every page in the app render with realistic data
 * and behave interactively (create/delete/cancel mutate this store) with no
 * backend running at all.
 *
 * `mockFetch` is a drop-in replacement for the global `fetch` used by
 * `src/api/client.ts` — same signature, returns a real `Response` so
 * `res.ok` / `res.status` / `res.json()` all work unmodified.
 *
 * Every entity shape below is typed against `src/api/types.ts` (the mirror
 * of the backend Pydantic schemas), so a shape drift here fails `tsc`.
 */
import type {
  ArtifactFormat,
  AuditEvent,
  BaseModelInfo,
  ChatCompletionResponse,
  Dataset,
  DatasetDownloadUrl,
  DatasetPreview,
  Evaluation,
  EvaluationAccepted,
  EvaluationCompareRequest,
  EvaluationCompareResponse,
  EvaluationCreate,
  MlflowUrlResponse,
  ModelArtifact,
  ModelDescriptorList,
  ModelDownloadUrl,
  ModelExportAccepted,
  ModelExportRequest,
  Page,
  Project,
  ProjectCreate,
  ProjectUpdate,
  SDGJobAccepted,
  SDGRequest,
  SdgPipelineModels,
  SeedUploadResponse,
  TaskType,
  TaskTypeInfo,
  Training,
  TrainingJobAccepted,
  TrainingLossHistory,
  TrainingMetrics,
  TrainingRequest,
  UsageEvent,
  UsageSummaryResponse,
  WSMessage,
} from '@/api/types'

// --- small utilities ---------------------------------------------------------

let seq = 1000
function genId(prefix: string): string {
  seq += 1
  return `${prefix}-${seq.toString(36)}`
}

function isoAt(msAgo: number): string {
  return new Date(Date.now() - msAgo).toISOString()
}

const MIN = 60_000
const HOUR = 60 * MIN
const DAY = 24 * HOUR

function json(data: unknown, status = 200): Response {
  if (status === 204) return new Response(null, { status: 204 })
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function errorResponse(status: number, detail: string, code: string | null = null): Response {
  return json({ detail, code, extra: null }, status)
}

function page<T>(items: T[], limit: number, offset: number): Page<T> {
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset }
}

function parseQuery(url: URL): Record<string, string> {
  const out: Record<string, string> = {}
  url.searchParams.forEach((v, k) => {
    out[k] = v
  })
  return out
}

// --- store --------------------------------------------------------------------

interface Store {
  projects: Project[]
  datasets: Dataset[]
  trainings: Training[]
  models: ModelArtifact[]
  evaluations: Evaluation[]
  usageEvents: UsageEvent[]
  auditEvents: AuditEvent[]
  /** Latest WS-shaped snapshot per celery_task_id — what `getJobProgress` serves. */
  jobProgress: Record<string, WSMessage>
}

const BASE_MODEL = 'unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit'
const BASE_MODEL_2 = 'unsloth/Llama-3.2-3B-Instruct-bnb-4bit'

function seedStore(): Store {
  const projects: Project[] = [
    {
      id: 'p1',
      name: 'Support Ticket Classifier',
      description: 'Routes inbound support tickets to the right team by intent.',
      task_type: 'classification',
      external_project_id: null,
      queue_state: 'processing',
      queue_position: null,
      owner_queue_position: null,
      created_at: isoAt(9 * DAY),
      updated_at: isoAt(20 * MIN),
    },
    {
      id: 'p2',
      name: 'Product Manual QA',
      description: 'Answers customer questions against the product manual.',
      task_type: 'qa',
      external_project_id: null,
      queue_state: 'queued',
      queue_position: 1,
      owner_queue_position: 1,
      created_at: isoAt(5 * DAY),
      updated_at: isoAt(3 * HOUR),
    },
    {
      id: 'p3',
      name: 'Calendar Tool Router',
      description: 'Picks the right calendar function call from a free-text request.',
      task_type: 'tool_calling',
      external_project_id: null,
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      created_at: isoAt(1 * DAY),
      updated_at: isoAt(1 * DAY),
    },
  ]

  const datasets: Dataset[] = [
    // --- project 1: seed, completed SDG, running SDG (progress-able) ---
    {
      id: 'ds-p1-seed',
      project_id: 'p1',
      name: 'ticket-seed-240',
      task_type: 'classification',
      source: 'seed',
      status: 'completed',
      error_message: null,
      num_samples: 240,
      storage_uri: 's3://mock-bucket/datasets/ds-p1-seed.jsonl',
      size_bytes: 84_000,
      generation_metadata: null,
      parent_dataset_id: null,
      created_at: isoAt(9 * DAY),
      updated_at: isoAt(9 * DAY),
    },
    {
      id: 'ds-p1-sdg-done',
      project_id: 'p1',
      name: 'ticket-sdg-900',
      task_type: 'classification',
      source: 'sdg',
      status: 'completed',
      error_message: null,
      num_samples: 900,
      storage_uri: 's3://mock-bucket/datasets/ds-p1-sdg-done.jsonl',
      size_bytes: 410_000,
      // Regular SDG output (not a holdout split) — parent_dataset_id stays
      // null so `datasetRoleTag` doesn't misclassify it as hold-out; the
      // seed used is recorded informationally in generation_metadata.
      generation_metadata: { celery_task_id: 'job-sdg-p1-done', loops: 3, seed_dataset_id: 'ds-p1-seed' },
      parent_dataset_id: null,
      created_at: isoAt(8 * DAY),
      updated_at: isoAt(8 * DAY),
    },
    {
      // Holdout split held out of `ds-p1-sdg-done` — exercises the
      // `datasetRoleTag` "hold-out" branch and gives the auto-pipeline
      // evaluate step (see POST /trainings) a real holdout to evaluate
      // tr-p1-manual-done / tr-p1-hpo-running against.
      id: 'ds-p1-holdout',
      project_id: 'p1',
      name: 'ticket-sdg-900-holdout',
      task_type: 'classification',
      source: 'sdg',
      status: 'completed',
      error_message: null,
      num_samples: 90,
      storage_uri: 's3://mock-bucket/datasets/ds-p1-holdout.jsonl',
      size_bytes: 41_000,
      generation_metadata: { celery_task_id: 'job-sdg-p1-done', role: 'holdout' },
      parent_dataset_id: 'ds-p1-sdg-done',
      created_at: isoAt(8 * DAY),
      updated_at: isoAt(8 * DAY),
    },
    {
      id: 'ds-p1-sdg-running',
      project_id: 'p1',
      name: 'ticket-sdg-900-v2',
      task_type: 'classification',
      source: 'sdg',
      status: 'running',
      error_message: null,
      num_samples: 0,
      storage_uri: null,
      size_bytes: null,
      generation_metadata: { celery_task_id: 'job-sdg-p1-running', seed_dataset_id: 'ds-p1-seed' },
      parent_dataset_id: null,
      created_at: isoAt(20 * MIN),
      updated_at: isoAt(1 * MIN),
    },
    // --- project 2: seed, completed SDG ---
    {
      id: 'ds-p2-seed',
      project_id: 'p2',
      name: 'manual-seed-180',
      task_type: 'qa',
      source: 'seed',
      status: 'completed',
      error_message: null,
      num_samples: 180,
      storage_uri: 's3://mock-bucket/datasets/ds-p2-seed.jsonl',
      size_bytes: 96_000,
      generation_metadata: null,
      parent_dataset_id: null,
      created_at: isoAt(5 * DAY),
      updated_at: isoAt(5 * DAY),
    },
    {
      id: 'ds-p2-sdg-done',
      project_id: 'p2',
      name: 'manual-sdg-650',
      task_type: 'qa',
      source: 'sdg',
      status: 'completed',
      error_message: null,
      num_samples: 650,
      storage_uri: 's3://mock-bucket/datasets/ds-p2-sdg-done.jsonl',
      size_bytes: 512_000,
      generation_metadata: { celery_task_id: 'job-sdg-p2-done', loops: 2, seed_dataset_id: 'ds-p2-seed' },
      parent_dataset_id: null,
      created_at: isoAt(4 * DAY),
      updated_at: isoAt(4 * DAY),
    },
    // --- project 3: seed, completed SDG, failed SDG ---
    {
      id: 'ds-p3-seed',
      project_id: 'p3',
      name: 'router-seed-150',
      task_type: 'tool_calling',
      source: 'seed',
      status: 'completed',
      error_message: null,
      num_samples: 150,
      storage_uri: 's3://mock-bucket/datasets/ds-p3-seed.jsonl',
      size_bytes: 61_000,
      generation_metadata: null,
      parent_dataset_id: null,
      created_at: isoAt(1 * DAY),
      updated_at: isoAt(1 * DAY),
    },
    {
      id: 'ds-p3-sdg-done',
      project_id: 'p3',
      name: 'router-sdg-500',
      task_type: 'tool_calling',
      source: 'sdg',
      status: 'completed',
      error_message: null,
      num_samples: 500,
      storage_uri: 's3://mock-bucket/datasets/ds-p3-sdg-done.jsonl',
      size_bytes: 220_000,
      generation_metadata: { celery_task_id: 'job-sdg-p3-done', loops: 1, seed_dataset_id: 'ds-p3-seed' },
      parent_dataset_id: null,
      created_at: isoAt(23 * HOUR),
      updated_at: isoAt(23 * HOUR),
    },
    {
      id: 'ds-p3-sdg-failed',
      project_id: 'p3',
      name: 'router-sdg-failed',
      task_type: 'tool_calling',
      source: 'sdg',
      status: 'failed',
      error_message:
        'CUDA out of memory. Tried to allocate 1.24 GiB (GPU 0; 12.00 GiB total capacity; 11.31 GiB already allocated; 210 MiB free) while warming up the local judge model.',
      num_samples: 0,
      storage_uri: null,
      size_bytes: null,
      generation_metadata: { celery_task_id: 'job-sdg-p3-failed', seed_dataset_id: 'ds-p3-seed' },
      parent_dataset_id: null,
      created_at: isoAt(6 * HOUR),
      updated_at: isoAt(6 * HOUR),
    },
    // --- orphan: project deleted, dataset survives with project_id: null ---
    {
      id: 'ds-orphan-1',
      project_id: null,
      name: 'legacy-seed-orphaned',
      task_type: 'classification',
      source: 'seed',
      status: 'completed',
      error_message: null,
      num_samples: 60,
      storage_uri: 's3://mock-bucket/datasets/ds-orphan-1.jsonl',
      size_bytes: 21_000,
      generation_metadata: null,
      parent_dataset_id: null,
      created_at: isoAt(30 * DAY),
      updated_at: isoAt(30 * DAY),
    },
  ]

  const trainings: Training[] = [
    {
      id: 'tr-p1-manual-done',
      project_id: 'p1',
      dataset_id: 'ds-p1-sdg-done',
      mode: 'manual',
      status: 'completed',
      celery_task_id: 'job-tr-p1-manual',
      base_model: BASE_MODEL,
      training_name: 'ticket-classifier-manual-v1',
      mlflow_experiment_id: '1',
      mlflow_run_id: 'mlflow-run-p1-manual',
      config_json: { num_train_epochs: 3, learning_rate: 0.0002 },
      best_metric_value: 0.0842,
      best_params_json: null,
      error_message: null,
      started_at: isoAt(8 * DAY),
      ended_at: isoAt(8 * DAY - 40 * MIN),
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      created_at: isoAt(8 * DAY),
      updated_at: isoAt(8 * DAY - 40 * MIN),
      // Demonstrates a fully-settled auto pipeline: art-1 is this training's
      // export target and ev-1 is the evaluation against its holdout.
      auto_export: true,
      auto_evaluate: true,
      auto_pipeline: {
        export: { status: 'completed', artifact_id: 'art-1', error: null },
        evaluate: { status: 'completed', evaluation_id: 'ev-1', skip_reason: null, error: null },
      },
    },
    {
      id: 'tr-p1-hpo-running',
      project_id: 'p1',
      dataset_id: 'ds-p1-sdg-done',
      mode: 'hpo',
      status: 'running',
      celery_task_id: 'job-tr-p1-hpo',
      base_model: BASE_MODEL,
      training_name: 'ticket-classifier-hpo-v2',
      mlflow_experiment_id: '1',
      mlflow_run_id: 'mlflow-run-p1-hpo',
      config_json: { n_trials: 8 },
      best_metric_value: 0.0791,
      best_params_json: { learning_rate: '0.00018', lora_r: '16' },
      error_message: null,
      started_at: isoAt(20 * MIN),
      ended_at: null,
      queue_state: 'processing',
      queue_position: null,
      owner_queue_position: null,
      created_at: isoAt(20 * MIN),
      updated_at: isoAt(1 * MIN),
      auto_export: false,
      auto_evaluate: false,
      auto_pipeline: null,
    },
    {
      id: 'tr-p2-pending',
      project_id: 'p2',
      dataset_id: 'ds-p2-sdg-done',
      mode: 'manual',
      status: 'pending',
      celery_task_id: 'job-tr-p2-pending',
      base_model: BASE_MODEL_2,
      training_name: 'manual-qa-v1',
      mlflow_experiment_id: null,
      mlflow_run_id: null,
      config_json: { num_train_epochs: 2 },
      best_metric_value: null,
      best_params_json: null,
      error_message: null,
      started_at: null,
      ended_at: null,
      queue_state: 'queued',
      queue_position: 1,
      owner_queue_position: 1,
      created_at: isoAt(3 * HOUR),
      updated_at: isoAt(3 * HOUR),
      auto_export: false,
      auto_evaluate: false,
      auto_pipeline: null,
    },
    {
      id: 'tr-p2-failed',
      project_id: 'p2',
      dataset_id: 'ds-p2-sdg-done',
      mode: 'manual',
      status: 'failed',
      celery_task_id: 'job-tr-p2-failed',
      base_model: BASE_MODEL_2,
      training_name: 'manual-qa-oom',
      mlflow_experiment_id: '2',
      mlflow_run_id: 'mlflow-run-p2-failed',
      config_json: { num_train_epochs: 3, per_device_train_batch_size: 8 },
      best_metric_value: null,
      best_params_json: null,
      error_message:
        'CUDA out of memory. Tried to allocate 2.10 GiB (GPU 0; 12.00 GiB total capacity; 11.62 GiB already allocated; 78 MiB free). Try lowering per_device_train_batch_size or max_seq_length.',
      started_at: isoAt(2 * DAY),
      ended_at: isoAt(2 * DAY - 6 * MIN),
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      created_at: isoAt(2 * DAY),
      updated_at: isoAt(2 * DAY - 6 * MIN),
      auto_export: false,
      auto_evaluate: false,
      auto_pipeline: null,
    },
    {
      // p2 is the seeded `qa` project — gives Models/ModelDetail/Compare a
      // completed qa-task artifact with a judge score to show alongside
      // p1's classification one (art-1), and a second fully-settled
      // auto_pipeline demo (see art-4 / ev-5 below).
      id: 'tr-p2-manual-done',
      project_id: 'p2',
      dataset_id: 'ds-p2-sdg-done',
      mode: 'manual',
      status: 'completed',
      celery_task_id: 'job-tr-p2-manual',
      base_model: BASE_MODEL_2,
      training_name: 'faq-answerer-manual-v1',
      mlflow_experiment_id: '3',
      mlflow_run_id: 'mlflow-run-p2-manual',
      config_json: { num_train_epochs: 3, learning_rate: 0.0002 },
      best_metric_value: 0.1024,
      best_params_json: null,
      error_message: null,
      started_at: isoAt(4 * DAY),
      ended_at: isoAt(4 * DAY - 35 * MIN),
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      created_at: isoAt(4 * DAY),
      updated_at: isoAt(4 * DAY - 35 * MIN),
      auto_export: true,
      auto_evaluate: true,
      auto_pipeline: {
        export: { status: 'completed', artifact_id: 'art-4', error: null },
        evaluate: { status: 'completed', evaluation_id: 'ev-5', skip_reason: null, error: null },
      },
    },
  ]

  const models: ModelArtifact[] = [
    {
      id: 'art-1',
      training_job_id: 'tr-p1-manual-done',
      name: 'support-ticket-classifier-v1',
      base_model: BASE_MODEL,
      mlflow_run_id: 'mlflow-run-p1-manual',
      lora_adapter_uri: 's3://mock-bucket/artifacts/art-1/lora/',
      gguf_uri: 's3://mock-bucket/artifacts/art-1/model.q4_k_m.gguf',
      safetensors_uri: null,
      size_mb: 860,
      ollama_model_tag: 'support-ticket-classifier-v1:latest',
      export_error_message: null,
      export_status: 'completed',
      export_celery_task_id: 'job-export-art1',
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      created_at: isoAt(8 * DAY - 40 * MIN),
      updated_at: isoAt(8 * DAY - 30 * MIN),
      base_ollama_tag: 'qwen2.5:1.5b',
    },
    {
      id: 'art-2',
      training_job_id: 'tr-p1-manual-done',
      name: 'support-ticket-classifier-v1-safetensors',
      base_model: BASE_MODEL,
      mlflow_run_id: 'mlflow-run-p1-manual',
      lora_adapter_uri: null,
      gguf_uri: null,
      safetensors_uri: null,
      size_mb: null,
      ollama_model_tag: null,
      export_error_message: null,
      export_status: 'running',
      export_celery_task_id: 'job-export-art2',
      queue_state: 'processing',
      queue_position: null,
      owner_queue_position: null,
      created_at: isoAt(15 * MIN),
      updated_at: isoAt(1 * MIN),
      base_ollama_tag: 'qwen2.5:1.5b',
    },
    {
      id: 'art-3',
      training_job_id: 'tr-p2-failed',
      name: 'manual-qa-v0-export-attempt',
      base_model: BASE_MODEL_2,
      mlflow_run_id: null,
      lora_adapter_uri: null,
      gguf_uri: null,
      safetensors_uri: null,
      size_mb: null,
      ollama_model_tag: null,
      export_error_message:
        'GGUF conversion failed: CUDA out of memory while quantizing to q4_k_m (GPU 0; 3.2 GiB/12.0 GiB reserved by another job).',
      export_status: 'failed',
      export_celery_task_id: 'job-export-art3',
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      created_at: isoAt(1 * DAY),
      updated_at: isoAt(1 * DAY - 10 * MIN),
      base_ollama_tag: 'llama3.2:3b',
    },
    {
      id: 'art-4',
      training_job_id: 'tr-p2-manual-done',
      name: 'faq-answerer-v1',
      base_model: BASE_MODEL_2,
      mlflow_run_id: 'mlflow-run-p2-manual',
      lora_adapter_uri: 's3://mock-bucket/artifacts/art-4/lora/',
      gguf_uri: 's3://mock-bucket/artifacts/art-4/model.q4_k_m.gguf',
      safetensors_uri: null,
      size_mb: 1720,
      ollama_model_tag: 'faq-answerer-v1:latest',
      export_error_message: null,
      export_status: 'completed',
      export_celery_task_id: 'job-export-art4',
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      created_at: isoAt(4 * DAY - 35 * MIN),
      updated_at: isoAt(4 * DAY - 25 * MIN),
      base_ollama_tag: 'llama3.2:3b',
    },
  ]

  const evaluations: Evaluation[] = [
    {
      id: 'ev-1',
      model_artifact_id: 'art-1',
      dataset_id: 'ds-p1-sdg-done',
      celery_task_id: 'job-eval-1',
      status: 'completed',
      metrics_json: { accuracy: 0.942, f1_macro: 0.918, n: 200, out_of_set_predictions: 3 },
      llm_judge_score: 4.3,
      llm_judge_model: 'anthropic/claude-3-haiku',
      error_message: null,
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      started_at: isoAt(7 * DAY),
      ended_at: isoAt(7 * DAY - 4 * MIN),
      created_at: isoAt(7 * DAY),
      updated_at: isoAt(7 * DAY - 4 * MIN),
    },
    {
      id: 'ev-2',
      model_artifact_id: 'art-1',
      dataset_id: 'ds-p2-sdg-done',
      celery_task_id: 'job-eval-2',
      status: 'running',
      metrics_json: null,
      llm_judge_score: null,
      llm_judge_model: null,
      error_message: null,
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      started_at: isoAt(5 * MIN),
      ended_at: null,
      created_at: isoAt(5 * MIN),
      updated_at: isoAt(1 * MIN),
    },
    {
      // Completed so /models/compare has two comparable runs on first load
      // (it needs >= 2 `completed` evaluations before it renders anything).
      // Deliberately weaker than ev-1 so the compare deltas are non-trivial.
      id: 'ev-3',
      model_artifact_id: 'art-1',
      dataset_id: 'ds-p3-sdg-done',
      celery_task_id: 'job-eval-3',
      status: 'completed',
      metrics_json: { accuracy: 0.883, f1_macro: 0.851, n: 150, out_of_set_predictions: 5 },
      llm_judge_score: 3.8,
      llm_judge_model: 'anthropic/claude-3-haiku',
      error_message: null,
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      started_at: isoAt(1 * DAY),
      ended_at: isoAt(1 * DAY - 1 * MIN),
      created_at: isoAt(1 * DAY),
      updated_at: isoAt(1 * DAY - 1 * MIN),
    },
    {
      // Keeps a cancelled row in the seed so the evaluations table still
      // exercises that badge/empty-metrics path (ev-3 used to cover it).
      id: 'ev-4',
      model_artifact_id: 'art-1',
      dataset_id: 'ds-p2-sdg-done',
      celery_task_id: 'job-eval-4',
      status: 'cancelled',
      metrics_json: null,
      llm_judge_score: null,
      llm_judge_model: null,
      error_message: null,
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      started_at: isoAt(2 * DAY),
      ended_at: isoAt(2 * DAY - 1 * MIN),
      created_at: isoAt(2 * DAY),
      updated_at: isoAt(2 * DAY - 1 * MIN),
    },
    {
      // art-4's auto-eval — the qa-task counterpart to ev-1, so a judge
      // score is visible on a `qa` project's model without digging into p1.
      id: 'ev-5',
      model_artifact_id: 'art-4',
      dataset_id: 'ds-p2-sdg-done',
      celery_task_id: 'job-eval-5',
      status: 'completed',
      metrics_json: { rouge1: 0.812, rougeL: 0.774, bleu: 0.612, n: 120 },
      llm_judge_score: 4.5,
      llm_judge_model: 'anthropic/claude-3-haiku',
      error_message: null,
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      started_at: isoAt(4 * DAY - 30 * MIN),
      ended_at: isoAt(4 * DAY - 27 * MIN),
      created_at: isoAt(4 * DAY - 30 * MIN),
      updated_at: isoAt(4 * DAY - 27 * MIN),
    },
  ]

  const usageEvents: UsageEvent[] = [
    { id: 'ue-1', created_at: isoAt(8 * DAY), actor_id: null, project_id: 'p1', job_id: 'job-sdg-p1-done', provider: 'openrouter', model: 'openai/gpt-4o-mini', stage: 'sdg_generation', prompt_tokens: 128_000, completion_tokens: 64_000, cost_usd: '3.8400', outcome: 'success' },
    { id: 'ue-2', created_at: isoAt(8 * DAY), actor_id: null, project_id: 'p1', job_id: 'job-sdg-p1-done', provider: 'openrouter', model: 'anthropic/claude-3-haiku', stage: 'judge', prompt_tokens: 42_000, completion_tokens: 9_000, cost_usd: '0.5610', outcome: 'success' },
    { id: 'ue-3', created_at: isoAt(7 * DAY), actor_id: null, project_id: 'p1', job_id: 'job-eval-1', provider: 'openrouter', model: 'anthropic/claude-3-haiku', stage: 'evaluation_judge', prompt_tokens: 18_000, completion_tokens: 6_000, cost_usd: '0.2160', outcome: 'success' },
    { id: 'ue-4', created_at: isoAt(4 * DAY), actor_id: null, project_id: 'p2', job_id: 'job-sdg-p2-done', provider: 'openrouter', model: 'openai/gpt-4o-mini', stage: 'sdg_generation', prompt_tokens: 96_000, completion_tokens: 51_000, cost_usd: '2.8800', outcome: 'success' },
    { id: 'ue-5', created_at: isoAt(2 * DAY), actor_id: null, project_id: 'p2', job_id: 'job-tr-p2-failed', provider: 'openrouter', model: 'openai/gpt-4o-mini', stage: 'sdg_generation', prompt_tokens: 4_000, completion_tokens: 900, cost_usd: null, outcome: 'success' },
    { id: 'ue-6', created_at: isoAt(23 * HOUR), actor_id: null, project_id: 'p3', job_id: 'job-sdg-p3-done', provider: 'openrouter', model: 'meta-llama/llama-3.1-8b-instruct', stage: 'sdg_generation', prompt_tokens: 51_000, completion_tokens: 22_000, cost_usd: '0.7300', outcome: 'success' },
    { id: 'ue-7', created_at: isoAt(6 * HOUR), actor_id: null, project_id: 'p3', job_id: 'job-sdg-p3-failed', provider: 'openrouter', model: 'anthropic/claude-3-haiku', stage: 'judge', prompt_tokens: 6_000, completion_tokens: 400, cost_usd: '0.0410', outcome: 'failure' },
  ]

  const auditEvents: AuditEvent[] = [
    { id: 'ae-p1-1', created_at: isoAt(9 * DAY), actor_id: null, project_id: 'p1', action: 'project.created', resource_type: 'project', resource_id: 'p1', outcome: 'success', request_id: genId('req'), metadata: null },
    { id: 'ae-p1-2', created_at: isoAt(9 * DAY), actor_id: null, project_id: 'p1', action: 'dataset.seed_uploaded', resource_type: 'dataset', resource_id: 'ds-p1-seed', outcome: 'success', request_id: genId('req'), metadata: null },
    { id: 'ae-p1-3', created_at: isoAt(8 * DAY), actor_id: null, project_id: 'p1', action: 'dataset.generated', resource_type: 'dataset', resource_id: 'ds-p1-sdg-done', outcome: 'success', request_id: genId('req'), metadata: null },
    { id: 'ae-p1-4', created_at: isoAt(8 * DAY), actor_id: null, project_id: 'p1', action: 'training.completed', resource_type: 'training', resource_id: 'tr-p1-manual-done', outcome: 'success', request_id: genId('req'), metadata: null },
    { id: 'ae-p1-5', created_at: isoAt(8 * DAY - 30 * MIN), actor_id: null, project_id: 'p1', action: 'model.exported', resource_type: 'model_artifact', resource_id: 'art-1', outcome: 'success', request_id: genId('req'), metadata: null },
    { id: 'ae-p1-6', created_at: isoAt(20 * MIN), actor_id: null, project_id: 'p1', action: 'training.started', resource_type: 'training', resource_id: 'tr-p1-hpo-running', outcome: 'success', request_id: genId('req'), metadata: null },
    { id: 'ae-p2-1', created_at: isoAt(5 * DAY), actor_id: null, project_id: 'p2', action: 'project.created', resource_type: 'project', resource_id: 'p2', outcome: 'success', request_id: genId('req'), metadata: null },
    { id: 'ae-p2-2', created_at: isoAt(2 * DAY), actor_id: null, project_id: 'p2', action: 'training.failed', resource_type: 'training', resource_id: 'tr-p2-failed', outcome: 'failure', request_id: genId('req'), metadata: null },
    { id: 'ae-p2-3', created_at: isoAt(3 * HOUR), actor_id: null, project_id: 'p2', action: 'training.queued', resource_type: 'training', resource_id: 'tr-p2-pending', outcome: 'success', request_id: genId('req'), metadata: null },
    { id: 'ae-p3-1', created_at: isoAt(1 * DAY), actor_id: null, project_id: 'p3', action: 'project.created', resource_type: 'project', resource_id: 'p3', outcome: 'success', request_id: genId('req'), metadata: null },
    { id: 'ae-p3-2', created_at: isoAt(6 * HOUR), actor_id: null, project_id: 'p3', action: 'dataset.generation_failed', resource_type: 'dataset', resource_id: 'ds-p3-sdg-failed', outcome: 'failure', request_id: genId('req'), metadata: null },
  ]

  const jobProgress: Record<string, WSMessage> = {
    'job-sdg-p1-running': {
      type: 'sdg_progress',
      job_id: 'job-sdg-p1-running',
      timestamp: isoAt(1 * MIN),
      phase: 'generating',
      samples_generated: 340,
      samples_target: 900,
      samples_valid: 310,
      samples_rejected: 22,
      duplicates_removed: 8,
      current_loop: 2,
      judge_rejected: 14,
      judge_parse_failures: 1,
      dedup_rejected: 8,
    },
    'job-tr-p1-hpo': {
      type: 'hpo_progress',
      job_id: 'job-tr-p1-hpo',
      timestamp: isoAt(1 * MIN),
      trial_number: 4,
      trials_total: 8,
      current_params: { learning_rate: 0.00021, lora_r: 16, num_train_epochs: 3 },
      best_value: 0.0791,
      best_params: { learning_rate: 0.00018, lora_r: 16 },
      last_trial_value: 0.0855,
      last_trial_pruned: false,
      inner_progress: {
        type: 'training_progress',
        job_id: 'job-tr-p1-hpo',
        timestamp: isoAt(1 * MIN),
        epoch: 1.8,
        epochs_total: 3,
        step: 142,
        steps_total: 240,
        train_loss: 0.412,
        eval_loss: 0.398,
        learning_rate: 0.00021,
        samples_per_second: 6.4,
        gpu_memory_mb: 9840,
      },
    },
    'job-export-art2': {
      type: 'export_progress',
      job_id: 'job-export-art2',
      timestamp: isoAt(1 * MIN),
      stage: 'quantizing',
      detail: 'q4_k_m',
    },
    'job-eval-2': {
      type: 'evaluation_progress',
      job_id: 'job-eval-2',
      timestamp: isoAt(1 * MIN),
      phase: 'scoring',
      rows_done: 120,
      rows_total: 200,
    },
  }

  return { projects, datasets, trainings, models, evaluations, usageEvents, auditEvents, jobProgress }
}

const store = seedStore()

// --- simulated liveness ------------------------------------------------------
// Mutations that kick off a job (generate/train/export/evaluate) settle the
// underlying entity to a terminal state after a short delay so the REST
// polling every page already does (see useDatasets/useTrainings/etc.
// refetchInterval) visibly reflects progress without a real backend.

function settleDataset(id: string, jobId: string, targetSamples: number, delayMs = 6000): void {
  setTimeout(() => {
    const ds = store.datasets.find((d) => d.id === id)
    if (!ds || ds.status !== 'running') return
    ds.status = 'completed'
    ds.num_samples = targetSamples
    ds.storage_uri = `s3://mock-bucket/datasets/${id}.jsonl`
    ds.size_bytes = targetSamples * 450
    ds.updated_at = new Date().toISOString()
    store.jobProgress[jobId] = {
      type: 'completed',
      job_id: jobId,
      timestamp: new Date().toISOString(),
      result: { num_samples: targetSamples },
      mlflow_run_id: null,
      dataset_id: id,
      model_artifact_id: null,
    }
  }, delayMs)
}

/**
 * Phases the backend added alongside the pre-existing SDG phase set
 * (generating/validating/judging/dedup/deduplicating/persisting/
 * format_detection/meta_prompting) — the hold-out split gets carved off and
 * persisted separately from the training data. `phase` on the wire is a
 * plain string (see `SDGProgressMsg` in api/types.ts), so these three don't
 * need a type of their own here either.
 */
const SDG_TAIL_PHASES: Array<{ phase: string; holdMs: number }> = [
  { phase: 'splitting_holdout', holdMs: 800 },
  { phase: 'persisting_train', holdMs: 800 },
  { phase: 'persisting_holdout', holdMs: 800 },
]

/**
 * Ticks `store.jobProgress[jobId]` through a live-looking SDG run instead of
 * the single static snapshot `settleDataset` alone would leave in place:
 * `samples_generated` climbs toward `target` during `generating`, then the
 * three post-generation phases above flash in order. `onDone` fires once the
 * tail phases finish — the one-shot caller (POST /datasets/generate) uses it
 * to settle the dataset to `completed`, exactly like `settleDataset` used to
 * do on a fixed timer. Pass `loop: true` for fixtures that should keep
 * demoing the sequence forever instead of settling once.
 */
function runSdgProgressSimulation(
  jobId: string,
  target: number,
  options: { loop?: boolean; tickMs?: number; ticks?: number; onDone?: () => void } = {},
): void {
  const { loop = false, tickMs = 500, ticks = 6, onDone } = options
  const startGenerated = Math.max(1, Math.round(target * 0.15))

  const emit = (phase: string, generated: number): void => {
    const valid = Math.round(generated * 0.92)
    const duplicates = Math.round(generated * 0.02)
    store.jobProgress[jobId] = {
      type: 'sdg_progress',
      job_id: jobId,
      timestamp: new Date().toISOString(),
      phase,
      samples_generated: generated,
      samples_target: target,
      samples_valid: valid,
      samples_rejected: Math.max(0, generated - valid - duplicates),
      duplicates_removed: duplicates,
      current_loop: 0,
      judge_rejected: 0,
      judge_parse_failures: 0,
      dedup_rejected: duplicates,
    }
  }

  const runGenerating = (onGenDone: () => void): void => {
    let tick = 0
    emit('generating', startGenerated)
    const timer = setInterval(() => {
      tick += 1
      const generated = Math.min(target, startGenerated + Math.round(((target - startGenerated) * tick) / ticks))
      emit('generating', generated)
      if (tick >= ticks) {
        clearInterval(timer)
        onGenDone()
      }
    }, tickMs)
  }

  const runTailPhases = (index: number): void => {
    if (index >= SDG_TAIL_PHASES.length) {
      onDone?.()
      if (loop) runGenerating(() => runTailPhases(0))
      return
    }
    const { phase, holdMs } = SDG_TAIL_PHASES[index]
    emit(phase, target)
    setTimeout(() => runTailPhases(index + 1), holdMs)
  }

  runGenerating(() => runTailPhases(0))
}

function settleTraining(id: string, jobId: string, delayMs = 8000): void {
  setTimeout(() => {
    const tr = store.trainings.find((t) => t.id === id)
    if (!tr || (tr.status !== 'running' && tr.status !== 'pending')) return
    tr.status = 'completed'
    tr.best_metric_value = 0.081
    tr.ended_at = new Date().toISOString()
    tr.queue_state = null
    tr.queue_position = null
    tr.owner_queue_position = null
    tr.updated_at = new Date().toISOString()

    const artifactId = genId('art')
    store.models.push({
      id: artifactId,
      training_job_id: tr.id,
      name: `${tr.training_name ?? tr.id}-artifact`,
      base_model: tr.base_model,
      mlflow_run_id: tr.mlflow_run_id,
      lora_adapter_uri: `s3://mock-bucket/artifacts/${artifactId}/lora/`,
      gguf_uri: null,
      safetensors_uri: null,
      size_mb: null,
      ollama_model_tag: null,
      export_error_message: null,
      export_status: null,
      export_celery_task_id: null,
      queue_state: null,
      queue_position: null,
      owner_queue_position: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      base_ollama_tag: null,
    })

    store.jobProgress[jobId] = {
      type: 'completed',
      job_id: jobId,
      timestamp: new Date().toISOString(),
      result: {},
      mlflow_run_id: tr.mlflow_run_id,
      dataset_id: null,
      model_artifact_id: artifactId,
    }

    if (tr.auto_export || tr.auto_evaluate) runAutoPipeline(tr.id, artifactId)
  }, delayMs)
}

/**
 * Drives `Training.auto_pipeline` after training settles: export (on the
 * artifact `settleTraining` just created) → evaluate (against the holdout
 * sibling of the training's dataset, if one exists). Mirrors the real
 * pipeline's export-then-evaluate ordering; each step is a no-op if its
 * flag wasn't requested (status stays 'skipped').
 *
 * `auto_pipeline` itself is null on the row until this point — matching the
 * backend, which leaves the column null at insert time regardless of the
 * auto_export/auto_evaluate flags and only populates it once the pipeline
 * actually kicks off (see api/models/training_job.py).
 */
function runAutoPipeline(trainingId: string, artifactId: string, stepDelayMs = 4000): void {
  const tr = store.trainings.find((t) => t.id === trainingId)
  if (!tr || (!tr.auto_export && !tr.auto_evaluate)) return
  tr.auto_pipeline = {
    export: { status: tr.auto_export ? 'pending' : 'skipped', artifact_id: null, error: null },
    evaluate: {
      status: tr.auto_evaluate ? 'pending' : 'skipped',
      evaluation_id: null,
      skip_reason: tr.auto_evaluate ? null : 'auto_evaluate not requested',
      error: null,
    },
  }
  const pipeline = tr.auto_pipeline

  const runEvaluate = (): void => {
    if (pipeline.evaluate.status !== 'pending') return
    pipeline.evaluate.status = 'running'
    tr.updated_at = new Date().toISOString()
    setTimeout(() => {
      const holdout = store.datasets.find(
        (d) => d.parent_dataset_id === tr.dataset_id && (d.generation_metadata as { role?: string } | null)?.role === 'holdout',
      )
      if (!holdout) {
        pipeline.evaluate.status = 'skipped'
        pipeline.evaluate.skip_reason = "no holdout dataset found for this training's dataset"
        tr.updated_at = new Date().toISOString()
        return
      }
      const project = store.projects.find((p) => p.id === tr.project_id)
      const useJudge = project?.task_type === 'qa'
      const evId = genId('ev')
      const ev: Evaluation = {
        id: evId,
        model_artifact_id: artifactId,
        dataset_id: holdout.id,
        celery_task_id: genId('job-eval'),
        status: 'completed',
        metrics_json: { accuracy: 0.912, f1_macro: 0.889, n: holdout.num_samples || 50, out_of_set_predictions: 1 },
        llm_judge_score: useJudge ? 4.1 : null,
        llm_judge_model: useJudge ? SDG_PIPELINE_MODELS.judge : null,
        error_message: null,
        queue_state: null,
        queue_position: null,
        owner_queue_position: null,
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      store.evaluations.unshift(ev)
      pipeline.evaluate.status = 'completed'
      pipeline.evaluate.evaluation_id = evId
      tr.updated_at = new Date().toISOString()
    }, stepDelayMs)
  }

  if (pipeline.export.status !== 'pending') {
    runEvaluate()
    return
  }

  pipeline.export.status = 'running'
  tr.updated_at = new Date().toISOString()
  setTimeout(() => {
    const art = store.models.find((m) => m.id === artifactId)
    if (art) {
      art.export_status = 'completed'
      art.gguf_uri = `s3://mock-bucket/artifacts/${artifactId}/model.q4_k_m.gguf`
      art.size_mb = 780
      art.ollama_model_tag = `local/${tr.training_name ?? tr.id}`
      art.updated_at = new Date().toISOString()
    }
    pipeline.export.status = 'completed'
    pipeline.export.artifact_id = artifactId
    tr.updated_at = new Date().toISOString()
    runEvaluate()
  }, stepDelayMs)
}

function settleExport(artifactId: string, jobId: string, format: ArtifactFormat, delayMs = 5000): void {
  setTimeout(() => {
    const art = store.models.find((m) => m.id === artifactId)
    if (!art || art.export_status !== 'running') return
    art.export_status = 'completed'
    art.size_mb = 780
    if (format === 'gguf') art.gguf_uri = `s3://mock-bucket/artifacts/${artifactId}/model.q4_k_m.gguf`
    else art.safetensors_uri = `s3://mock-bucket/artifacts/${artifactId}/safetensors/`
    art.lora_adapter_uri = art.lora_adapter_uri ?? `s3://mock-bucket/artifacts/${artifactId}/lora/`
    art.ollama_model_tag = `${art.name}:latest`
    art.updated_at = new Date().toISOString()
    store.jobProgress[jobId] = {
      type: 'completed',
      job_id: jobId,
      timestamp: new Date().toISOString(),
      result: { format },
      mlflow_run_id: null,
      dataset_id: null,
      model_artifact_id: artifactId,
    }
  }, delayMs)
}

function settleEvaluation(id: string, jobId: string, delayMs = 5000): void {
  setTimeout(() => {
    const ev = store.evaluations.find((e) => e.id === id)
    if (!ev || ev.status !== 'running') return
    ev.status = 'completed'
    ev.metrics_json = { accuracy: 0.887, f1_macro: 0.86, n: 150, out_of_set_predictions: 2 }
    if (ev.llm_judge_model) ev.llm_judge_score = 4.0
    ev.ended_at = new Date().toISOString()
    ev.updated_at = new Date().toISOString()
    store.jobProgress[jobId] = {
      type: 'completed',
      job_id: jobId,
      timestamp: new Date().toISOString(),
      result: {},
      mlflow_run_id: null,
      dataset_id: null,
      model_artifact_id: null,
    }
  }, delayMs)
}

// --- static metadata (task types / base models / sdg pipeline) --------------

const TASK_TYPE_INFO: TaskTypeInfo[] = [
  {
    task_type: 'classification',
    display_name: 'Classification',
    description: 'Assign one label from a fixed set to each input text.',
    sample_schema: { text: 'string', label: 'string' },
    example: { text: 'My order still has not arrived after 2 weeks.', label: 'shipping_delay' },
    sdg_modes_supported: ['with_seed', 'description_only'],
  },
  {
    task_type: 'qa',
    display_name: 'Question Answering',
    description: 'Answer a question, optionally grounded in a source document.',
    sample_schema: { question: 'string', answer: 'string' },
    example: { question: 'How do I reset my password?', answer: 'Go to Settings > Security > Reset password.' },
    sdg_modes_supported: ['with_seed', 'description_only'],
  },
  {
    task_type: 'tool_calling',
    display_name: 'Tool Calling',
    description: 'Pick the correct function and arguments for a free-text request.',
    sample_schema: { question: 'string', answer: 'string (JSON-encoded {name, parameters})' },
    example: {
      question: 'Schedule a meeting with Alex tomorrow at 3pm.',
      answer: JSON.stringify({ name: 'create_event', parameters: { title: 'Meeting with Alex', start: 'tomorrow 15:00' } }),
    },
    sdg_modes_supported: ['with_seed', 'description_only'],
  },
]

const BASE_MODELS: BaseModelInfo[] = [
  {
    id: 'unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit',
    display_name: 'Qwen2.5 1.5B Instruct',
    family: 'qwen',
    params_billions: 1.5,
    context_length: 32_768,
    recommended_max_seq_length: 2048,
    quantization: '4bit',
    license: 'apache-2.0',
    notes: 'Good default for classification and short QA — fast to train on a 12GB GPU.',
  },
  {
    id: 'unsloth/Llama-3.2-3B-Instruct-bnb-4bit',
    display_name: 'Llama 3.2 3B Instruct',
    family: 'llama',
    params_billions: 3,
    context_length: 131_072,
    recommended_max_seq_length: 4096,
    quantization: '4bit',
    license: 'llama3.2',
    notes: 'Larger context window — best for QA over longer source documents.',
  },
  {
    id: 'unsloth/gemma-2-2b-it-bnb-4bit',
    display_name: 'Gemma 2 2B IT',
    family: 'gemma',
    params_billions: 2,
    context_length: 8192,
    recommended_max_seq_length: 2048,
    quantization: '4bit',
    license: 'gemma',
    notes: null,
  },
  {
    id: 'unsloth/SmolLM2-1.7B-Instruct-bnb-4bit',
    display_name: 'SmolLM2 1.7B Instruct',
    family: 'smollm',
    params_billions: 1.7,
    context_length: 8192,
    recommended_max_seq_length: 2048,
    quantization: '4bit',
    license: 'apache-2.0',
    notes: 'Smallest/fastest option — good for quick iteration on tool-calling.',
  },
]

const SDG_PIPELINE_MODELS: SdgPipelineModels = {
  generator: 'openai/gpt-4o-mini',
  judge: 'anthropic/claude-3-haiku',
  diversity_rules: 'meta-llama/llama-3.1-8b-instruct',
}

// --- router -------------------------------------------------------------------

type Handler = (match: RegExpMatchArray, url: URL, init?: RequestInit) => Response | Promise<Response>

interface Route {
  method: string
  pattern: RegExp
  handler: Handler
}

function readJsonBody<T>(init?: RequestInit): T {
  if (!init?.body || typeof init.body !== 'string') return {} as T
  return JSON.parse(init.body) as T
}

const routes: Route[] = [
  // --- projects ---
  {
    method: 'GET',
    pattern: /^\/api\/v1\/projects$/,
    handler: (_m, url) => {
      const q = parseQuery(url)
      const sorted = [...store.projects].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      return json(page(sorted, Number(q.limit ?? 50), Number(q.offset ?? 0)))
    },
  },
  {
    method: 'POST',
    pattern: /^\/api\/v1\/projects$/,
    handler: (_m, _url, init) => {
      const body = readJsonBody<ProjectCreate>(init)
      const proj: Project = {
        id: genId('p'),
        name: body.name,
        description: body.description ?? null,
        task_type: body.task_type,
        external_project_id: null,
        queue_state: null,
        queue_position: null,
        owner_queue_position: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      store.projects.unshift(proj)
      return json(proj, 201)
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/projects\/([^/]+)$/,
    handler: (m) => {
      const proj = store.projects.find((p) => p.id === m[1])
      if (!proj) return errorResponse(404, 'Project not found')
      return json(proj)
    },
  },
  {
    method: 'PATCH',
    pattern: /^\/api\/v1\/projects\/([^/]+)$/,
    handler: (m, _url, init) => {
      const proj = store.projects.find((p) => p.id === m[1])
      if (!proj) return errorResponse(404, 'Project not found')
      const body = readJsonBody<ProjectUpdate>(init)
      if (body.name !== undefined) proj.name = body.name
      if (body.description !== undefined) proj.description = body.description
      proj.updated_at = new Date().toISOString()
      return json(proj)
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/api\/v1\/projects\/([^/]+)$/,
    handler: (m) => {
      const idx = store.projects.findIndex((p) => p.id === m[1])
      if (idx === -1) return errorResponse(404, 'Project not found')
      store.projects.splice(idx, 1)
      // Datasets survive project deletion as orphans (project_id -> null)
      // rather than being cascade-deleted.
      for (const ds of store.datasets) {
        if (ds.project_id === m[1]) {
          ds.project_id = null
          ds.updated_at = new Date().toISOString()
        }
      }
      return json(undefined, 204)
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/projects\/([^/]+)\/activity$/,
    handler: (m, url) => {
      const q = parseQuery(url)
      const rows = store.auditEvents
        .filter((e) => e.project_id === m[1])
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
      return json(page(rows, Number(q.limit ?? 25), Number(q.offset ?? 0)))
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/projects\/([^/]+)\/usage$/,
    handler: (m, url) => {
      const q = parseQuery(url)
      const rows = store.usageEvents
        .filter((e) => e.project_id === m[1])
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
      return json(page(rows, Number(q.limit ?? 25), Number(q.offset ?? 0)))
    },
  },

  // --- datasets ---
  {
    method: 'GET',
    pattern: /^\/api\/v1\/datasets$/,
    handler: (_m, url) => {
      const q = parseQuery(url)
      let rows = [...store.datasets]
      if (q.project_id) rows = rows.filter((d) => d.project_id === q.project_id)
      rows.sort((a, b) => b.created_at.localeCompare(a.created_at))
      return json(page(rows, Number(q.limit ?? 50), Number(q.offset ?? 0)))
    },
  },
  {
    method: 'POST',
    pattern: /^\/api\/v1\/datasets\/upload-seed$/,
    handler: async (_m, _url, init) => {
      const form = init?.body as FormData
      const projectId = String(form.get('project_id') ?? '')
      const taskType = String(form.get('task_type') ?? 'classification') as TaskType
      const name = form.get('name')
      const file = form.get('file') as File | null
      const numSamples = 50 + Math.floor(Math.random() * 250)
      const ds: Dataset = {
        id: genId('ds'),
        project_id: projectId,
        name: (name ? String(name) : file?.name.replace(/\.(jsonl|json|pdf)$/i, '')) || 'uploaded-seed',
        task_type: taskType,
        source: 'seed',
        status: 'completed',
        error_message: null,
        num_samples: numSamples,
        storage_uri: `s3://mock-bucket/datasets/${genId('obj')}.jsonl`,
        size_bytes: numSamples * 350,
        generation_metadata: null,
        parent_dataset_id: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      store.datasets.unshift(ds)
      const resp: SeedUploadResponse = {
        dataset_id: ds.id,
        task_type: taskType,
        num_samples: numSamples,
        invalid_rows: [],
        format_detection: {
          ran: true,
          model_used: SDG_PIPELINE_MODELS.generator,
          field_mapping: {},
          rows_total: numSamples,
          rows_canonicalised: numSamples,
          rows_dropped: 0,
          notes: null,
        },
        pdf_uri: null,
      }
      return json(resp, 201)
    },
  },
  {
    method: 'POST',
    pattern: /^\/api\/v1\/datasets\/generate$/,
    handler: (_m, _url, init) => {
      const body = readJsonBody<SDGRequest>(init)
      const jobId = genId('job-sdg')
      const target = body.num_samples ?? 200
      const seedDatasetId = body.sdg_mode === 'with_seed' ? body.seed_dataset_id : null
      // description_only (no-seed) requests carry per-task-type generation
      // config instead of a seed — stashed in generation_metadata so the
      // preview handler below can render task-appropriate mock rows (real
      // labels / tool names) instead of generic placeholders.
      const classificationConfig = body.sdg_mode === 'description_only' ? body.classification_config ?? null : null
      const toolCallingConfig = body.sdg_mode === 'description_only' ? body.tool_calling_config ?? null : null
      // parent_dataset_id is reserved for the hold-out split's link back to
      // this dataset (see `datasetRoleTag`) — the seed used to generate it
      // is recorded informationally in generation_metadata instead, so a
      // regular SDG output never gets misclassified as "hold-out".
      const ds: Dataset = {
        id: genId('ds'),
        project_id: body.project_id,
        name: body.dataset_name || `sdg-${target}`,
        task_type: body.task_type,
        source: 'sdg',
        status: 'running',
        error_message: null,
        num_samples: 0,
        storage_uri: null,
        size_bytes: null,
        generation_metadata: {
          celery_task_id: jobId,
          seed_dataset_id: seedDatasetId,
          sdg_mode: body.sdg_mode,
          classification_config: classificationConfig,
          tool_calling_config: toolCallingConfig,
        },
        parent_dataset_id: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      store.datasets.unshift(ds)
      // Ticks through generating (samples_generated climbing) and then the
      // three post-generation phases (splitting_holdout -> persisting_train
      // -> persisting_holdout) before settling the dataset — replaces the
      // old single static snapshot + fixed-delay `settleDataset` pairing so
      // dev:mock actually shows a live phase progression.
      runSdgProgressSimulation(jobId, target, {
        onDone: () => {
          const runningDs = store.datasets.find((d) => d.id === ds.id)
          if (!runningDs || runningDs.status !== 'running') return
          runningDs.status = 'completed'
          runningDs.num_samples = target
          runningDs.storage_uri = `s3://mock-bucket/datasets/${ds.id}.jsonl`
          runningDs.size_bytes = target * 450
          runningDs.updated_at = new Date().toISOString()
          store.jobProgress[jobId] = {
            type: 'completed',
            job_id: jobId,
            timestamp: new Date().toISOString(),
            result: { num_samples: target },
            mlflow_run_id: null,
            dataset_id: ds.id,
            model_artifact_id: null,
          }
        },
      })

      const holdoutSize = body.holdout_size ?? 0
      if (holdoutSize > 0) {
        const holdoutJobId = `${jobId}-holdout`
        const holdoutDs: Dataset = {
          id: genId('ds'),
          project_id: body.project_id,
          name: body.holdout_name || `${ds.name}-holdout`,
          task_type: body.task_type,
          source: 'sdg',
          status: 'running',
          error_message: null,
          num_samples: 0,
          storage_uri: null,
          size_bytes: null,
          generation_metadata: {
            celery_task_id: jobId,
            role: 'holdout',
            sdg_mode: body.sdg_mode,
            classification_config: classificationConfig,
            tool_calling_config: toolCallingConfig,
          },
          parent_dataset_id: ds.id,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }
        store.datasets.unshift(holdoutDs)
        settleDataset(holdoutDs.id, holdoutJobId, holdoutSize)
      }

      const resp: SDGJobAccepted = { job_id: jobId, dataset_id: ds.id, status: 'running', websocket_url: `/ws/jobs/${jobId}` }
      return json(resp, 202)
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/datasets\/([^/]+)\/preview$/,
    handler: (m, url) => {
      const ds = store.datasets.find((d) => d.id === m[1])
      if (!ds) return errorResponse(404, 'Dataset not found')
      const q = parseQuery(url)
      const limit = Number(q.limit ?? 20)
      // No-seed (description_only) datasets stash the user-supplied labels /
      // tool definitions in generation_metadata (see the /generate handler
      // above) — use them here so mock rows actually reflect what the user
      // asked for instead of always falling back to generic placeholders.
      const meta = (ds.generation_metadata ?? {}) as {
        classification_config?: { labels: string[] } | null
        tool_calling_config?: { tool_definitions: { name: string }[] } | null
      }
      const mockLabels = meta.classification_config?.labels?.length
        ? meta.classification_config.labels
        : ['billing', 'shipping_delay', 'account_access', 'refund_request']
      const mockTools = meta.tool_calling_config?.tool_definitions?.length
        ? meta.tool_calling_config.tool_definitions
        : [{ name: 'mock_tool' }]
      const samples =
        ds.task_type === 'classification'
          ? Array.from({ length: Math.min(limit, ds.num_samples) }, (_, i) => ({
              text: `Sample support ticket text #${i + 1} for ${ds.name}.`,
              label: mockLabels[i % mockLabels.length],
            }))
          : ds.task_type === 'qa'
            ? Array.from({ length: Math.min(limit, ds.num_samples) }, (_, i) => ({
                question: `Sample question #${i + 1} about ${ds.name}?`,
                answer: `Sample grounded answer #${i + 1}.`,
              }))
            : Array.from({ length: Math.min(limit, ds.num_samples) }, (_, i) => ({
                question: `Sample tool request #${i + 1}.`,
                answer: JSON.stringify({ name: mockTools[i % mockTools.length].name, parameters: { index: i } }),
              }))
      const resp: DatasetPreview = { dataset_id: ds.id, task_type: ds.task_type, samples, total: ds.num_samples }
      return json(resp)
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/datasets\/([^/]+)\/download-url$/,
    handler: (m) => {
      const ds = store.datasets.find((d) => d.id === m[1])
      if (!ds) return errorResponse(404, 'Dataset not found')
      const resp: DatasetDownloadUrl = {
        url: `https://mock-minio.local/datasets/${ds.id}.jsonl?mock=1`,
        filename: `${ds.name}.jsonl`,
        content_type: 'application/jsonl',
        expires_at: new Date(Date.now() + 15 * MIN).toISOString(),
        expires_in: 900,
      }
      return json(resp)
    },
  },
  {
    method: 'POST',
    pattern: /^\/api\/v1\/datasets\/([^/]+)\/cancel$/,
    handler: (m) => {
      const ds = store.datasets.find((d) => d.id === m[1])
      if (!ds) return errorResponse(404, 'Dataset not found')
      if (ds.status === 'pending' || ds.status === 'running') {
        ds.status = 'cancelled'
        ds.updated_at = new Date().toISOString()
      }
      return json({ dataset_id: ds.id, status: ds.status })
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/api\/v1\/datasets\/([^/]+)$/,
    handler: (m) => {
      const idx = store.datasets.findIndex((d) => d.id === m[1])
      if (idx === -1) return errorResponse(404, 'Dataset not found')
      const inUse = store.trainings.some((t) => t.dataset_id === m[1])
      if (inUse) return errorResponse(409, `dataset '${m[1]}' is in use by an existing training`)
      store.datasets.splice(idx, 1)
      return json(undefined, 204)
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/datasets\/([^/]+)$/,
    handler: (m) => {
      const ds = store.datasets.find((d) => d.id === m[1])
      if (!ds) return errorResponse(404, 'Dataset not found')
      return json(ds)
    },
  },

  // --- trainings ---
  {
    method: 'GET',
    pattern: /^\/api\/v1\/trainings$/,
    handler: (_m, url) => {
      const q = parseQuery(url)
      let rows = [...store.trainings]
      if (q.project_id) rows = rows.filter((t) => t.project_id === q.project_id)
      if (q.status) rows = rows.filter((t) => t.status === q.status)
      rows.sort((a, b) => b.created_at.localeCompare(a.created_at))
      return json(page(rows, Number(q.limit ?? 50), Number(q.offset ?? 0)))
    },
  },
  {
    method: 'POST',
    pattern: /^\/api\/v1\/trainings$/,
    handler: (_m, _url, init) => {
      const body = readJsonBody<TrainingRequest>(init)
      if (body.training_name && store.trainings.some((t) => t.training_name === body.training_name)) {
        return errorResponse(409, `training_name '${body.training_name}' already exists`)
      }
      const jobId = genId('job-tr')
      const autoExport = body.auto_export ?? false
      const autoEvaluate = body.auto_evaluate ?? false
      const tr: Training = {
        id: genId('tr'),
        project_id: body.project_id,
        dataset_id: body.dataset_id,
        mode: body.mode,
        status: 'running',
        celery_task_id: jobId,
        base_model: body.base_model ?? BASE_MODEL,
        training_name: body.training_name ?? null,
        mlflow_experiment_id: null,
        mlflow_run_id: genId('mlflow-run'),
        config_json: {},
        best_metric_value: null,
        best_params_json: null,
        error_message: null,
        started_at: new Date().toISOString(),
        ended_at: null,
        queue_state: 'processing',
        queue_position: null,
        owner_queue_position: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        auto_export: autoExport,
        auto_evaluate: autoEvaluate,
        // Matches the backend row default: auto_pipeline stays null until
        // the pipeline actually kicks off (after training completes), even
        // when auto_export/auto_evaluate are true at creation time — see
        // `runAutoPipeline`, which populates it in `settleTraining`.
        auto_pipeline: null,
      }
      store.trainings.unshift(tr)
      store.jobProgress[jobId] = {
        type: 'training_progress',
        job_id: jobId,
        timestamp: new Date().toISOString(),
        epoch: 0.1,
        epochs_total: 3,
        step: 5,
        steps_total: 150,
        train_loss: 1.2,
        eval_loss: null,
        learning_rate: 0.0002,
        samples_per_second: 5.8,
        gpu_memory_mb: 8600,
      }
      settleTraining(tr.id, jobId)
      const resp: TrainingJobAccepted = {
        job_id: jobId,
        training_id: tr.id,
        mlflow_run_id: tr.mlflow_run_id,
        mlflow_url: null,
        status: 'running',
        websocket_url: `/ws/jobs/${jobId}`,
      }
      return json(resp, 202)
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/trainings\/([^/]+)\/mlflow-url$/,
    handler: (m) => {
      const tr = store.trainings.find((t) => t.id === m[1])
      if (!tr) return errorResponse(404, 'Training not found')
      const resp: MlflowUrlResponse = {
        training_id: tr.id,
        mlflow_run_id: tr.mlflow_run_id,
        mlflow_url: tr.mlflow_run_id ? `https://mock-mlflow.local/#/runs/${tr.mlflow_run_id}` : null,
      }
      return json(resp)
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/trainings\/([^/]+)\/metrics$/,
    handler: (m) => {
      const tr = store.trainings.find((t) => t.id === m[1])
      if (!tr) return errorResponse(404, 'Training not found')
      const steps = 12
      const trainLoss = Array.from({ length: steps }, (_, i) => ({
        step: (i + 1) * 20,
        value: Number((1.4 * Math.exp(-i / 6) + 0.08).toFixed(4)),
        timestamp_ms: Date.now() - (steps - i) * 60_000,
      }))
      const evalLoss = trainLoss.map((p) => ({ ...p, value: Number((p.value + 0.03).toFixed(4)) }))
      const resp: TrainingMetrics = {
        training_id: tr.id,
        mlflow_run_id: tr.mlflow_run_id,
        metrics: { train_loss: trainLoss, eval_loss: evalLoss },
        hpo_children:
          tr.mode === 'hpo'
            ? [
                { run_id: genId('trial'), name: 'trial-0', final_eval_loss: 0.091, params: { learning_rate: '0.0001', lora_r: '8' } },
                { run_id: genId('trial'), name: 'trial-1', final_eval_loss: 0.083, params: { learning_rate: '0.00015', lora_r: '16' } },
                { run_id: genId('trial'), name: 'trial-2 (pruned)', final_eval_loss: null, params: { learning_rate: '0.0006', lora_r: '32' } },
                { run_id: genId('trial'), name: 'trial-3 (best)', final_eval_loss: 0.0791, params: { learning_rate: '0.00018', lora_r: '16' } },
              ]
            : null,
      }
      return json(resp)
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/trainings\/([^/]+)\/loss-history$/,
    handler: (m) => {
      const tr = store.trainings.find((t) => t.id === m[1])
      if (!tr) return errorResponse(404, 'Training not found')
      const steps = 10
      const train = Array.from({ length: steps }, (_, i) => ({
        step: (i + 1) * 20,
        value: Number((1.3 * Math.exp(-i / 5) + 0.09).toFixed(4)),
        timestamp_ms: Date.now() - (steps - i) * 60_000,
      }))
      const evalPts = train.map((p) => ({ ...p, value: Number((p.value + 0.02).toFixed(4)) }))
      const resp: TrainingLossHistory = { training_id: tr.id, mlflow_run_id: tr.mlflow_run_id, train_loss: train, eval_loss: evalPts }
      return json(resp)
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/api\/v1\/trainings\/([^/]+)$/,
    handler: (m) => {
      const tr = store.trainings.find((t) => t.id === m[1])
      if (!tr) return errorResponse(404, 'Training not found')
      if (tr.status === 'pending' || tr.status === 'running') {
        tr.status = 'cancelled'
        tr.ended_at = new Date().toISOString()
        tr.queue_state = null
        tr.queue_position = null
        tr.owner_queue_position = null
        tr.updated_at = new Date().toISOString()
      }
      return json({ message: `training ${tr.id} is ${tr.status}` })
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/trainings\/([^/]+)$/,
    handler: (m) => {
      const tr = store.trainings.find((t) => t.id === m[1])
      if (!tr) return errorResponse(404, 'Training not found')
      return json(tr)
    },
  },

  // --- models ---
  {
    method: 'GET',
    pattern: /^\/api\/v1\/models$/,
    handler: (_m, url) => {
      const q = parseQuery(url)
      let rows = [...store.models]
      if (q.project_id) {
        const trainingIds = new Set(store.trainings.filter((t) => t.project_id === q.project_id).map((t) => t.id))
        rows = rows.filter((mo) => trainingIds.has(mo.training_job_id))
      }
      rows.sort((a, b) => b.created_at.localeCompare(a.created_at))
      return json(page(rows, Number(q.limit ?? 50), Number(q.offset ?? 0)))
    },
  },
  {
    method: 'POST',
    pattern: /^\/api\/v1\/models\/([^/]+)\/export$/,
    handler: (m, _url, init) => {
      const art = store.models.find((mo) => mo.id === m[1])
      if (!art) return errorResponse(404, 'Model artifact not found')
      const body = readJsonBody<ModelExportRequest>(init)
      const jobId = genId('job-export')
      art.export_status = 'running'
      art.export_celery_task_id = jobId
      art.export_error_message = null
      art.updated_at = new Date().toISOString()
      store.jobProgress[jobId] = {
        type: 'export_progress',
        job_id: jobId,
        timestamp: new Date().toISOString(),
        stage: 'downloading',
        detail: body.quantization ?? null,
      }
      settleExport(art.id, jobId, body.format)
      const resp: ModelExportAccepted = { artifact_id: art.id, format: body.format, job_id: jobId, status: 'running', websocket_url: `/ws/jobs/${jobId}` }
      return json(resp, 202)
    },
  },
  {
    method: 'POST',
    pattern: /^\/api\/v1\/models\/([^/]+)\/export\/cancel$/,
    handler: (m) => {
      const art = store.models.find((mo) => mo.id === m[1])
      if (!art) return errorResponse(404, 'Model artifact not found')
      if (art.export_status === 'pending' || art.export_status === 'running') {
        art.export_status = 'cancelled'
        art.updated_at = new Date().toISOString()
      }
      return json({ artifact_id: art.id, status: art.export_status ?? 'cancelled' })
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/models\/([^/]+)\/download-url$/,
    handler: (m, url) => {
      const art = store.models.find((mo) => mo.id === m[1])
      if (!art) return errorResponse(404, 'Model artifact not found')
      const q = parseQuery(url)
      const format = (q.format ?? 'gguf') as ArtifactFormat
      const files =
        format === 'gguf'
          ? [{ key: `${art.id}/model.gguf`, name: `${art.name}.gguf`, size_bytes: (art.size_mb ?? 800) * 1024 * 1024, url: `https://mock-minio.local/${art.id}/model.gguf?mock=1` }]
          : [
              { key: `${art.id}/adapter_config.json`, name: 'adapter_config.json', size_bytes: 900, url: `https://mock-minio.local/${art.id}/adapter_config.json?mock=1` },
              { key: `${art.id}/adapter_model.safetensors`, name: 'adapter_model.safetensors', size_bytes: 42_000_000, url: `https://mock-minio.local/${art.id}/adapter_model.safetensors?mock=1` },
              { key: `${art.id}/README.md`, name: 'README.md', size_bytes: 1200, url: `https://mock-minio.local/${art.id}/README.md?mock=1` },
            ]
      const resp: ModelDownloadUrl = { format, files, expires_at: new Date(Date.now() + 15 * MIN).toISOString(), expires_in: 900 }
      return json(resp)
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/models\/([^/]+)$/,
    handler: (m) => {
      const art = store.models.find((mo) => mo.id === m[1])
      if (!art) return errorResponse(404, 'Model artifact not found')
      return json(art)
    },
  },

  // --- evaluations ---
  {
    method: 'POST',
    pattern: /^\/api\/v1\/evaluations$/,
    handler: (_m, _url, init) => {
      const body = readJsonBody<EvaluationCreate>(init)
      const jobId = genId('job-eval')
      const ev: Evaluation = {
        id: genId('ev'),
        model_artifact_id: body.model_artifact_id,
        dataset_id: body.dataset_id,
        celery_task_id: jobId,
        status: 'running',
        metrics_json: null,
        llm_judge_score: null,
        llm_judge_model: body.use_llm_judge ? body.judge_model ?? SDG_PIPELINE_MODELS.judge : null,
        error_message: null,
        queue_state: null,
        queue_position: null,
        owner_queue_position: null,
        started_at: new Date().toISOString(),
        ended_at: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      store.evaluations.unshift(ev)
      store.jobProgress[jobId] = {
        type: 'evaluation_progress',
        job_id: jobId,
        timestamp: new Date().toISOString(),
        phase: 'predicting',
        rows_done: 0,
        rows_total: 150,
      }
      settleEvaluation(ev.id, jobId)
      const resp: EvaluationAccepted = { evaluation_id: ev.id, job_id: jobId, status: 'running', websocket_url: `/ws/jobs/${jobId}` }
      return json(resp, 202)
    },
  },
  {
    method: 'POST',
    pattern: /^\/api\/v1\/evaluations\/compare$/,
    handler: (_m, _url, init) => {
      const body = readJsonBody<EvaluationCompareRequest>(init)
      const evals = body.evaluation_ids.map((id) => store.evaluations.find((e) => e.id === id)).filter((e): e is Evaluation => !!e)
      const metricNames = new Set<string>()
      for (const ev of evals) {
        if (ev.metrics_json) for (const k of Object.keys(ev.metrics_json)) metricNames.add(k)
      }
      const metrics: Record<string, Record<string, number | null>> = {}
      for (const name of metricNames) {
        metrics[name] = {}
        for (const ev of evals) {
          const v = ev.metrics_json?.[name]
          metrics[name][ev.id] = typeof v === 'number' ? v : null
        }
      }
      const judge_scores: Record<string, number | null> = {}
      for (const ev of evals) judge_scores[ev.id] = ev.llm_judge_score
      const resp: EvaluationCompareResponse = { evaluation_ids: body.evaluation_ids, metrics, judge_scores }
      return json(resp)
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/evaluations$/,
    handler: (_m, url) => {
      const q = parseQuery(url)
      let rows = [...store.evaluations]
      if (q.model_artifact_id) rows = rows.filter((e) => e.model_artifact_id === q.model_artifact_id)
      if (q.dataset_id) rows = rows.filter((e) => e.dataset_id === q.dataset_id)
      if (q.status) rows = rows.filter((e) => e.status === q.status)
      rows.sort((a, b) => b.created_at.localeCompare(a.created_at))
      return json(page(rows, Number(q.limit ?? 50), Number(q.offset ?? 0)))
    },
  },
  {
    method: 'POST',
    pattern: /^\/api\/v1\/evaluations\/([^/]+)\/cancel$/,
    handler: (m) => {
      const ev = store.evaluations.find((e) => e.id === m[1])
      if (!ev) return errorResponse(404, 'Evaluation not found')
      if (ev.status === 'pending' || ev.status === 'running') {
        ev.status = 'cancelled'
        ev.ended_at = new Date().toISOString()
        ev.updated_at = new Date().toISOString()
      }
      return json({ evaluation_id: ev.id, status: ev.status })
    },
  },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/evaluations\/([^/]+)$/,
    handler: (m) => {
      const ev = store.evaluations.find((e) => e.id === m[1])
      if (!ev) return errorResponse(404, 'Evaluation not found')
      return json(ev)
    },
  },

  // --- usage ---
  {
    method: 'GET',
    pattern: /^\/api\/v1\/usage$/,
    handler: () => {
      const byBucket = new Map<string, { model: string; stage: string; prompt_tokens: number; completion_tokens: number; cost: number; hasUnpriced: boolean }>()
      for (const e of store.usageEvents) {
        const key = `${e.model}::${e.stage}`
        const bucket = byBucket.get(key) ?? { model: e.model, stage: e.stage, prompt_tokens: 0, completion_tokens: 0, cost: 0, hasUnpriced: false }
        bucket.prompt_tokens += e.prompt_tokens
        bucket.completion_tokens += e.completion_tokens
        if (e.cost_usd === null) bucket.hasUnpriced = true
        else bucket.cost += Number(e.cost_usd)
        byBucket.set(key, bucket)
      }
      const items = [...byBucket.values()].map((b) => ({
        model: b.model,
        stage: b.stage,
        prompt_tokens: b.prompt_tokens,
        completion_tokens: b.completion_tokens,
        cost_usd: b.hasUnpriced && b.cost === 0 ? null : b.cost.toFixed(4),
      }))
      const totalPrompt = store.usageEvents.reduce((s, e) => s + e.prompt_tokens, 0)
      const totalCompletion = store.usageEvents.reduce((s, e) => s + e.completion_tokens, 0)
      const totalCost = store.usageEvents.reduce((s, e) => s + (e.cost_usd ? Number(e.cost_usd) : 0), 0)
      const hasUnpriced = store.usageEvents.some((e) => e.cost_usd === null)
      const now = new Date()
      const periodStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1))
      const periodEnd = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1))
      const resp: UsageSummaryResponse = {
        period_start: periodStart.toISOString(),
        period_end: periodEnd.toISOString(),
        prompt_tokens: totalPrompt,
        completion_tokens: totalCompletion,
        cost_usd: totalCost.toFixed(4),
        items,
        has_unpriced_usage: hasUnpriced,
      }
      return json(resp)
    },
  },

  // --- meta ---
  { method: 'GET', pattern: /^\/api\/v1\/tasks$/, handler: () => json(TASK_TYPE_INFO) },
  {
    method: 'GET',
    pattern: /^\/api\/v1\/tasks\/([^/]+)\/example$/,
    handler: (m) => {
      const info = TASK_TYPE_INFO.find((t) => t.task_type === m[1])
      if (!info) return errorResponse(404, 'Unknown task type')
      return json(info.example)
    },
  },
  { method: 'GET', pattern: /^\/api\/v1\/base-models$/, handler: () => json(BASE_MODELS) },
  { method: 'GET', pattern: /^\/api\/v1\/sdg-pipeline$/, handler: () => json(SDG_PIPELINE_MODELS) },

  // --- jobs ---
  {
    method: 'GET',
    pattern: /^\/api\/v1\/jobs\/([^/]+)\/progress$/,
    handler: (m) => {
      const snap = store.jobProgress[m[1]]
      if (!snap) return errorResponse(404, 'No progress snapshot for this job yet')
      return json(snap)
    },
  },

  // --- inference ---
  {
    method: 'GET',
    pattern: /^\/api\/v1\/inference\/models$/,
    handler: () => {
      const tags = store.models.filter((m) => m.ollama_model_tag).map((m) => m.ollama_model_tag as string)
      const baseTags = [...new Set(store.models.map((m) => m.base_ollama_tag).filter((t): t is string => !!t))]
      const resp: ModelDescriptorList = {
        object: 'list',
        data: [...tags, ...baseTags].map((id) => ({
          id,
          object: 'model',
          created: Math.floor(Date.now() / 1000),
          owned_by: 'ollama',
          metadata: null,
        })),
      }
      return json(resp)
    },
  },
  {
    method: 'POST',
    pattern: /^\/api\/v1\/inference\/chat\/completions$/,
    handler: (_m, _url, init) => {
      const body = readJsonBody<{ model: string; messages: { role: string; content: string }[] }>(init)
      const lastUser = [...(body.messages ?? [])].reverse().find((msg) => msg.role === 'user')
      const reply = `[mock:${body.model}] Here's a simulated response to: "${(lastUser?.content ?? '').slice(0, 120)}"`
      const resp: ChatCompletionResponse = {
        id: genId('chatcmpl'),
        object: 'chat.completion',
        created: Math.floor(Date.now() / 1000),
        model: body.model,
        choices: [
          {
            index: 0,
            message: { role: 'assistant', content: reply, name: null, tool_call_id: null },
            finish_reason: 'stop',
          },
        ],
        usage: { prompt_tokens: 64, completion_tokens: 32, total_tokens: 96 },
      }
      return json(resp)
    },
  },
]

/** Drop-in replacement for the global `fetch` used by `src/api/client.ts`. */
export async function mockFetch(path: string, init?: RequestInit): Promise<Response> {
  const url = new URL(path, 'http://mock.local')
  const method = (init?.method ?? 'GET').toUpperCase()
  for (const route of routes) {
    if (route.method !== method) continue
    const match = url.pathname.match(route.pattern)
    if (!match) continue
    // Simulate network latency so loading states are visible.
    await new Promise((resolve) => setTimeout(resolve, 120))
    return route.handler(match, url, init)
  }
  return errorResponse(404, `No mock route for ${method} ${url.pathname}`, 'mock_route_not_found')
}
