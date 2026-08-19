// In dev, Vite proxy forwards /api/v1 → engine. In prod, calls go directly to VITE_ENGINE_HOST.
import { supabase } from "@/integrations/supabase/client";

// Empty in same-origin deployments. Vite proxies relative URLs in local development.
const ENGINE_HOST = ((import.meta.env.VITE_ENGINE_HOST as string | undefined) ?? "").replace(/\/$/, "");

export const ENGINE_BASE = ENGINE_HOST;

// ─── Types ────────────────────────────────────────────────────────────────────

export type EngineTaskType = "classification" | "tool_calling" | "qa";

export interface EngineBaseModel {
  id: string;
  display_name: string;
  family: string;
  params_billions: number;
  context_length: number;
  recommended_max_seq_length: number;
  quantization: string;
  license: string | null;
  notes: string | null;
  ollama_tag: string | null;
}

export interface EngineProject {
  id: string;
  name: string;
  description: string | null;
  task_type: EngineTaskType;
  external_project_id: string | null;
  owner_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface EngineSeedUploadResponse {
  dataset_id: string;
  task_type: EngineTaskType;
  num_samples: number;
  invalid_rows: unknown[];
  format_detection: {
    ran: boolean;
    model_used: string | null;
    field_mapping: Record<string, string>;
    rows_total: number;
    rows_canonicalised: number;
    rows_dropped: number;
    notes: string | null;
  };
  pdf_uri: string | null;
}

export interface EngineSdgResponse {
  job_id: string;
  dataset_id: string;
  websocket_url: string;
  status: string;
}

export interface EngineTrainingAccepted {
  training_id: string;
  job_id: string;
  mlflow_run_id: string | null;
  mlflow_url: string | null;
  status: string;
  websocket_url: string;
}

export interface EngineTrainingStatus {
  id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  project_id: string;
  dataset_id: string;
  celery_task_id: string | null;
  base_model: string;
  training_name: string | null;
  mlflow_run_id: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface EngineDataset {
  id: string;
  project_id: string;
  parent_dataset_id: string | null;
  name: string;
  task_type: EngineTaskType;
  source: "seed" | "sdg" | "merged";
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  error_message: string | null;
  num_samples: number;
  storage_uri: string | null;
  size_bytes: number | null;
  generation_metadata: Record<string, unknown> | null;
  celery_task_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface EnginePage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface EngineLossPoint {
  step: number;
  value: number;
  timestamp_ms: number;
}

export interface EngineLossHistory {
  training_id: string;
  mlflow_run_id: string | null;
  train_loss: EngineLossPoint[];
  eval_loss: EngineLossPoint[];
}

export interface EngineInferenceMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface EngineChatRequest {
  model: string;
  messages: EngineInferenceMessage[];
  max_tokens?: number;
  temperature?: number;
  top_p?: number;
  stream?: false;
}

export interface EngineChatResponse {
  id: string;
  object: "chat.completion";
  created: number;
  model: string;
  choices: Array<{
    index: number;
    message: { role: "assistant"; content: string; name: null; tool_call_id: null };
    finish_reason: string;
  }>;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

export interface EngineInferenceModel {
  id: string;
  object: "model";
  created: number;
  owned_by: string;
}

export interface EngineModelArtifact {
  id: string;
  training_job_id: string;
  name: string;
  base_model: string;
  mlflow_run_id: string | null;
  lora_adapter_uri: string | null;
  gguf_uri: string | null;
  safetensors_uri: string | null;
  size_mb: number | null;
  ollama_model_tag: string | null;
  base_ollama_tag: string | null;
  export_error_message: string | null;
  export_status: "pending" | "running" | "completed" | "failed" | "cancelled" | null;
  export_celery_task_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface EngineModelExportResponse {
  artifact_id: string;
  format: "gguf" | "safetensors";
  job_id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  websocket_url: string;
}

export interface EngineTrainingProgressEvent {
  type: "training_progress";
  job_id: string;
  timestamp: string;
  epoch: number;
  epochs_total: number;
  step: number;
  steps_total: number;
  train_loss: number | null;
  eval_loss: number | null;
  learning_rate: number | null;
  samples_per_second: number | null;
  gpu_memory_mb: number | null;
}

export interface EngineJobCompletedEvent {
  type: "completed";
  job_id: string;
  timestamp: string;
  result: Record<string, unknown>;
  mlflow_run_id: string | null;
  dataset_id: string | null;
  model_artifact_id: string | null;
}

export interface EngineJobFailedEvent {
  type: "failed";
  job_id: string;
  timestamp: string;
  error: string;
  error_type: string | null;
  traceback?: string | null;
}

export type EngineJobProgress =
  | EngineTrainingProgressEvent
  | EngineJobCompletedEvent
  | EngineJobFailedEvent
  | ({
      type: "sdg_progress" | "hpo_progress" | "export_progress" | "evaluation_progress";
      job_id: string;
      timestamp: string;
    } & Record<string, unknown>);

export interface EngineDatasetPreview {
  dataset_id: string;
  rows: Record<string, unknown>[];
  total_rows: number;
}

export interface EngineDatasetDownloadUrl {
  download_url: string;
  expires_at: string;
}

export interface EngineTrainingMetrics {
  training_id: string;
  metrics: Record<string, { values: number[]; steps: number[]; timestamps: number[] }>;
  hpo_children: Array<{ run_id: string; params: Record<string, unknown>; metrics: Record<string, number> }> | null;
}

export interface EngineModelDownloadUrl {
  download_url: string;
  format: string;
  expires_at: string;
}

export interface EngineAuditEvent {
  id: string;
  project_id: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface EngineUsageEvent {
  id: string;
  project_id: string;
  model: string;
  stage: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  created_at: string;
}

export interface EngineUsageSummary {
  total_cost_usd: number;
  by_model: Array<{ model: string; stage: string; cost_usd: number; prompt_tokens: number; completion_tokens: number }>;
  since: string;
}

export interface EngineEvaluationCreate {
  model_artifact_id: string;
  dataset_id: string;
  eval_name?: string;
  config?: Record<string, unknown>;
}

export interface EngineEvaluationAccepted {
  evaluation_id: string;
  job_id: string;
  status: string;
  websocket_url: string;
}

export interface EngineEvaluation {
  id: string;
  model_artifact_id: string;
  dataset_id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  eval_name: string | null;
  metrics: Record<string, number> | null;
  error_message: string | null;
  celery_task_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface EngineEvaluationComparison {
  evaluations: EngineEvaluation[];
  comparison: Record<string, Record<string, number>>;
}

export interface EngineTaskTypeInfo {
  task_type: EngineTaskType;
  display_name: string;
  description: string;
  sample_schema: Record<string, unknown>;
  example: Record<string, unknown>;
}

export interface EngineSdgPipelineConfig {
  generator: string;
  judge: string;
  diversity_rules: string;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────


interface EngineErrorBody {
  detail?: string;
  code?: string | null;
  extra?: Record<string, unknown> | null;
}

export class EngineApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly requestId: string | null;
  readonly extra: Record<string, unknown> | null;

  constructor(status: number, body: EngineErrorBody, requestId: string | null) {
    super(body.detail || `Engine API request failed (${status})`);
    this.name = "EngineApiError";
    this.status = status;
    this.code = body.code ?? null;
    this.requestId = requestId;
    this.extra = body.extra ?? null;
  }
}

async function authHeaders(): Promise<Record<string, string>> {
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) {
    throw new EngineApiError(401, {
      detail: "Please sign in before using the fine-tuning engine.",
      code: "missing_session",
    }, null);
  }
  return { Authorization: `Bearer ${session.access_token}` };
}

async function throwEngineError(res: Response): Promise<never> {
  const raw = await res.text().catch(() => "");
  let body: EngineErrorBody = { detail: raw || `Engine API request failed (${res.status})` };
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as EngineErrorBody;
      body = { ...parsed, detail: parsed.detail || body.detail };
    } catch {
      // Non-JSON proxy errors still keep their response text.
    }
  }
  throw new EngineApiError(res.status, body, res.headers.get("X-Request-ID"));
}

async function engineFetch(path: string, init?: RequestInit, json = true): Promise<Response> {
  const auth = await authHeaders();
  const res = await fetch(`${ENGINE_HOST}/api/v1${path}`, {
    ...init,
    headers: {
      ...(json ? { "Content-Type": "application/json" } : {}),
      ...auth,
      ...init?.headers,
    },
  });
  if (!res.ok) await throwEngineError(res);
  return res;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await engineFetch(path, init);
  return res.json() as Promise<T>;
}

export async function engineListBaseModels(): Promise<EngineBaseModel[]> {
  return apiFetch<EngineBaseModel[]>("/base-models");
}

// ─── Node 1 — Create Project ──────────────────────────────────────────────────

export async function engineCreateProject(
  name: string,
  description: string,
  task_type: EngineTaskType,
  externalProjectId: string,
): Promise<EngineProject> {
  return apiFetch<EngineProject>("/projects", {
    method: "POST",
    body: JSON.stringify({ name, description, task_type, external_project_id: externalProjectId }),
  });
}

export async function engineGetProject(id: string): Promise<EngineProject> {
  return apiFetch<EngineProject>(`/projects/${id}`);
}

export async function engineFindProjectByExternalId(externalProjectId: string): Promise<EngineProject | null> {
  const query = new URLSearchParams({ external_project_id: externalProjectId, limit: "1" });
  const page = await apiFetch<EnginePage<EngineProject>>(`/projects?${query.toString()}`);
  return page.items[0] ?? null;
}

export interface EngineProjectUpdate {
  name?: string;
  description?: string;
}

export async function engineUpdateProject(projectId: string, body: EngineProjectUpdate): Promise<EngineProject> {
  return apiFetch<EngineProject>(`/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function engineDeleteProject(projectId: string): Promise<void> {
  await engineFetch(`/projects/${projectId}`, { method: "DELETE" });
}


// ─── Node 3a — Upload Seed Data ───────────────────────────────────────────────

export async function engineUploadSeed(
  file: File,
  projectId: string,
  taskType: EngineTaskType,
  name?: string,
): Promise<EngineSeedUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("project_id", projectId);
  form.append("task_type", taskType);
  if (name) form.append("name", name);

  const res = await engineFetch("/datasets/upload-seed", {
    method: "POST",
    body: form,
    // No Content-Type header — browser sets multipart boundary automatically
  }, false);
  return res.json() as Promise<EngineSeedUploadResponse>;
}

// ─── Node 4 — SDG + Holdout Split ────────────────────────────────────────────

export async function engineGenerateDataset(
  projectId: string,
  taskType: EngineTaskType,
  taskDescription: string,
  seedDatasetId: string,
  numSamples = 200,
  holdoutSize = 50,
  idempotencyKey?: string,
): Promise<EngineSdgResponse> {
  return apiFetch<EngineSdgResponse>("/datasets/generate", {
    method: "POST",
    headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    body: JSON.stringify({
      sdg_mode: "with_seed",
      project_id: projectId,
      task_type: taskType,
      task_description: taskDescription,
      seed_dataset_id: seedDatasetId,
      num_samples: numSamples,
      holdout_size: holdoutSize,
    }),
  });
}

// ─── Node 5 — Verify Dataset ──────────────────────────────────────────────────

export async function engineGetDataset(id: string): Promise<EngineDataset> {
  return apiFetch<EngineDataset>(`/datasets/${id}`);
}

export async function engineListDatasets(projectId: string): Promise<EnginePage<EngineDataset>> {
  const query = new URLSearchParams({ project_id: projectId, limit: "200" });
  return apiFetch<EnginePage<EngineDataset>>(`/datasets?${query.toString()}`);
}

// ─── Node 6 — Training ────────────────────────────────────────────────────────

export interface ManualTrainingConfig {
  num_train_epochs: number;
  per_device_train_batch_size?: number;
  gradient_accumulation_steps?: number;
  learning_rate: number;
  max_seq_length?: number;
  optim?: string;
  packing?: boolean;
  lora?: {
    r: number;
    alpha: number;
    dropout: number;
    target_modules: string[];
  };
}

export async function engineStartTraining(
  projectId: string,
  datasetId: string,
  baseModel: string,
  trainingName: string,
  config: ManualTrainingConfig,
  idempotencyKey?: string,
): Promise<EngineTrainingAccepted> {
  return apiFetch<EngineTrainingAccepted>("/trainings", {
    method: "POST",
    headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    body: JSON.stringify({
      mode: "manual",
      project_id: projectId,
      dataset_id: datasetId,
      base_model: baseModel,
      training_name: trainingName,
      manual_config: config,
    }),
  });
}

export async function engineGetTraining(trainingId: string): Promise<EngineTrainingStatus> {
  return apiFetch<EngineTrainingStatus>(`/trainings/${trainingId}`);
}

export async function engineListTrainings(projectId: string): Promise<EnginePage<EngineTrainingStatus>> {
  const query = new URLSearchParams({ project_id: projectId, limit: "200" });
  return apiFetch<EnginePage<EngineTrainingStatus>>(`/trainings?${query.toString()}`);
}

export async function engineCancelTraining(trainingId: string): Promise<void> {
  await engineFetch(`/trainings/${trainingId}`, { method: "DELETE" });
}

// ─── Node 6 — Model Artifacts ─────────────────────────────────────────────────

export async function engineGetModelArtifacts(trainingId: string): Promise<{ items: EngineModelArtifact[] }> {
  const query = new URLSearchParams({ training_job_id: trainingId, limit: "200" });
  return apiFetch(`/models?${query.toString()}`);
}

export async function engineGetModelArtifact(modelId: string): Promise<EngineModelArtifact> {
  return apiFetch<EngineModelArtifact>(`/models/${modelId}`);
}

export async function engineExportModel(modelId: string): Promise<EngineModelExportResponse> {
  return apiFetch<EngineModelExportResponse>(`/models/${modelId}/export`, {
    method: "POST",
    body: JSON.stringify({ format: "gguf", quantization: "q4_k_m" }),
  });
}

// ─── Node 11 — Loss History ───────────────────────────────────────────────────

export async function engineGetLossHistory(trainingId: string): Promise<EngineLossHistory> {
  return apiFetch<EngineLossHistory>(`/trainings/${trainingId}/loss-history`);
}

export async function engineGetJobProgress(jobId: string): Promise<EngineJobProgress> {
  return apiFetch<EngineJobProgress>(`/jobs/${encodeURIComponent(jobId)}/progress`);
}

// ─── Node 8 — Inference ───────────────────────────────────────────────────────

export async function engineListInferenceModels(): Promise<{ object: "list"; data: EngineInferenceModel[] }> {
  return apiFetch<{ object: "list"; data: EngineInferenceModel[] }>("/inference/models");
}

export async function engineChatCompletion(req: EngineChatRequest): Promise<EngineChatResponse> {
  return apiFetch<EngineChatResponse>("/inference/chat/completions", {
    method: "POST",
    body: JSON.stringify({ ...req, stream: false }),
  });
}

// ─── Additional Dataset Operations ───────────────────────────────────────────

export async function engineCancelDataset(datasetId: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/datasets/${datasetId}/cancel`, { method: "POST" });
}

export async function engineDeleteDataset(datasetId: string): Promise<void> {
  await engineFetch(`/datasets/${datasetId}`, { method: "DELETE" });
}

export async function enginePreviewDataset(datasetId: string, limit = 20): Promise<EngineDatasetPreview> {
  return apiFetch<EngineDatasetPreview>(`/datasets/${datasetId}/preview?limit=${limit}`);
}

export async function engineGetDatasetDownloadUrl(datasetId: string): Promise<EngineDatasetDownloadUrl> {
  return apiFetch<EngineDatasetDownloadUrl>(`/datasets/${datasetId}/download-url`);
}

// ─── Additional Training & Artifact Operations ─────────────────────────────

export async function engineGetMlflowUrl(trainingId: string): Promise<{ mlflow_url: string | null }> {
  return apiFetch<{ mlflow_url: string | null }>(`/trainings/${trainingId}/mlflow-url`);
}

export async function engineGetTrainingMetrics(trainingId: string): Promise<EngineTrainingMetrics> {
  return apiFetch<EngineTrainingMetrics>(`/trainings/${trainingId}/metrics`);
}

export async function engineCancelModelExport(modelId: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/models/${modelId}/export/cancel`, { method: "POST" });
}

export async function engineGetModelDownloadUrl(modelId: string, fmt = "gguf"): Promise<EngineModelDownloadUrl> {
  return apiFetch<EngineModelDownloadUrl>(`/models/${modelId}/download-url?format=${fmt}`);
}

// ─── Project Activity & Usage ───────────────────────────────────────────────

export async function engineListProjectActivity(
  projectId: string,
  limit = 50,
  offset = 0
): Promise<EnginePage<EngineAuditEvent>> {
  const query = new URLSearchParams({ limit: limit.toString(), offset: offset.toString() });
  return apiFetch<EnginePage<EngineAuditEvent>>(`/projects/${projectId}/activity?${query.toString()}`);
}

export async function engineListProjectUsage(
  projectId: string,
  limit = 50,
  offset = 0
): Promise<EnginePage<EngineUsageEvent>> {
  const query = new URLSearchParams({ limit: limit.toString(), offset: offset.toString() });
  return apiFetch<EnginePage<EngineUsageEvent>>(`/projects/${projectId}/usage?${query.toString()}`);
}

export async function engineGetUsageSummary(): Promise<EngineUsageSummary> {
  return apiFetch<EngineUsageSummary>("/usage");
}

// ─── Evaluations ────────────────────────────────────────────────────────────

export async function engineStartEvaluation(body: EngineEvaluationCreate): Promise<EngineEvaluationAccepted> {
  return apiFetch<EngineEvaluationAccepted>("/evaluations", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function engineListEvaluations(filter?: {
  modelArtifactId?: string;
  datasetId?: string;
  limit?: number;
  offset?: number;
}): Promise<EnginePage<EngineEvaluation>> {
  const query = new URLSearchParams();
  if (filter?.modelArtifactId) query.set("model_artifact_id", filter.modelArtifactId);
  if (filter?.datasetId) query.set("dataset_id", filter.datasetId);
  if (filter?.limit) query.set("limit", filter.limit.toString());
  if (filter?.offset) query.set("offset", filter.offset.toString());
  return apiFetch<EnginePage<EngineEvaluation>>(`/evaluations?${query.toString()}`);
}

export async function engineGetEvaluation(evaluationId: string): Promise<EngineEvaluation> {
  return apiFetch<EngineEvaluation>(`/evaluations/${evaluationId}`);
}

export async function engineCancelEvaluation(evaluationId: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/evaluations/${evaluationId}/cancel`, { method: "POST" });
}

export async function engineCompareEvaluations(evaluationIds: string[]): Promise<EngineEvaluationComparison> {
  return apiFetch<EngineEvaluationComparison>("/evaluations/compare", {
    method: "POST",
    body: JSON.stringify({ evaluation_ids: evaluationIds }),
  });
}

// ─── Tasks & Metadata ────────────────────────────────────────────────────────

export async function engineListTaskTypes(): Promise<EngineTaskTypeInfo[]> {
  return apiFetch<EngineTaskTypeInfo[]>("/tasks");
}

export async function engineGetTaskExample(taskType: EngineTaskType): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(`/tasks/${taskType}/example`);
}

export async function engineGetSdgPipelineConfig(): Promise<EngineSdgPipelineConfig> {
  return apiFetch<EngineSdgPipelineConfig>("/sdg-pipeline");
}

export interface EngineBaseModelInfo {
  id: string;
  display_name: string;
  family: string;
  params_billions: number;
  context_length: number;
  recommended_max_seq_length: number;
  license: string;
  notes?: string;
  ollama_tag?: string;
}

export async function engineListBaseModels(): Promise<EngineBaseModelInfo[]> {
  return apiFetch<EngineBaseModelInfo[]>("/base-models");
}


// ─── Health check ─────────────────────────────────────────────────────────────

export async function engineHealthCheck(): Promise<boolean> {
  try {
    const res = await fetch(`${ENGINE_HOST}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

