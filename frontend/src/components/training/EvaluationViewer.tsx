import { ClipboardCheck } from "lucide-react";

import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { EvaluationDetail } from "@/components/evaluation/EvaluationDetail";
import { useEvaluations } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";

interface EvaluationViewerProps {
  /** Model artifact whose most recent evaluation run should be shown — e.g.
   *  from a training/model detail page once export has produced an artifact. */
  modelArtifactId: string;
  modelName?: string;
}

/** Thin consumer of `EvaluationDetail` for embedding "this model's latest
 *  evaluation" inside another page (training monitor, model detail) without
 *  that page needing to know a specific evaluation id up front. Previously
 *  this component rendered fully mocked charts off `ComparisonResult[]`
 *  (student-vs-teacher radar/latency data with no backend behind it) and had
 *  no callers left in the app — replaced with a real, reusable card now that
 *  the evaluation stage exists (`useEvaluations` / `EvaluationDetail`). The
 *  full list + start/cancel workflow now lives on the model's own detail
 *  page (`/models/:id`) — evaluation runs automatically as part of the
 *  pipeline hub once a model is exported. */
export function EvaluationViewer({ modelArtifactId, modelName }: EvaluationViewerProps) {
  const { t } = useLanguage();
  const { data, isLoading } = useEvaluations(
    { model_artifact_id: modelArtifactId, limit: 1 },
    // list endpoint's `created_at desc` ordering (see api/endpoints/evaluations.ts) means
    // the first item is always the most recent run for this artifact.
  );

  const latest = data?.items[0];

  if (isLoading) {
    return <p className="py-10 text-center text-sm text-muted-foreground">{t("common.loading")}</p>;
  }

  if (!latest) {
    return (
      <EngineEmptyState
        icon={ClipboardCheck}
        title={t("eval.empty")}
        hint={t("eval.emptyHint")}
      />
    );
  }

  return <EvaluationDetail evaluationId={latest.id} modelName={modelName} />;
}
