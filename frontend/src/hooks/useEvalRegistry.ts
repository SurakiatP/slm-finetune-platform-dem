import { isTerminalStatus, type Evaluation } from '@/api/types'
import { useDatasets, useEvaluations } from '@/hooks/queries'

/**
 * Project-scoped evaluation list.
 *
 * The backend's `GET /evaluations` (api/routers/evaluations.py) filters by
 * `model_artifact_id` / `dataset_id` / `status` / `limit` / `offset` but has
 * no `project_id` filter. To scope the list to the current project without a
 * backend change, we fetch the project's datasets, build a Set of their ids,
 * then filter the (unscoped) evaluations list down to rows whose
 * `dataset_id` is in that set. This is client-side scoping — evaluations
 * against a dataset that isn't in this project's first 200 datasets would be
 * missed (see the 200-row cap note below), but every evaluation is created
 * against a dataset picked from this project's own dataset list, so in
 * practice every relevant row is covered.
 *
 * Both the dataset list and the evaluation list are capped at 200 rows
 * (`limit: 200`) — same pragmatic cap used elsewhere in this app for
 * unpaginated dropdowns/lists. Projects with more than 200 datasets or more
 * than 200 evaluations platform-wide may see incomplete results.
 */
export function useEvalRegistry(projectId: string): {
  evaluations: Evaluation[]
  isLoading: boolean
  isError: boolean
} {
  const datasetsQuery = useDatasets(projectId, { limit: 200 })
  const evaluationsQuery = useEvaluations(
    { limit: 200 },
    {
      refetchInterval: (query) =>
        query.state.data?.items.some((e) => !isTerminalStatus(e.status)) ? 5_000 : false,
    },
  )

  const datasetIds = new Set((datasetsQuery.data?.items ?? []).map((d) => d.id))

  const evaluations = (evaluationsQuery.data?.items ?? [])
    .filter((e) => datasetIds.has(e.dataset_id))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))

  return {
    evaluations,
    isLoading: datasetsQuery.isLoading || evaluationsQuery.isLoading,
    isError: datasetsQuery.isError || evaluationsQuery.isError,
  }
}
