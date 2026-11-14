import type { ModelArtifact, Training } from "@/api/types";

/**
 * Model artifacts don't carry a human-chosen name of their own worth
 * trusting long-term — `training_name` lives on the owning `Training` row
 * instead (set at launch, unique per owner). Every place a model artifact
 * is listed or shown should prefer that name, falling back to whatever the
 * artifact itself reports (`model.name`) when the training was never named.
 *
 * Callers join client-side from an already-fetched `Training[]` (one list
 * query) rather than issuing one `GET /trainings/{id}` per model artifact.
 */

/** `training.id -> training.training_name` for every named training in a
 *  fetched list. Unnamed trainings (`training_name: null`) are omitted so a
 *  lookup miss and an explicit "no name" both fall through to the caller's
 *  fallback the same way. */
export function buildTrainingNameMap(trainings: Training[] | undefined): Record<string, string> {
  const map: Record<string, string> = {};
  for (const training of trainings ?? []) {
    if (training.training_name) map[training.id] = training.training_name;
  }
  return map;
}

/** Resolve the display name for one model artifact: the owning training's
 *  `training_name` when known, else the artifact's own `name`. */
export function modelDisplayName(
  model: Pick<ModelArtifact, "training_job_id" | "name">,
  trainingNames: Record<string, string>,
): string {
  return trainingNames[model.training_job_id] ?? model.name;
}

/**
 * Default `training_name` for a run launched without one, derived from the
 * project name so the resulting model is recognisable — instead of the
 * worker's `manual-<id>` fallback (UAT DEF-012). Must satisfy the Ollama-tag
 * rule `^[a-z0-9][a-z0-9._-]{0,62}$`; non-Latin names (e.g. Thai) fall back
 * to "model". A timestamp suffix keeps it unique per owner.
 */
export function suggestTrainingName(projectName: string | null | undefined, now: Date = new Date()): string {
  const slug = (projectName ?? "")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/, "");
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
  return `${slug || "model"}-${stamp}`;
}
