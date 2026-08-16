import { Ban, CheckCircle2, Clock, Loader2, XCircle, type LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { JobStatus } from "@/api/types";

interface StatusConfig {
  icon: LucideIcon;
  spin?: boolean;
  className: string;
}

const statusConfig: Record<JobStatus, StatusConfig> = {
  pending: {
    icon: Clock,
    className: "border-border bg-muted text-muted-foreground",
  },
  running: {
    icon: Loader2,
    spin: true,
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  completed: {
    icon: CheckCircle2,
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  failed: {
    icon: XCircle,
    className: "border-destructive/30 bg-destructive/10 text-destructive",
  },
  cancelled: {
    icon: Ban,
    className: "border-border bg-muted text-muted-foreground",
  },
};

/** Job/dataset/run status chip. Tone + icon map mirrors the plain-CSS
 *  reference (frontend/src/components/data/StatusBadge.tsx), ported onto the
 *  shadcn Badge (outline variant + manual tone classes, since Badge has no
 *  built-in tone prop). */
export function StatusBadge({ status }: { status: JobStatus }) {
  const { icon: Icon, spin, className } = statusConfig[status];
  return (
    <Badge variant="outline" className={cn("gap-1 capitalize", className)}>
      <Icon className={cn("h-3 w-3", spin && "animate-spin")} aria-hidden />
      {status}
    </Badge>
  );
}
