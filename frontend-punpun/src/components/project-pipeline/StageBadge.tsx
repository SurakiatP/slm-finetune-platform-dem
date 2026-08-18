import { Ban, CheckCircle2, Circle, Loader2, XCircle, type LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { StepperStatus } from "./deriveStages";

interface StageBadgeConfig {
  icon: LucideIcon;
  spin?: boolean;
  className: string;
}

const config: Record<StepperStatus, StageBadgeConfig> = {
  pending: { icon: Circle, className: "border-border bg-muted text-muted-foreground" },
  active: {
    icon: Loader2,
    spin: true,
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  completed: {
    icon: CheckCircle2,
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  failed: { icon: XCircle, className: "border-destructive/30 bg-destructive/10 text-destructive" },
  skipped: { icon: Ban, className: "border-border bg-muted text-muted-foreground" },
};

/** Status pill for one pipeline stage node — used both in the top stepper and
 *  inside each stage's detail card, so the two always agree visually. Not
 *  `StatusBadge` (engine/StatusBadge.tsx only covers `JobStatus`, no
 *  `skipped`) since a stage can be "skipped" (auto_evaluate with no holdout,
 *  or an export/evaluate that was never requested). */
export function StageBadge({ status, label }: { status: StepperStatus; label: string }) {
  const { icon: Icon, spin, className } = config[status];
  return (
    <Badge variant="outline" className={cn("gap-1", className)}>
      <Icon className={cn("h-3 w-3", spin && "animate-spin")} aria-hidden />
      {label}
    </Badge>
  );
}
