/**
 * TypeScript mirrors of the backend Pydantic schemas (api/schemas/*.py).
 * Keep in sync with the backend — these string values are the public contract.
 */

// --- Enums (api/schemas/enums.py) ------------------------------------------

export type TaskType = 'classification' | 'tool_calling' | 'qa'
export type TrainingMode = 'manual' | 'hpo'
export type SDGMode = 'with_seed' | 'description_only'
export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
export type DatasetSource = 'seed' | 'sdg' | 'merged'
export type ArtifactFormat = 'lora' | 'gguf' | 'safetensors'

export const TASK_TYPES: TaskType[] = ['classification', 'tool_calling', 'qa']
export const TERMINAL_STATUSES: JobStatus[] = ['completed', 'failed', 'cancelled']

export function isTerminalStatus(status: JobStatus): boolean {
  return TERMINAL_STATUSES.includes(status)
}

// --- Generic responses (api/schemas/responses.py) ---------------------------

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface ErrorBody {
  detail: string
  code?: string | null
  extra?: Record<string, unknown> | null
}

// --- Projects (api/schemas/projects.py) -------------------------------------

export interface ProjectCreate {
  name: string
  description?: string | null
  task_type: TaskType
}

export interface ProjectUpdate {
  name?: string
  description?: string | null
}

export interface Project {
  id: string
  name: string
  description: string | null
  task_type: TaskType
  /** Opaque ID from an external system (e.g. a Supabase project row) this
   *  project is 1:1 mapped to. Always null for projects created from this
   *  embedded frontend (it has no external system to map) — present so a
   *  project created via the API with a non-null value still round-trips. */
  external_project_id: string | null
  created_at: string
  updated_at: string
}

// --- Datasets (api/schemas/datasets.py) -------------------------------------

export interface Dataset {
  id: string
  project_id: string
  name: string
  task_type: TaskType
  source: DatasetSource
  status: JobStatus
  error_message: string | null
  num_samples: number
  storage_uri: string | null
  size_bytes: number | null
  generation_metadata: Record<string, unknown> | null
  parent_dataset_id?: string | null
  created_at: string
  updated_at: string
}

export interface DatasetPreview {
  dataset_id: string
  task_type: TaskType
  samples: Record<string, unknown>[]
  total: number
}

// --- Data formats (api/schemas/data_formats.py) ------------------------------

export interface ClassificationSample {
  text: string
  label: string
}

/** `answer` is a JSON string encoding {name, parameters}. */
export interface ToolCallingSample {
  question: string
  answer: string
}

export interface QASample {
  question: string
  answer: string
}

export interface ToolParameterSpec {
  type: 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object'
  description?: string | null
  required?: boolean
  [extra: string]: unknown
}

export interface ToolDefinition {
  name: string
  description: string
  parameters: Record<string, ToolParameterSpec>
}

// --- SDG (api/schemas/sdg.py) ------------------------------------------------

interface SDGRequestBase {
  project_id: string
  task_type: TaskType
  task_description: string
  num_samples: number
  /** Extra rows persisted as a separate holdout child dataset; 0 disables. */
  holdout_size?: number
  temperature?: number
  dataset_name?: string | null
}

export interface SDGRequestWithSeed extends SDGRequestBase {
  sdg_mode: 'with_seed'
  /** A previously-uploaded dataset with source='seed' and matching task_type. */
  seed_dataset_id: string
}

export interface SDGRequestDescriptionOnly extends SDGRequestBase {
  sdg_mode: 'description_only'
  classification_config?: { labels: string[] } | null
  tool_calling_config?: { tool_definitions: ToolDefinition[] } | null
}

export type SDGRequest = SDGRequestWithSeed | SDGRequestDescriptionOnly

export interface SDGJobAccepted {
  job_id: string
  dataset_id: string
  status: JobStatus
  websocket_url: string
}

/** Audit trail of one Format Detection pass (api/schemas/upload.py). */
export interface FormatDetectionReport {
  ran: boolean
  model_used: string | null
  field_mapping: Record<string, string>
  rows_total: number
  rows_canonicalised: number
  rows_dropped: number
  notes: string | null
}

