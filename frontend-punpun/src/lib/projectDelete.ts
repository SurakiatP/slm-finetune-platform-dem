import { listDatasets, cancelDatasetGeneration } from "@/api/endpoints/datasets";
import { cancelEvaluation, listEvaluations } from "@/api/endpoints/evaluations";
import { cancelModelExport, listModels } from "@/api/endpoints/models";
import { deleteProject } from "@/api/endpoints/projects";
import { cancelTraining, listTrainings } from "@/api/endpoints/trainings";
import type { JobStatus } from "@/api/types";

const IN_FLIGHT_STATUSES: JobStatus[] = ["pending", "running"];

function isInFlight(status: JobStatus | null | undefined): boolean {
  return status != null && IN_FLIGHT_STATUSES.includes(status);
}

/** Fire every cancel concurrently and swallow individual failures: a job that
 *  went terminal between the list call and the cancel call 409s (or no-ops),
 *  which must not block the delete. */
async function cancelAll(cancels: Promise<unknown>[]): Promise<void> {
  await Promise.all(cancels.map((p) => p.catch(() => undefined)));
}

/**
 * Cascades a project delete: cancels every in-flight (pending/running) job
 * belonging to the project — trainings, SDG dataset generations, model
 * exports, and evaluations — and only then deletes the project row.
 *
 * `DELETE /projects/{id}` drops the DB rows but never reaches into Celery to
 * stop a worker mid-run (see api/services/projects_service.py — it is an
 * audit record plus `db.delete`). Anything left in flight therefore keeps
 * burning the resource it was already consuming after the user believes the
 * project is gone: trainings/exports/evaluations hold the single GPU slot and
 * count against the global cap, and an SDG run keeps paying OpenRouter per
 * generated sample. All cancels are best-effort and run before the delete.
 */
export async function deleteProjectCascade(projectId: string): Promise<void> {
  // Trainings and datasets are directly project-scoped; artifacts are too,
  // and evaluations hang off the artifacts, so fetch models once and reuse.
  const [trainingPages, datasetPage, modelPage] = await Promise.all([
    Promise.all(
      IN_FLIGHT_STATUSES.map((status) =>
        listTrainings({ project_id: projectId, status, limit: 200 }),
      ),
    ),
    // `listDatasets` exposes no status filter, so narrow client-side.
    listDatasets({ project_id: projectId, limit: 200 }),
    listModels({ project_id: projectId, limit: 200 }),
  ]);

  const trainingIds = trainingPages.flatMap((page) => page.items.map((t) => t.id));
  const datasetIds = datasetPage.items.filter((d) => isInFlight(d.status)).map((d) => d.id);
  const exportIds = modelPage.items.filter((m) => isInFlight(m.export_status)).map((m) => m.id);

  // Evaluations are keyed by model artifact, not by project — there is no
  // project-scoped list — so fan out across this project's artifacts.
  const evaluationPages = await Promise.all(
    modelPage.items.flatMap((model) =>
      IN_FLIGHT_STATUSES.map((status) =>
        listEvaluations({ model_artifact_id: model.id, status, limit: 200 }).catch(
          () => ({ items: [] }) as { items: { id: string }[] },
        ),
      ),
    ),
  );
  const evaluationIds = evaluationPages.flatMap((page) => page.items.map((e) => e.id));

  await cancelAll([
    ...trainingIds.map((id) => cancelTraining(id)),
    ...datasetIds.map((id) => cancelDatasetGeneration(id)),
    ...exportIds.map((id) => cancelModelExport(id)),
    ...evaluationIds.map((id) => cancelEvaluation(id)),
  ]);

  await deleteProject(projectId);
}
