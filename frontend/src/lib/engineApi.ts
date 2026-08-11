// In dev, Vite proxy forwards /api/v1 → engine. In prod, calls go directly to VITE_ENGINE_HOST.
const ENGINE_HOST = (import.meta.env.VITE_ENGINE_HOST as string | undefined) ?? "";

export const ENGINE_BASE = ENGINE_HOST;

// ─── Types ────────────────────────────────────────────────────────────────────

export type EngineTaskType = "classification" | "tool_calling" | "qa";

export interface EngineProject {
  id: string;
  name: string;
  description: string;
  task_type: EngineTaskType;
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
  base_model: string;
  training_name: string;
  mlflow_run_id: string | null;
  created_at: string;
  updated_at: string;
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
  ollama_model_tag: string | null;
  base_ollama_tag: string | null;
  status: string;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ENGINE_HOST}/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Engine API ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// ─── Node 1 — Create Project ──────────────────────────────────────────────────

export async function engineCreateProject(
  name: string,
  description: string,
  task_type: EngineTaskType,
): Promise<EngineProject> {
  return apiFetch<EngineProject>("/projects", {
    method: "POST",
    body: JSON.stringify({ name, description, task_type }),
  });
}

export async function engineGetProject(id: string): Promise<EngineProject> {
  return apiFetch<EngineProject>(`/projects/${id}`);
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

  const res = await fetch(`${ENGINE_HOST}/api/v1/datasets/upload-seed`, {
    method: "POST",
    body: form,
    // No Content-Type header — browser sets multipart boundary automatically
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Engine API ${res.status}: ${body}`);
  }
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
): Promise<EngineSdgResponse> {
  return apiFetch<EngineSdgResponse>("/datasets/generate", {
    method: "POST",
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

export async function engineGetDataset(id: string): Promise<{ id: string; status: string; num_samples: number; parent_id: string | null }> {
  return apiFetch(`/datasets/${id}`);
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
): Promise<EngineTrainingAccepted> {
  return apiFetch<EngineTrainingAccepted>("/trainings", {
    method: "POST",
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

export async function engineCancelTraining(trainingId: string): Promise<void> {
  await fetch(`${ENGINE_HOST}/api/v1/trainings/${trainingId}`, { method: "DELETE" });
}

// ─── Node 6 — Model Artifacts ─────────────────────────────────────────────────

export async function engineGetModelArtifacts(trainingId: string): Promise<{ items: EngineModelArtifact[] }> {
  return apiFetch(`/models?training_job_id=${trainingId}`);
}

// ─── Node 11 — Loss History ───────────────────────────────────────────────────

export async function engineGetLossHistory(trainingId: string): Promise<EngineLossHistory> {
  return apiFetch<EngineLossHistory>(`/trainings/${trainingId}/loss-history`);
}

// ─── Node 8 — Inference ───────────────────────────────────────────────────────

export async function engineListInferenceModels(): Promise<{ object: "list"; data: EngineInferenceModel[] }> {
  const res = await fetch(`${ENGINE_HOST}/api/v1/inference/models`);
  if (!res.ok) throw new Error(`Engine API ${res.status}`);
  return res.json();
}

export async function engineChatCompletion(req: EngineChatRequest): Promise<EngineChatResponse> {
  const res = await fetch(`${ENGINE_HOST}/api/v1/inference/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...req, stream: false }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Engine API ${res.status}: ${body}`);
  }
  return res.json() as Promise<EngineChatResponse>;
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
