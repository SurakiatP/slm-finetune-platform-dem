import type { BaseModel, Project, TaskType } from "@/types";
import {
  EngineApiError,
  engineCreateProject,
  engineFindProjectByExternalId,
  engineGenerateDataset,
  engineGetDataset,
  engineListDatasets,
  engineListTrainings,
  engineStartTraining,
  engineUploadSeed,
} from "@/lib/engineApi";
import { getEngineMeta, patchEngineMeta, setEngineMeta } from "@/lib/engineStore";
import { updateProject } from "@/lib/projectsApi";
import {
  BASE_MODEL_TO_ENGINE,
  TASK_TYPE_TO_ENGINE,
  buildManualConfig,
} from "@/lib/engineMappings";

export interface EngineWorkflowInput {
  supabaseProjectId: string;
  taskType: TaskType;
  taskDescription: string;
  seedFile?: File;
  epochs: number;
  learningRate: number;
  baseModel: BaseModel;
  projectName: string;
}

type FailureStep = "auth" | "project" | "upload" | "sdg" | "training";
const activeWorkflows = new Set<string>();

export function isEngineWorkflowActive(projectId: string): boolean {
  return activeWorkflows.has(projectId);
}

function errorPatch(error: unknown, failureStep: FailureStep) {
  if (error instanceof EngineApiError) {
    return {
      phase: "failed" as const,
      failureStep: error.code === "missing_session" ? "auth" as const : failureStep,
      error: error.message,
      errorStatus: error.status,
      errorCode: error.code ?? undefined,
      errorRequestId: error.requestId ?? undefined,
    };
  }
  return {
    phase: "failed" as const,
    failureStep,
    error: error instanceof Error ? error.message : "Engine workflow failed",
  };
}

