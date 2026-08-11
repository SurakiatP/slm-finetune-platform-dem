import { CheckCircle2, Loader2, Circle, XCircle } from "lucide-react";
import type { PipelineStep } from "@/data/trainingMockData";

const statusConfig = {
  completed: { icon: CheckCircle2, color: "text-success", bg: "bg-success/10", line: "bg-success" },
  active: { icon: Loader2, color: "text-primary", bg: "bg-primary/10", line: "bg-primary" },
  pending: { icon: Circle, color: "text-muted-foreground", bg: "bg-secondary", line: "bg-border" },
  failed: { icon: XCircle, color: "text-destructive", bg: "bg-destructive/10", line: "bg-destructive" },
};

export function PipelineSteps({ steps }: { steps: PipelineStep[] }) {
  return (
    <div className="space-y-0">
      {steps.map((step, i) => {
        const cfg = statusConfig[step.status];
        const Icon = cfg.icon;
        const isLast = i === steps.length - 1;

        return (
          <div key={step.id} className="flex gap-3">
            {/* Timeline */}
            <div className="flex flex-col items-center">
              <div className={`w-8 h-8 rounded-full flex items-center justify-center ${cfg.bg}`}>
                <Icon className={`h-4 w-4 ${cfg.color} ${step.status === "active" ? "animate-spin" : ""}`} />
              </div>
              {!isLast && <div className={`w-0.5 flex-1 min-h-[24px] ${cfg.line}`} />}
            </div>

            {/* Content */}
            <div className={`pb-5 ${isLast ? "pb-0" : ""}`}>
              <div className="flex items-center gap-2">
                <p className={`text-sm font-semibold ${step.status === "pending" ? "text-muted-foreground" : "text-foreground"}`}>
                  {step.label}
                </p>
                {step.duration && (
                  <span className="text-[10px] text-muted-foreground bg-secondary px-1.5 py-0.5 rounded">{step.duration}</span>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">{step.description}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
