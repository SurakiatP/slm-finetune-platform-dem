import { useMemo } from "react";
import { useEvaluations, useTrainings } from "@/hooks/queries";

export type DatasetUsage = "training" | "evaluation";

/**
 * Which datasets the server will refuse to delete: any training job or
 * evaluation run that references a dataset blocks DELETE /datasets/{id}
 * (409), whatever that job's status. Shared by /datasets and the project
 * Insights page so both disable the delete button for the same rows
 * (UAT DEF-011, DEF-024).
 */
export function useDatasetUsage(): Map<string, DatasetUsage> {
  const { data: trainingsPage } = useTrainings({ limit: 200 });
  const { data: evaluationsPage } = useEvaluations({ limit: 200 });
  return useMemo(() => {
    const usage = new Map<string, DatasetUsage>();
    for (const ev of evaluationsPage?.items ?? []) usage.set(ev.dataset_id, "evaluation");
    // Training wins when both apply — it is the reason the server reports first.
    for (const tr of trainingsPage?.items ?? []) usage.set(tr.dataset_id, "training");
    return usage;
  }, [trainingsPage, evaluationsPage]);
}
