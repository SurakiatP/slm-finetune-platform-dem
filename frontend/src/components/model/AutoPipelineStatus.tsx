import { CheckCircle2, Clock, Loader2, SkipForward, XCircle, type LucideIcon } from "lucide-react";

import type { AutoPipelineState, AutoPipelineStepStatus } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { shortId } from "@/lib/format";
import { useLanguage } from "@/i18n/LanguageContext";
import { cn } from "@/lib/utils";

// AutoPipelineStepStatus adds 'skipped' on top of the plain JobStatus set
// (the evaluate step reports it when there's no holdout dataset), so this
// can't reuse components/engine/StatusBadge's JobStatus-only map.
const STEP_CONFIG: Record<AutoPipelineStepStatus, { icon: LucideIcon; spin?: boolean; className: string }> = {
  pending: { icon: Clock, className: "border-border bg-muted text-muted-foreground" },
  running: {
    icon: Loader2,
    spin: true,
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  completed: {
    icon: CheckCircle2,
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  failed: { icon: XCircle, className: "border-destructive/30 bg-destructive/10 text-destructive" },
  skipped: { icon: SkipForward, className: "border-border bg-muted text-muted-foreground" },
};

function StepStatusBadge({ status }: { status: AutoPipelineStepStatus }) {
  const { icon: Icon, spin, className } = STEP_CONFIG[status];
  return (
    <Badge variant="outline" className={cn("gap-1 capitalize", className)}>
      <Icon className={cn("h-3 w-3", spin && "animate-spin")} aria-hidden />
      {status}
    </Badge>
  );
}

/** Live state of the auto_export → auto_evaluate chain kicked off after a
 *  training completes (`Training.auto_pipeline`). Renders nothing useful
 *  callers shouldn't show — parent decides whether `pipeline` is non-null
 *  before mounting this. */
export function AutoPipelineStatus({ pipeline }: { pipeline: AutoPipelineState }) {
  const { t } = useLanguage();

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{t("modelNaming.autoPipeline")}</CardTitle>
        <p className="text-xs text-muted-foreground">{t("modelNaming.autoPipelineDesc")}</p>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex items-center justify-between gap-3">
          <span className="text-muted-foreground">{t("modelDetail.export")}</span>
          <div className="flex items-center gap-2">
            {pipeline.export.artifact_id && (
              <span className="font-mono text-xs text-foreground">{shortId(pipeline.export.artifact_id)}</span>
            )}
            <StepStatusBadge status={pipeline.export.status} />
          </div>
        </div>
        {pipeline.export.error && (
          <ErrorDetail error={{ detail: pipeline.export.error, code: null }} />
        )}

        <div className="flex items-center justify-between gap-3">
          <span className="text-muted-foreground">{t("eval.title")}</span>
          <div className="flex items-center gap-2">
            {pipeline.evaluate.evaluation_id && (
              <span className="font-mono text-xs text-foreground">{shortId(pipeline.evaluate.evaluation_id)}</span>
            )}
            <StepStatusBadge status={pipeline.evaluate.status} />
          </div>
        </div>
        {pipeline.evaluate.skip_reason && (
          <p className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{t("modelNaming.skipReason")}:</span> {pipeline.evaluate.skip_reason}
          </p>
        )}
        {pipeline.evaluate.error && (
          <ErrorDetail error={{ detail: pipeline.evaluate.error, code: null }} />
        )}
      </CardContent>
    </Card>
  );
}
