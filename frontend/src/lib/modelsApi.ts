import { supabase } from "@/integrations/supabase/client";
import type { TrainedModel, BaseModel, TaskType } from "@/types";

interface ModelRow {
  id: string;
  user_id: string;
  project_id: string | null;
  name: string;
  base_model: string;
  task_type: string;
  accuracy: number;
  f1_score: number;
  precision: number;
  recall: number;
  latency_ms: number;
  file_size: string;
  format: string;
  status: string;
  created_at: string;
}

export interface TrainedModelExt extends TrainedModel {
  projectId: string;
  precision: number;
  recall: number;
  latencyMs: number;
}

function toModel(r: ModelRow): TrainedModelExt {
  return {
    id: r.id,
    name: r.name,
    projectId: r.project_id ?? "",
    baseModel: r.base_model as BaseModel,
    taskType: r.task_type as TaskType,
    accuracy: Number(r.accuracy),
    f1Score: Number(r.f1_score),
    precision: Number(r.precision),
    recall: Number(r.recall),
    latencyMs: r.latency_ms,
    fileSize: r.file_size,
    format: r.format,
    status: r.status as TrainedModel["status"],
    createdAt: r.created_at,
  };
}

export async function listModels(): Promise<TrainedModelExt[]> {
  const { data, error } = await supabase.from("trained_models").select("*").order("created_at", { ascending: false });
  if (error) throw error;
  return (data as ModelRow[]).map(toModel);
}

export async function getModel(id: string): Promise<TrainedModelExt | null> {
  const { data, error } = await supabase.from("trained_models").select("*").eq("id", id).maybeSingle();
  if (error) throw error;
  return data ? toModel(data as ModelRow) : null;
}
