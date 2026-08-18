/**
 * Pure derivation of "where is this project in the seed → SDG → training →
 * export → evaluate pipeline" from server data only (datasets list,
 * trainings list, and `Training.auto_pipeline`). No component in this
 * directory keeps its own notion of pipeline progress in local state — a
 * full page reload must land on the same stage, because the server is the
 * only source of truth (see W2-T3: the bug being fixed is a user leaving
 * mid-SDG and the wizard's local state forgetting where they were).
 */
import { datasetRoleTag } from "@/lib/labels";
import { isTerminalStatus, type AutoPipelineStepStatus, type Dataset, type Training } from "@/api/types";

/** Status for one node in the top-level stage stepper. `skipped` is its own
 *  visual state (not `completed`) so an auto_evaluate step that ran with no
 *  holdout dataset doesn't read as "evaluation happened". */
export type StepperStatus = "pending" | "active" | "completed" | "failed" | "skipped";

export interface PipelineStages {
  seed: StepperStatus;
  sdg: StepperStatus;
  training: StepperStatus;
  export: StepperStatus;
  evaluate: StepperStatus;
}

export interface PipelineDerivedState {
  /** Most recently created seed-role dataset, if any. */
  seedDataset: Dataset | null;
  /** Most recently created SDG-output dataset (excludes hold-out splits) —
   *  the dataset the rest of the pipeline (training/export/eval) is tracked
   *  against. A project mid a *second* SDG regeneration attempt tracks that
   *  attempt, even if an older generation already completed — the hub always
   *  reflects the latest attempt, matching what the wizard would show. */
  currentDataset: Dataset | null;
  /** Training run tied to `currentDataset`: the active (non-terminal) one if
   *  there is one, else the most recently created — mirrors TrainingMonitor's
   *  own run-selection rule for consistency across pages. */
  currentTraining: Training | null;
  stages: PipelineStages;
}

function byCreatedAtDesc<T extends { created_at: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

export function pickSeedDataset(datasets: Dataset[]): Dataset | null {
  const seeds = datasets.filter((d) => datasetRoleTag(d) === "seed");
  return byCreatedAtDesc(seeds)[0] ?? null;
}

/** Latest non-hold-out SDG output dataset — `datasetRoleTag` already treats
 *  hold-out splits as their own category, so plain `.filter` here can't
 *  accidentally pick one up as "the" training dataset. */
export function pickCurrentDataset(datasets: Dataset[]): Dataset | null {
  const generated = datasets.filter((d) => datasetRoleTag(d) === "training");
  return byCreatedAtDesc(generated)[0] ?? null;
}

export function pickCurrentTraining(trainings: Training[], datasetId: string | null): Training | null {
  if (!datasetId) return null;
  const scoped = trainings.filter((tr) => tr.dataset_id === datasetId);
  if (scoped.length === 0) return null;
  const active = scoped.filter((tr) => !isTerminalStatus(tr.status));
  return byCreatedAtDesc(active.length > 0 ? active : scoped)[0];
}

function autoStepToStepper(status: AutoPipelineStepStatus): StepperStatus {
  switch (status) {
    case "completed":
      return "completed";
    case "running":
      return "active";
    case "failed":
      return "failed";
    case "skipped":
      return "skipped";
    case "pending":
    default:
      return "pending";
  }
}

export function computeStages(
  seedDataset: Dataset | null,
  currentDataset: Dataset | null,
  currentTraining: Training | null,
): PipelineStages {
  const seed: StepperStatus = seedDataset ? "completed" : "pending";

  let sdg: StepperStatus = "pending";
  if (currentDataset) {
    if (currentDataset.status === "completed") sdg = "completed";
    else if (currentDataset.status === "running" || currentDataset.status === "pending") sdg = "active";
    else sdg = "failed"; // failed | cancelled
  }

  let training: StepperStatus = "pending";
  if (currentTraining) {
    if (currentTraining.status === "completed") training = "completed";
    else if (currentTraining.status === "running" || currentTraining.status === "pending") training = "active";
    else training = "failed"; // failed | cancelled
  }

  let exportStage: StepperStatus = "pending";
  let evaluate: StepperStatus = "pending";
  if (currentTraining?.status === "completed") {
    const pipeline = currentTraining.auto_pipeline;
    if (pipeline) {
      exportStage = autoStepToStepper(pipeline.export.status);
      evaluate = autoStepToStepper(pipeline.evaluate.status);
    } else {
      // Training finished but neither auto_export nor auto_evaluate was
      // requested at launch — nothing will move these forward on its own.
      exportStage = "skipped";
      evaluate = "skipped";
    }
  }

  return { seed, sdg, training, export: exportStage, evaluate };
}

export function derivePipelineState(datasets: Dataset[], trainings: Training[]): PipelineDerivedState {
  const seedDataset = pickSeedDataset(datasets);
  const currentDataset = pickCurrentDataset(datasets);
  const currentTraining = pickCurrentTraining(trainings, currentDataset?.id ?? null);
  const stages = computeStages(seedDataset, currentDataset, currentTraining);
  return { seedDataset, currentDataset, currentTraining, stages };
}
