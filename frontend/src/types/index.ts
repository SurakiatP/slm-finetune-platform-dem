export type ProjectStatus = "training" | "completed" | "failed" | "queued" | "paused";
export type TaskType = "classification" | "ner" | "qa" | "function-calling" | "extraction" | "ranking";
// Base model identifiers are provided by the Engine catalog at runtime.
export type BaseModel = string;

export interface Project {
  id: string;
  name: string;
  description: string;
  taskType: TaskType;
  baseModel: BaseModel;
  status: ProjectStatus;
  progress: number;
  createdAt: string;
  updatedAt: string;
  epochs: number;
  learningRate: number;
  datasetSize: number;
  creditsCost: number;
  pinned?: boolean;
  tags?: string[];
}

export interface TrainedModel {
  id: string;
  name: string;
  projectId: string;
  baseModel: BaseModel;
  taskType: TaskType;
  accuracy: number;
  f1Score: number;
  fileSize: string;
  format: string;
  status: "ready" | "deploying" | "deployed";
  createdAt: string;
}

export interface TrainingJob {
  id: string;
  projectId: string;
  status: ProjectStatus;
  currentEpoch: number;
  totalEpochs: number;
  loss: number;
  startedAt: string;
  estimatedCompletion: string;
}

export interface UsageStats {
  totalProjects: number;
  modelsTrainedCount: number;
  trainingHoursUsed: number;
  creditsRemaining: number;
  creditsTotal: number;
  planTier: "Free" | "Pro" | "Enterprise";
}

export interface ActivityDataPoint {
  date: string;
  jobs: number;
  credits: number;
}

export interface EvaluationMetrics {
  accuracy: number;
  f1Score: number;
  precision: number;
  recall: number;
  rouge1: number;
  latencyMs: number;
}
