import { Loader2, ListOrdered } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { QueueState } from "@/api/types";

/** Project-level GPU queue standing, from the `queue_state`/`queue_position`
 *  trio the backend adds to detail GET responses (train/export/eval share one
 *  GPU, worker concurrency 1). The fields are only populated while the
 *  project has in-flight GPU work and only on detail endpoints — list rows
 *  and terminal rows carry nulls, and this component renders nothing for
 *  them, so it is safe to drop in unconditionally next to a StatusBadge. */
export function QueueBadge({
  queueState,
  queuePosition,
}: {
  queueState: QueueState | null | undefined;
  queuePosition: number | null | undefined;
}) {
  if (queueState === "processing") {
    return (
      <Badge
        variant="outline"
        className={cn(
          "gap-1 border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
        )}
      >
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        GPU active
      </Badge>
    );
  }
  if (queueState === "queued" && queuePosition != null) {
    return (
      <Badge
        variant="outline"
        className={cn(
          "gap-1 border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
        )}
      >
        <ListOrdered className="h-3 w-3" aria-hidden />
        Queue #{queuePosition}
      </Badge>
    );
  }
  return null;
}