export interface SeedUploadResponse {
  dataset_id: string
  task_type: TaskType
  num_samples: number
  invalid_rows: number[]
  format_detection: FormatDetectionReport
  pdf_uri: string | null
}

// --- Training (api/schemas/training.py) --------------------------------------

export interface LoRAConfig {
  r?: number
  alpha?: number
  dropout?: number
  target_modules?: string[]
}

export type LrSchedulerType = 'linear' | 'cosine' | 'constant'

export type OptimType = 'adamw_8bit' | 'paged_adamw_8bit' | 'adamw_torch'

export interface ManualTrainingConfig {
  learning_rate?: number
  num_train_epochs?: number
  per_device_train_batch_size?: number
  gradient_accumulation_steps?: number
  warmup_ratio?: number
  weight_decay?: number
  lr_scheduler_type?: LrSchedulerType
  max_seq_length?: number
  seed?: number
  optim?: OptimType
  packing?: boolean
  neftune_noise_alpha?: number | null
  lora?: LoRAConfig
}

export interface HPOFloatRange {
  type: 'float'
  low: number
  high: number
  log?: boolean
}

export interface HPOIntRange {
  type: 'int'
  low: number
  high: number
  step?: number
  log?: boolean
}

export interface HPOCategorical {
  type: 'categorical'
  choices: (string | number | boolean)[]
}

export type HPOParam = HPOFloatRange | HPOIntRange | HPOCategorical

export interface HPOSearchSpace {
  learning_rate?: HPOFloatRange | null
  num_train_epochs?: HPOIntRange | null
  per_device_train_batch_size?: HPOCategorical | null
  gradient_accumulation_steps?: HPOCategorical | null
  warmup_ratio?: HPOFloatRange | null
  weight_decay?: HPOFloatRange | null
  lr_scheduler_type?: HPOCategorical | null
  lora_r?: HPOCategorical | null
  lora_alpha?: HPOCategorical | null
  lora_dropout?: HPOFloatRange | null
}

export interface HPOConfig {
  n_trials?: number
  objective_metric?: string
  direction?: 'minimize' | 'maximize'
  timeout_seconds?: number | null
  sampler?: 'tpe' | 'random'
  pruner?: 'median' | 'none'
  search_space: HPOSearchSpace
  fixed_config?: ManualTrainingConfig
}

interface TrainingRequestBase {
  project_id: string
  dataset_id: string
  base_model?: string | null
  training_name?: string | null
}

export interface ManualTrainingRequest extends TrainingRequestBase {
  mode: 'manual'
  manual_config?: ManualTrainingConfig
}

export interface HPOTrainingRequest extends TrainingRequestBase {
  mode: 'hpo'
  hpo_config: HPOConfig
}

export type TrainingRequest = ManualTrainingRequest | HPOTrainingRequest

export interface TrainingJobAccepted {
  job_id: string
  training_id: string
  mlflow_run_id: string | null
  mlflow_url: string | null
  status: JobStatus
  websocket_url: string
}

// --- Trainings read views (api/schemas/trainings.py) -------------------------

export interface Training {
  id: string
  project_id: string
  dataset_id: string
  mode: TrainingMode
  status: JobStatus
  celery_task_id: string | null
  base_model: string
  training_name: string | null
  mlflow_experiment_id: string | null
  mlflow_run_id: string | null
  config_json: Record<string, unknown>
  best_metric_value: number | null
  best_params_json: Record<string, unknown> | null
  error_message: string | null
  started_at: string | null
  ended_at: string | null
  created_at: string
  updated_at: string
}

export interface MetricPoint {
  step: number
  value: number
  timestamp_ms: number
}

/** Body of GET /trainings/{id}/loss-history — chart backfill (WS has no replay). */
export interface TrainingLossHistory {
  training_id: string
  mlflow_run_id: string | null
  train_loss: MetricPoint[]
  eval_loss: MetricPoint[]
}

export interface MlflowUrlResponse {
  training_id: string
  mlflow_run_id: string | null
  mlflow_url: string | null
}

/** Compact view of one HPO trial run (no full series — just final + params). */
export interface HpoChildSummary {
  run_id: string
  name: string
  final_eval_loss: number | null
  params: Record<string, string>
}