async function pollDatasetReady(datasetId: string, maxAttempts = 240, intervalMs = 5000): Promise<string> {
  for (let i = 0; i < maxAttempts; i++) {
    const dataset = await engineGetDataset(datasetId);
    if (dataset.status === "completed") return datasetId;
    if (dataset.status === "failed" || dataset.status === "cancelled") {
      throw new Error(dataset.error_message || `Dataset generation ${dataset.status}`);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error("Dataset generation is still running. Reopen the project to resume monitoring.");
}

async function findOrCreateEngineProject(input: EngineWorkflowInput): Promise<string> {
  const storedId = getEngineMeta(input.supabaseProjectId)?.engineProjectId;
  if (storedId) return storedId;

  const existing = await engineFindProjectByExternalId(input.supabaseProjectId);
  if (existing) {
    setEngineMeta(input.supabaseProjectId, { engineProjectId: existing.id, phase: "project_created" });
    return existing.id;
  }

  const taskType = TASK_TYPE_TO_ENGINE[input.taskType];
  if (!taskType) throw new Error("Selected task is not supported by the Engine");

  try {
    const created = await engineCreateProject(
      input.projectName,
      input.taskDescription,
      taskType,
      input.supabaseProjectId,
    );
    setEngineMeta(input.supabaseProjectId, { engineProjectId: created.id, phase: "project_created" });
    return created.id;
  } catch (error) {
    if (!(error instanceof EngineApiError) || error.status !== 409) throw error;
    const raced = await engineFindProjectByExternalId(input.supabaseProjectId);
    if (!raced) throw error;
    setEngineMeta(input.supabaseProjectId, { engineProjectId: raced.id, phase: "project_created" });
    return raced.id;
  }
}

export async function runEngineWorkflow(input: EngineWorkflowInput): Promise<void> {
  if (activeWorkflows.has(input.supabaseProjectId)) return;
  activeWorkflows.add(input.supabaseProjectId);
  let failureStep: FailureStep = "project";
  try {
    const engineTaskType = TASK_TYPE_TO_ENGINE[input.taskType];
    const engineBaseModel = BASE_MODEL_TO_ENGINE[input.baseModel];
    if (!engineTaskType || !engineBaseModel) {
      throw new Error("Selected task or base model is not supported by the Engine");
    }

    const engineProjectId = await findOrCreateEngineProject(input);
    let meta = getEngineMeta(input.supabaseProjectId);
    const datasets = await engineListDatasets(engineProjectId);

    failureStep = "upload";
    let seedDatasetId = meta?.seedDatasetId
      ?? datasets.items.find((dataset) => dataset.source === "seed")?.id;
    if (!seedDatasetId) {
      if (!input.seedFile) {
        throw new Error("The original seed file is required to resume before upload completed.");
      }
      patchEngineMeta(input.supabaseProjectId, { phase: "uploading", error: undefined });
      const seed = await engineUploadSeed(input.seedFile, engineProjectId, engineTaskType, "seed");
      seedDatasetId = seed.dataset_id;
      patchEngineMeta(input.supabaseProjectId, { seedDatasetId });
    }

    failureStep = "sdg";
    meta = getEngineMeta(input.supabaseProjectId);
    const recoveredGenerated = datasets.items.find(
      (dataset) => dataset.source === "sdg" && dataset.parent_dataset_id === null,
    );
    let trainDatasetId = meta?.trainDatasetId ?? recoveredGenerated?.id;
    if (!trainDatasetId) {
      patchEngineMeta(input.supabaseProjectId, { phase: "generating", error: undefined });
      const generated = await engineGenerateDataset(
        engineProjectId,
        engineTaskType,
        input.taskDescription,
        seedDatasetId,
        200,
        50,
        `sdg:${input.supabaseProjectId}`,
      );
      trainDatasetId = generated.dataset_id;
      patchEngineMeta(input.supabaseProjectId, {
        sdgJobId: generated.job_id,
        trainDatasetId,
      });
    } else if (recoveredGenerated?.celery_task_id) {
      patchEngineMeta(input.supabaseProjectId, {
        sdgJobId: recoveredGenerated.celery_task_id,
        trainDatasetId,
        phase: "generating",
      });
    }

    await pollDatasetReady(trainDatasetId);
    patchEngineMeta(input.supabaseProjectId, { trainDatasetId });

    failureStep = "training";
    meta = getEngineMeta(input.supabaseProjectId);
    const trainings = await engineListTrainings(engineProjectId);
    const recoveredTraining = trainings.items[0];
    let projectStatus: "training" | "completed" = "training";
    if (recoveredTraining) {
      if (recoveredTraining.status === "failed" || recoveredTraining.status === "cancelled") {
        throw new Error(recoveredTraining.error_message || `Training ${recoveredTraining.status}`);
      }
      projectStatus = recoveredTraining.status === "completed" ? "completed" : "training";
      patchEngineMeta(input.supabaseProjectId, {
        trainingId: recoveredTraining.id,
        jobId: recoveredTraining.celery_task_id ?? meta?.jobId,
        phase: recoveredTraining.status === "completed" ? "completed" : "training",
      });
    } else if (!meta?.trainingId) {
      const training = await engineStartTraining(
        engineProjectId,
        trainDatasetId,
        engineBaseModel,
        input.projectName,
        buildManualConfig(input.epochs, input.learningRate),
        `training:${input.supabaseProjectId}`,
      );
      patchEngineMeta(input.supabaseProjectId, {
        trainingId: training.training_id,
        jobId: training.job_id,
        phase: "training",
      });
    }

    await updateProject(input.supabaseProjectId, {
      status: projectStatus,
      progress: projectStatus === "completed" ? 100 : 0,
    });
  } catch (error) {
    const patch = errorPatch(error, failureStep);
    patchEngineMeta(input.supabaseProjectId, patch);
    await updateProject(input.supabaseProjectId, { status: "failed" }).catch(() => undefined);
  } finally {
    activeWorkflows.delete(input.supabaseProjectId);
  }
}

export function workflowInputFromProject(project: Project): EngineWorkflowInput {
  return {
    supabaseProjectId: project.id,
    taskType: project.taskType,
    taskDescription: project.description,
    epochs: project.epochs,
    learningRate: project.learningRate,
    baseModel: project.baseModel,
    projectName: project.name,
  };
}
