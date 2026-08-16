import { useMemo, useState } from "react";
import { ClipboardCheck, GitCompare, Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { ApiError } from "@/api/client";
import { isTerminalStatus } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { EvaluationDetail } from "@/components/evaluation/EvaluationDetail";
import { EvaluationTable } from "@/components/evaluation/EvaluationTable";
import { StartEvaluationDialog } from "@/components/evaluation/StartEvaluationDialog";
import { FadeIn, PageTransition } from "@/components/motion";
import { useDatasets, useEvaluations, useModels } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";

const ALL_MODELS = "__all__";

export default function Evaluations() {
  const { t } = useLanguage();
  const [modelFilter, setModelFilter] = useState<string>(ALL_MODELS);
  const [startOpen, setStartOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);

  const { data: models } = useModels(undefined, { limit: 200 });
  const { data: datasets } = useDatasets(undefined, { limit: 200 });

  const {
    data: evaluations,
    isLoading,
    error,
  } = useEvaluations(
    { model_artifact_id: modelFilter === ALL_MODELS ? undefined : modelFilter, limit: 100 },
    {
      // Poll while anything in the current page is still in flight; the
      // per-row detail view (EvaluationDetail) carries its own WS-backed
      // live progress once opened, this just keeps the list fresh.
      refetchInterval: (query) => {
        const page = query.state.data;
        if (!page) return false;
        const hasActive = page.items.some((e) => !isTerminalStatus(e.status));
        return hasActive ? 5000 : false;
      },
    },
  );

  const modelNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const m of models?.items ?? []) map[m.id] = m.name;
    return map;
  }, [models]);

  const datasetNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const d of datasets?.items ?? []) map[d.id] = d.name;
    return map;
  }, [datasets]);

  const apiError = error instanceof ApiError ? error : null;

  return (
    <PageTransition>
      <div className="max-w-7xl space-y-6">
        <FadeIn>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
                <ClipboardCheck className="h-6 w-6 text-primary" /> {t("eval.title")}
              </h1>
              <p className="text-sm text-muted-foreground">
                Task-specific metrics per model + dataset pair, with an optional LLM judge.
              </p>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" asChild>
                <Link to="/models/compare" className="gap-2">
                  <GitCompare className="h-4 w-4" /> {t("eval.compare")}
                </Link>
              </Button>
              <Button size="sm" onClick={() => setStartOpen(true)} className="gap-2">
                <Plus className="h-4 w-4" /> {t("eval.start")}
              </Button>
            </div>
          </div>
        </FadeIn>

        <FadeIn delay={0.05}>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Model:</span>
            <Select value={modelFilter} onValueChange={setModelFilter}>
              <SelectTrigger className="w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_MODELS}>All models</SelectItem>
                {(models?.items ?? []).map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          {apiError ? (
            <ErrorDetail error={{ detail: apiError.message, code: apiError.code }} />
          ) : isLoading ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{t("common.loading")}</p>
          ) : (
            <EvaluationTable
              evaluations={evaluations?.items ?? []}
              modelNames={modelNames}
              datasetNames={datasetNames}
              onRowClick={(evaluation) => setDetailId(evaluation.id)}
              emptyHint="Evaluate a trained model against a dataset to get accuracy, F1, ROUGE, or tool-calling metrics."
            />
          )}
        </FadeIn>
      </div>

      <StartEvaluationDialog
        open={startOpen}
        onOpenChange={setStartOpen}
        defaultModelArtifactId={modelFilter === ALL_MODELS ? undefined : modelFilter}
        onCreated={(accepted) => setDetailId(accepted.evaluation_id)}
      />

      <Dialog open={detailId !== null} onOpenChange={(open) => !open && setDetailId(null)}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t("eval.detail")}</DialogTitle>
          </DialogHeader>
          {detailId && (() => {
            const target = evaluations?.items.find((e) => e.id === detailId);
            return (
              <EvaluationDetail
                evaluationId={detailId}
                modelName={target ? modelNames[target.model_artifact_id] : undefined}
                datasetName={target ? datasetNames[target.dataset_id] : undefined}
              />
            );
          })()}
        </DialogContent>
      </Dialog>
    </PageTransition>
  );
}
