import { Ban, CheckCircle2, Loader2, Circle, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import type { PipelineStages, StepperStatus } from "./deriveStages";

const nodeConfig: Record<StepperStatus, { icon: typeof Circle; className: string; line: string }> = {
  pending: { icon: Circle, className: "text-muted-foreground bg-secondary", line: "bg-border" },
  active: { icon: Loader2, className: "text-primary bg-primary/10", line: "bg-border" },
  completed: { icon: CheckCircle2, className: "text-emerald-600 dark:text-emerald-400 bg-emerald-500/10", line: "bg-emerald-500/60" },
  failed: { icon: XCircle, className: "text-destructive bg-destructive/10", line: "bg-border" },
  skipped: { icon: Ban, className: "text-muted-foreground bg-secondary", line: "bg-border" },
};

interface StageStepperProps {
  stages: PipelineStages;
  labels: { seed: string; sdg: string; training: string; export: string; evaluate: string };
}

/** Horizontal 5-node overview of seed → SDG → training → export → evaluate.
 *  Purely presentational — every status comes from `deriveStages.ts`, itself
 *  computed from server data, so this never drifts from what a page reload
 *  would show. */
export function StageStepper({ stages, labels }: StageStepperProps) {
  const nodes: { key: keyof PipelineStages; label: string }[] = [
    { key: "seed", label: labels.seed },
    { key: "sdg", label: labels.sdg },
    { key: "training", label: labels.training },
    { key: "export", label: labels.export },
    { key: "evaluate", label: labels.evaluate },
  ];

  return (
    <div className="flex items-start">
      {nodes.map((node, i) => {
        const status = stages[node.key];
        const cfg = nodeConfig[status];
        const Icon = cfg.icon;
        const isLast = i === nodes.length - 1;
        return (
          <div key={node.key} className={cn("flex items-center", !isLast && "flex-1")}>
            <div className="flex flex-col items-center gap-1.5 shrink-0">
              <div className={cn("flex h-8 w-8 items-center justify-center rounded-full", cfg.className)}>
                <Icon className={cn("h-4 w-4", status === "active" && "animate-spin")} aria-hidden />
              </div>
              <span className="text-[10px] font-medium text-muted-foreground text-center w-16">{node.label}</span>
            </div>
            {!isLast && <div className={cn("h-0.5 flex-1 mx-1 mb-4", cfg.line)} />}
          </div>
        );
      })}
    </div>
  );
}