/** Full metric history of a training run, plus HPO child summary if applicable.
 *  `hpo_children` is null for manual mode and a list for HPO mode. */
export interface TrainingMetrics {
  training_id: string
  mlflow_run_id: string | null
  metrics: Record<string, MetricPoint[]>
  hpo_children: HpoChildSummary[] | null
}

// --- Model artifacts (api/schemas/artifacts.py) ------------------------------

export interface ModelArtifact {
  id: string
  training_job_id: string
  name: string
  base_model: string
  mlflow_run_id: string | null
  lora_adapter_uri: string | null
  gguf_uri: string | null
  safetensors_uri: string | null
  size_mb: number | null
  ollama_model_tag: string | null
  export_error_message: string | null
  /** In-flight export job state and its celery task id — drive the
   *  export-in-flight/cancel affordance. Null when no export is running. */
  export_status: JobStatus | null
  export_celery_task_id: string | null
  created_at: string
  updated_at: string
  /** Ollama-Hub equivalent of `base_model`, auto-pulled by the worker after
   *  export so the playground can A/B compare fine-tuned vs base. Null when
   *  this base has no known Ollama-Hub mapping — hide the "compare with
   *  base" affordance in that case. Server-computed (not stored). */
  base_ollama_tag: string | null
}

export interface ModelExportRequest {
  format: ArtifactFormat
  quantization?: string | null
}

export interface ModelExportAccepted {
  artifact_id: string
  format: ArtifactFormat
  job_id: string
  status: JobStatus
  websocket_url: string
}

// --- Evaluations (api/schemas/evaluations.py) --------------------------------

export interface EvaluationCreate {
  model_artifact_id: string
  dataset_id: string
  use_llm_judge?: boolean
  judge_model?: string | null
}

export interface Evaluation {
  id: string
  model_artifact_id: string
  dataset_id: string
  celery_task_id: string | null
  status: JobStatus
  metrics_json: Record<string, unknown> | null
  llm_judge_score: number | null
  llm_judge_model: string | null
  error_message: string | null
  started_at: string | null
  ended_at: string | null
  created_at: string
  updated_at: string
}

export interface EvaluationCompareRequest {
  evaluation_ids: string[]
}

export interface EvaluationCompareResponse {
  evaluation_ids: string[]
  /** metric_name → {evaluation_id → value} */
  metrics: Record<string, Record<string, number | null>>
  judge_scores: Record<string, number | null>
}

export interface EvaluationAccepted {
  evaluation_id: string
  job_id: string
  status: JobStatus
  websocket_url: string
}

// --- Inference (api/schemas/inference.py) ------------------------------------

export type ChatRole = 'system' | 'user' | 'assistant' | 'tool'

export interface ChatMessage {
  role: ChatRole
  content: string
  name?: string | null
  tool_call_id?: string | null
}

export interface ChatCompletionRequest {
  model: string
  messages: ChatMessage[]
  temperature?: number
  top_p?: number
  max_tokens?: number | null
  stream?: boolean
  stop?: string[] | null
  seed?: number | null
}

export interface ChatCompletionUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface ChatCompletionChoice {
  index: number
  message: ChatMessage
  finish_reason: 'stop' | 'length' | 'tool_calls' | 'content_filter' | null
}

export interface ChatCompletionResponse {
  id: string
  object: 'chat.completion'
  created: number
  model: string
  choices: ChatCompletionChoice[]
  usage: ChatCompletionUsage
}

export interface ModelDescriptor {
  id: string
  object: 'model'
  created: number
  owned_by: string
  metadata: Record<string, unknown> | null
}

export interface ModelDescriptorList {
  object: 'list'
  data: ModelDescriptor[]
}

// --- Metadata (api/schemas/tasks_meta.py) ------------------------------------

export interface TaskTypeInfo {
  task_type: TaskType
  display_name: string
  description: string
  sample_schema: Record<string, unknown>
  example: Record<string, unknown>
  sdg_modes_supported: string[]
}

export interface BaseModelInfo {
  id: string
  display_name: string
  family: string
  params_billions: number
  context_length: number
  recommended_max_seq_length: number
  quantization: string
  license: string | null
  notes: string | null
}

