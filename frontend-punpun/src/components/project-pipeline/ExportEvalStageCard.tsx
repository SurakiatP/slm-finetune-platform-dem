import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";

import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { StageBadge } from "./StageBadge";
import { useLanguage } from "@/i18n/LanguageContext";
import type { StepperStatus } from "./deriveStages";
import type { Training } from "@/api/types";

/** Export/evaluate stage detail — read-only, straight off
 *  `training.auto_pipeline` (per the wave brief: these two steps don't get
 *  their own live WS progress bar here, just status + a link to the page
 *  that has the full detail). Renders nothing until a training run has
 *  actually completed, since `auto_pipeline` has no meaning before that. */
export function ExportEvalStageCard({ training }: { training: Training | null }) {
  const { t } = useLanguage();

  if (!training || training.status !== "completed") {
    return <p className="text-xs text-muted-foreground">{t("pipelineHub.exportEvalWaiting")}</p>;
  }

  const pipeline = training.auto_pipeline;

  if (!pipeline) {
    return <p className="text-xs text-muted-foreground">{t("pipelineHub.autoPipelineNotRequested")}</p>;
  }

  const exportStatus: StepperStatus =
    pipeline.export.status === "pending"
      ? "pending"
      : pipeline.export.status === "running"
        ? "active"
        : pipeline.export.status === "completed"
          ? "completed"
          : pipeline.export.status === "skipped"
            ? "skipped"
            : "failed";

  const evalStatus: StepperStatus =
    pipeline.evaluate.status === "pending"
      ? "pending"
      : pipeline.evaluate.status === "running"
        ? "active"
        : pipeline.evaluate.status === "completed"
          ? "completed"
          : pipeline.evaluate.status === "skipped"
            ? "skipped"
            : "failed";

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
        <span className="text-sm text-foreground">{t("pipelineHub.stageExport")}</span>
        <StageBadge status={exportStatus} label={t(`pipelineHub.autoStatus.${pipeline.export.status}`)} />
      </div>
      {pipeline.export.status === "failed" && pipeline.export.error && (
        <ErrorDetail error={{ detail: pipeline.export.error }} />
      )}
      {pipeline.export.status === "completed" && pipeline.export.artifact_id && (
        <Link
          to={`/models/${pipeline.export.artifact_id}`}
          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
        >
          {t("pipelineHub.viewModel")} <ArrowRight className="h-3 w-3" />
        </Link>
      )}

      <div className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
        <span className="text-sm text-foreground">{t("pipelineHub.stageEval")}</span>
        <StageBadge status={evalStatus} label={t(`pipelineHub.autoStatus.${pipeline.evaluate.status}`)} />
      </div>
      {pipeline.evaluate.status === "skipped" && pipeline.evaluate.skip_reason && (
        <p className="text-xs text-muted-foreground">{pipeline.evaluate.skip_reason}</p>
      )}
      {pipeline.evaluate.status === "failed" && pipeline.evaluate.error && (
        <ErrorDetail error={{ detail: pipeline.evaluate.error }} />
      )}
      {pipeline.evaluate.status === "completed" && pipeline.evaluate.evaluation_id && (
        <Link to="/evaluations" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
          {t("pipelineHub.viewEvaluation")} <ArrowRight className="h-3 w-3" />
        </Link>
      )}
    </div>
  );
}
