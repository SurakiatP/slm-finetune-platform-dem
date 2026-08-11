import { supabase } from "@/integrations/supabase/client";
import type { Project, TaskType, BaseModel, ProjectStatus } from "@/types";

// DB row shape (snake_case)
interface ProjectRow {
  id: string;
  user_id: string;
  name: string;
  description: string;
  task_type: string;
  base_model: string;
  status: string;
  progress: number;
  epochs: number;
  learning_rate: number;
  dataset_size: number;
  credits_cost: number;
  pinned: boolean;
  tags: string[];
  created_at: string;
  updated_at: string;
}

function toProject(r: ProjectRow): Project {
  return {
    id: r.id,
    name: r.name,
    description: r.description,
    taskType: r.task_type as TaskType,
    baseModel: r.base_model as BaseModel,
    status: r.status as ProjectStatus,
    progress: r.progress,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    epochs: r.epochs,
    learningRate: r.learning_rate,
    datasetSize: r.dataset_size,
    creditsCost: r.credits_cost,
    pinned: r.pinned,
    tags: r.tags,
  };
}

export async function listProjects(): Promise<Project[]> {
  const { data, error } = await supabase
    .from("projects")
    .select("*")
    .order("updated_at", { ascending: false });
  if (error) throw error;
  return (data as ProjectRow[]).map(toProject);
}

export async function getProject(id: string): Promise<Project | null> {
  const { data, error } = await supabase
    .from("projects")
    .select("*")
    .eq("id", id)
    .maybeSingle();
  if (error) throw error;
  return data ? toProject(data as ProjectRow) : null;
}

export interface CreateProjectInput {
  name: string;
  description?: string;
  taskType: TaskType;
  baseModel: BaseModel;
  epochs?: number;
  learningRate?: number;
  datasetSize?: number;
  tags?: string[];
}

export async function createProject(input: CreateProjectInput): Promise<Project> {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error("Not authenticated");

  const { data, error } = await supabase
    .from("projects")
    .insert({
      user_id: user.id,
      name: input.name,
      description: input.description ?? "",
      task_type: input.taskType,
      base_model: input.baseModel,
      epochs: input.epochs ?? 3,
      learning_rate: input.learningRate ?? 2e-4,
      dataset_size: input.datasetSize ?? 0,
      tags: input.tags ?? [],
      status: "queued",
      progress: 0,
    })
    .select("*")
    .single();
  if (error) throw error;
  return toProject(data as ProjectRow);
}

export async function updateProject(id: string, patch: Partial<Project>): Promise<Project> {
  const dbPatch: {
    name?: string;
    description?: string;
    task_type?: string;
    base_model?: string;
    status?: string;
    progress?: number;
    epochs?: number;
    learning_rate?: number;
    dataset_size?: number;
    credits_cost?: number;
    pinned?: boolean;
    tags?: string[];
  } = {};
  if (patch.name !== undefined) dbPatch.name = patch.name;
  if (patch.description !== undefined) dbPatch.description = patch.description;
  if (patch.taskType !== undefined) dbPatch.task_type = patch.taskType;
  if (patch.baseModel !== undefined) dbPatch.base_model = patch.baseModel;
  if (patch.status !== undefined) dbPatch.status = patch.status;
  if (patch.progress !== undefined) dbPatch.progress = patch.progress;
  if (patch.epochs !== undefined) dbPatch.epochs = patch.epochs;
  if (patch.learningRate !== undefined) dbPatch.learning_rate = patch.learningRate;
  if (patch.datasetSize !== undefined) dbPatch.dataset_size = patch.datasetSize;
  if (patch.creditsCost !== undefined) dbPatch.credits_cost = patch.creditsCost;
  if (patch.pinned !== undefined) dbPatch.pinned = patch.pinned;
  if (patch.tags !== undefined) dbPatch.tags = patch.tags;

  const { data, error } = await supabase
    .from("projects")
    .update(dbPatch)
    .eq("id", id)
    .select("*")
    .single();
  if (error) throw error;
  return toProject(data as ProjectRow);
}

export async function deleteProject(id: string): Promise<void> {
  const { error } = await supabase.from("projects").delete().eq("id", id);
  if (error) throw error;
}