export interface SdgPipelineModels {
  generator: string
  judge: string
  diversity_rules: string
}

// --- WebSocket progress messages (api/schemas/progress.py) -------------------

export type SDGPhase =
  | 'format_detection'
  | 'meta_prompting'
  | 'generating'
  | 'validating'
  | 'judging'
  | 'dedup'
  | 'deduplicating'
  | 'persisting'

interface WSMessageBase {
  job_id: string
  timestamp: string
}

export interface SDGProgressMsg extends WSMessageBase {
  type: 'sdg_progress'
  phase: SDGPhase
  samples_generated: number
  samples_target: number
  samples_valid: number
  samples_rejected: number
  duplicates_removed: number
  current_loop?: number | null
  judge_rejected?: number | null
  judge_parse_failures?: number | null
  dedup_rejected?: number | null
}

export interface TrainingProgressMsg extends WSMessageBase {
  type: 'training_progress'
  epoch: number
  epochs_total: number
  step: number
  steps_total: number
  train_loss: number | null
  eval_loss: number | null
  learning_rate: number | null
  samples_per_second: number | null
  gpu_memory_mb: number | null
}

export interface HPOProgressMsg extends WSMessageBase {
  type: 'hpo_progress'
  trial_number: number
  trials_total: number
  current_params: Record<string, string | number | boolean> | null
  best_value: number | null
  best_params: Record<string, string | number | boolean> | null
  last_trial_value: number | null
  last_trial_pruned: boolean
  inner_progress: TrainingProgressMsg | null
}

export interface JobCompletedMsg extends WSMessageBase {
  type: 'completed'
  result: Record<string, unknown>
  mlflow_run_id: string | null
  dataset_id: string | null
  model_artifact_id: string | null
}

export interface JobFailedMsg extends WSMessageBase {
  type: 'failed'
  error: string
  error_type: string | null
  traceback: string | null
}

export type WSMessage =
  | SDGProgressMsg
  | TrainingProgressMsg
  | HPOProgressMsg
  | JobCompletedMsg
  | JobFailedMsg

// --- Usage (api/schemas/usage.py) ---------------------------------------------

/** Wire shape for a single usage row. */
export interface UsageEvent {
  id: string
  created_at: string
  actor_id: string | null
  project_id: string | null
  job_id: string | null
  provider: string
  model: string
  stage: string
  prompt_tokens: number
  completion_tokens: number
  /** Pydantic Decimal serializes as a JSON string; null when unpriced. */
  cost_usd: string | null
  outcome: string
}

/** One (model, stage) bucket within a `UsageSummaryResponse`. */
export interface UsageRollupItem {
  model: string
  stage: string
  prompt_tokens: number
  completion_tokens: number
  cost_usd: string | null
}

/** Aggregate usage/cost over a date range, broken down by model+stage. */
export interface UsageSummaryResponse {
  period_start: string
  period_end: string
  prompt_tokens: number
  completion_tokens: number
  cost_usd: string | null
  items: UsageRollupItem[]
  /** True when at least one row in this period had cost_usd IS NULL, so a
   *  consumer knows the total is a floor, not a total. */
  has_unpriced_usage: boolean
}

// --- Audit (api/schemas/audit.py) ---------------------------------------------

export interface AuditEvent {
  id: string
  created_at: string
  actor_id: string | null
  project_id: string | null
  action: string
  resource_type: string
  resource_id: string | null
  outcome: string
  request_id: string | null
  metadata?: Record<string, unknown> | null
}

// --- Download links (api/schemas/download_links.py) ---------------------------

/** Body of `GET /api/v1/datasets/{id}/download-url`. */
export interface DatasetDownloadUrl {
  url: string
  filename: string
  content_type: string
  expires_at: string
  /** Seconds from mint time until `url` stops working. */
  expires_in: number
}

/** One object inside a `ModelDownloadUrl.files` listing. */
export interface ModelDownloadFile {
  key: string
  name: string
  size_bytes: number
  url: string
}

/** Body of `GET /api/v1/models/{id}/download-url`. */
export interface ModelDownloadUrl {
  format: ArtifactFormat
  files: ModelDownloadFile[]
  expires_at: string
  expires_in: number
  truncated?: boolean
}
