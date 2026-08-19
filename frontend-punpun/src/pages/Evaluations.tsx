import { useMemo, useState } from "react";
import { ClipboardList, Search } from "lucide-react";

import { ApiError } from "@/api/client";
import type { Evaluation } from "@/api/types";
import { EvaluationDetail } from "@/components/evaluation/EvaluationDetail";
import { EvaluationTable } from "@/components/evaluation/EvaluationTable";
import { FadeIn, PageTransition } from "@/components/motion";
import { Input } from "@/components/ui/input";
import { useDatasets, useEvaluations, useModels } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

/**
 * Global, cross-project evaluation list. Mirrors Datasets.tsx's page shell
 * (header, search, loading/error/empty states) but rows expand inline
 * (accordion-style, one at a time) into the same EvaluationDetail used
 * elsewhere, instead of navigating away — no start/download actions here,
 * those stay scoped to their project/model contexts.
 */
export default function Evaluations() {
  const { t } = useLanguage();
  const [search, setSearch] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: evaluationsPage, isLoading, error } = useEvaluations({ limit: 200 });
  const { data: modelsPage } = useModels(undefined, { limit: 200 });
  const { data: datasetsPage } = useDatasets(undefined, { limit: 200 });

  const modelNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const m of modelsPage?.items ?? []) map[m.id] = m.name;
    return map;
  }, [modelsPage]);

  const datasetNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const d of datasetsPage?.items ?? []) map[d.id] = d.name;
    return map;
  }, [datasetsPage]);

  const evaluations = evaluationsPage?.items ?? [];
  const filtered = evaluations
    .filter((evaluation: Evaluation) => {
      const modelName = modelNames[evaluation.model_artifact_id] ?? "";
      const datasetName = datasetNames[evaluation.dataset_id] ?? "";
      const needle = search.toLowerCase();
      return modelName.toLowerCase().includes(needle) || datasetName.toLowerCase().includes(needle);
    })
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  const apiError = error ? errorMessage(error) : null;

  return (
    <PageTransition>
      <div className="max-w-7xl space-y-6">
        <FadeIn>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
                <ClipboardList className="h-6 w-6 text-primary" /> {t("evaluationsPage.title")}
              </h1>
              <p className="text-sm text-muted-foreground">{t("evaluationsPage.subtitle")}</p>
            </div>
          </div>
        </FadeIn>

        <FadeIn delay={0.05}>
          <div className="relative w-full sm:max-w-xs">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input
              placeholder={t("evaluationsPage.searchPlaceholder")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
              aria-label={t("evaluationsPage.searchPlaceholder")}
            />
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          {isLoading ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{t("common.loading")}</p>
          ) : apiError ? (
            <p className="py-10 text-center text-sm text-destructive">{apiError}</p>
          ) : (
            <EvaluationTable
              evaluations={filtered}
              modelNames={modelNames}
              datasetNames={datasetNames}
              expandedId={expandedId}
              onRowClick={(evaluation) =>
                setExpandedId((cur) => (cur === evaluation.id ? null : evaluation.id))
              }
              renderExpanded={(evaluation) => (
                <EvaluationDetail
                  evaluationId={evaluation.id}
                  modelName={modelNames[evaluation.model_artifact_id]}
                  datasetName={datasetNames[evaluation.dataset_id]}
                />
              )}
              emptyHint={search ? t("evaluationsPage.noResults") : t("evaluationsPage.emptyHint")}
            />
          )}
        </FadeIn>
      </div>
    </PageTransition>
  );
}
