import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  engineFindProjectByExternalId,
  engineCreateProject,
  engineListDatasets,
  engineListTrainings,
  engineUploadSeed,
  engineGenerateDataset,
  engineGetDataset,
  engineStartTraining,
  updateProject,
  patchEngineMeta,
  getEngineMeta,
  setEngineMeta,
} = vi.hoisted(() => ({
  engineFindProjectByExternalId: vi.fn(),
  engineCreateProject: vi.fn(),
  engineListDatasets: vi.fn(),
  engineListTrainings: vi.fn(),
  engineUploadSeed: vi.fn(),
  engineGenerateDataset: vi.fn(),
  engineGetDataset: vi.fn(),
  engineStartTraining: vi.fn(),
  updateProject: vi.fn(),
  patchEngineMeta: vi.fn(),
  getEngineMeta: vi.fn(),
  setEngineMeta: vi.fn(),
}));

vi.mock("@/lib/engineApi", () => ({
  EngineApiError: class EngineApiError extends Error {
    status: number;
    code: string | null = null;
    requestId: string | null = null;
    constructor(status: number, body: { detail?: string }) {
      super(body.detail);
      this.status = status;
    }
  },
  engineCreateProject,
  engineFindProjectByExternalId,
  engineListDatasets,
  engineListTrainings,
  engineUploadSeed,
  engineGenerateDataset,
  engineGetDataset,
  engineStartTraining,
}));

vi.mock("@/lib/projectsApi", () => ({
  createProject: vi.fn(),
  updateProject,
}));

vi.mock("@/lib/engineStore", () => ({
  getEngineMeta,
  setEngineMeta,
  patchEngineMeta,
}));

import { EngineApiError } from "@/lib/engineApi";
import { runEngineWorkflow } from "@/lib/engineWorkflow";

describe("NewProject Engine workflow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateProject.mockResolvedValue({});
    getEngineMeta.mockReturnValue({ engineProjectId: "engine-project" });
    engineListDatasets.mockResolvedValue({ items: [] });
    engineListTrainings.mockResolvedValue({ items: [] });
    engineUploadSeed.mockResolvedValue({ dataset_id: "seed-dataset" });
    engineGenerateDataset.mockResolvedValue({ dataset_id: "generated-dataset", job_id: "sdg-job" });
    engineGetDataset.mockResolvedValue({ id: "generated-dataset", status: "completed" });
    engineStartTraining.mockResolvedValue({ training_id: "training", job_id: "training-job" });
  });

  it("runs upload, generation and training in order", async () => {
    const file = new File(["{}"], "seed.json", { type: "application/json" });

    await runEngineWorkflow({
      supabaseProjectId: "supabase-project",
      taskType: "qa",
      taskDescription: "Answer questions about the policy document.",
      seedFile: file,
      epochs: 3,
      learningRate: 0.0002,
      baseModel: "qwen2.5-1.5b",
      projectName: "Project",
    });

    expect(engineUploadSeed).toHaveBeenCalledWith(file, "engine-project", "qa", "seed");
    expect(engineGenerateDataset).toHaveBeenCalledWith(
      "engine-project", "qa", "Answer questions about the policy document.", "seed-dataset", 200, 50,
      "sdg:supabase-project",
    );
    expect(engineStartTraining).toHaveBeenCalledWith(
      "engine-project",
      "generated-dataset",
      "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit",
      "Project",
      expect.any(Object),
      "training:supabase-project",
    );
    expect(updateProject).toHaveBeenCalledWith("supabase-project", { status: "training", progress: 0 });
    expect(patchEngineMeta).toHaveBeenCalledWith("supabase-project", expect.objectContaining({
      phase: "training",
      trainingId: "training",
      jobId: "training-job",
    }));
  });

  it("passes a catalog model ID to the Engine without remapping it", async () => {
    await runEngineWorkflow({
      supabaseProjectId: "supabase-project",
      taskType: "qa",
      taskDescription: "Answer questions about the policy document.",
      seedFile: new File(["{}"], "seed.json"),
      epochs: 3,
      learningRate: 0.0002,
      baseModel: "unsloth/Qwen3-0.6B-unsloth-bnb-4bit",
      projectName: "Project",
    });

    expect(engineStartTraining).toHaveBeenCalledWith(
      "engine-project",
      "generated-dataset",
      "unsloth/Qwen3-0.6B-unsloth-bnb-4bit",
      "Project",
      expect.any(Object),
      "training:supabase-project",
    );
  });

  it("marks the real project failed and never starts training when upload fails", async () => {
    engineUploadSeed.mockRejectedValue(new Error("Engine unavailable"));

    await runEngineWorkflow({
      supabaseProjectId: "supabase-project",
      taskType: "qa",
      taskDescription: "Answer questions about the policy document.",
      seedFile: new File(["{}"], "seed.json"),
      epochs: 3,
      learningRate: 0.0002,
      baseModel: "qwen2.5-1.5b",
      projectName: "Project",
    });

    expect(engineStartTraining).not.toHaveBeenCalled();
    expect(patchEngineMeta).toHaveBeenLastCalledWith("supabase-project", expect.objectContaining({
      phase: "failed",
      error: "Engine unavailable",
      failureStep: "upload",
    }));
    expect(updateProject).toHaveBeenCalledWith("supabase-project", { status: "failed" });
  });

  it("recovers the existing Engine project after a create conflict", async () => {
    getEngineMeta.mockReturnValue(null);
    engineFindProjectByExternalId
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ id: "existing-engine-project" });
    engineCreateProject.mockRejectedValue(new EngineApiError(409, { detail: "duplicate external id" }, null));

    await runEngineWorkflow({
      supabaseProjectId: "supabase-project",
      taskType: "qa",
      taskDescription: "Answer questions about the policy document.",
      seedFile: new File(["{}"], "seed.json"),
      epochs: 3,
      learningRate: 0.0002,
      baseModel: "qwen2.5-1.5b",
      projectName: "Project",
    });

    expect(setEngineMeta).toHaveBeenCalledWith("supabase-project", expect.objectContaining({
      engineProjectId: "existing-engine-project",
    }));
    expect(engineListDatasets).toHaveBeenCalledWith("existing-engine-project");
  });

  it("resumes from backend state after local metadata is lost", async () => {
    getEngineMeta.mockReturnValue(null);
    engineFindProjectByExternalId.mockResolvedValue({ id: "existing-engine-project" });
    engineListDatasets.mockResolvedValue({ items: [
      { id: "seed-dataset", source: "seed", parent_dataset_id: null, status: "completed" },
      { id: "generated-dataset", source: "sdg", parent_dataset_id: null, status: "completed", celery_task_id: "sdg-job" },
    ] });
    engineGetDataset.mockResolvedValue({ id: "generated-dataset", status: "completed" });
    engineListTrainings.mockResolvedValue({ items: [{
      id: "training", status: "running", celery_task_id: "training-job", error_message: null,
    }] });

    await runEngineWorkflow({
      supabaseProjectId: "supabase-project",
      taskType: "qa",
      taskDescription: "Answer questions about the policy document.",
      epochs: 3,
      learningRate: 0.0002,
      baseModel: "qwen2.5-1.5b",
      projectName: "Project",
    });

    expect(engineUploadSeed).not.toHaveBeenCalled();
    expect(engineGenerateDataset).not.toHaveBeenCalled();
    expect(engineStartTraining).not.toHaveBeenCalled();
    expect(patchEngineMeta).toHaveBeenCalledWith("supabase-project", expect.objectContaining({
      trainingId: "training",
      jobId: "training-job",
    }));
  });
});
