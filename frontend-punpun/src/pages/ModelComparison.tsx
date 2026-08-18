import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from "recharts";

import { ApiError } from "@/api/client";
import type { Evaluation } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { CompareTable } from "@/components/evaluation/CompareTable";
import { FadeIn, PageTransition } from "@/components/motion";
import { buildTrainingNameMap, modelDisplayName } from "@/components/model/modelNaming";
import { useCompareEvaluations, useDatasets, useEvaluations, useModels, useTrainings } from "@/hooks/queries";
import { metricMeta } from "@/lib/metrics";
import { shortId } from "@/lib/format";
import { useLanguage } from "@/i18n/LanguageContext";
import { GitCompare, Loader2, Scale } from "lucide-react";

const COLORS = ["hsl(var(--primary))", "hsl(var(--warning))", "hsl(var(--destructive))", "hsl(142,71%,45%)"];
const MAX_SLOTS = 4;

export default function ModelComparison() {
  const { t } = useLanguage();

  const { data: models } = useModels(undefined, { limit: 200 });
  const { data: trainings } = useTrainings({ limit: 200 });
  const { data: datasets } = useDatasets(undefined, { limit: 200 });
  const { data: completedPage, isLoading: evaluationsLoading } = useEvaluations({
    status: "completed",
    limit: 100,
  });
  const compareMutation = useCompareEvaluations();

  const candidates = useMemo(() => completedPage?.items ?? [], [completedPage]);
  const [selected, setSelected] = useState<string[]>([]);

  // Seed the first two slots once completed evaluations load — mirrors the
  // old mock page's default-select behavior, but via an effect instead of
  // mutating state mid-render.
  useEffect(() => {
    if (selected.length === 0 && candidates.length >= 2) {
      setSelected([candidates[0].id, candidates[1].id]);
    }
  }, [candidates, selected.length]);

  const modelNames = useMemo(() => {
    const trainingNames = buildTrainingNameMap(trainings?.items);
    const map: Record<string, string> = {};
    for (const m of models?.items ?? []) map[m.id] = modelDisplayName(m, trainingNames);
    return map;
  }, [models, trainings]);

  const datasetNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const d of datasets?.items ?? []) map[d.id] = d.name;
    return map;
  }, [datasets]);

  const labelFor = (evaluation: Evaluation) =>
    `${modelNames[evaluation.model_artifact_id] ?? shortId(evaluation.model_artifact_id)} · ${
      datasetNames[evaluation.dataset_id] ?? shortId(evaluation.dataset_id)
    }`;

  const handleSelect = (index: number, value: string) => {
    const next = [...selected];
    next[index] = value;
    setSelected(next);
  };

  const addSlot = () => {
    if (selected.length >= MAX_SLOTS) return;
    const unused = candidates.find((c) => !selected.includes(c.id));
    if (unused) setSelected([...selected, unused.id]);
  };

  const removeSlot = (index: number) => {
    if (selected.length > 2) setSelected(selected.filter((_, i) => i !== index));
  };

  const runCompare = () => {
    if (selected.length < 2) return;
    compareMutation.mutate({ evaluation_ids: selected });
  };

  const result = compareMutation.data;
  const compareError = compareMutation.error instanceof ApiError ? compareMutation.error : null;

  const compareLabels = useMemo(() => {
    const map: Record<string, string> = {};
    for (const id of selected) {
      const ev = candidates.find((c) => c.id === id);
      if (ev) map[id] = labelFor(ev);
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, candidates, modelNames, datasetNames]);

  // Radar overview only makes sense for 0–1 "ratio" metrics — mixing in
  // counts (n, skipped rows) or the 1–5 judge score would distort the scale.
  const radarData = useMemo(() => {
    if (!result) return [];
    const ratioMetrics = Object.keys(result.metrics).filter((name) => {
      const sample = Object.values(result.metrics[name]).find((v): v is number => v !== null) ?? 0;
      return metricMeta(name, sample).kind === "ratio";
    });
    return ratioMetrics.map((name) => {
      const entry: Record<string, string | number> = { metric: name };
      result.evaluation_ids.forEach((id, i) => {
        const v = result.metrics[name]?.[id];
        if (v !== null && v !== undefined) entry[`s${i}`] = v * 100;
      });
      return entry;
    });
  }, [result]);

  return (
    <PageTransition>
      <div className="max-w-7xl space-y-6">
        <FadeIn>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
                <GitCompare className="h-6 w-6 text-primary" /> {t("compare.title")}
              </h1>
              <p className="text-sm text-muted-foreground">{t("compare.subtitle")}</p>
            </div>
            {selected.length < MAX_SLOTS && candidates.length > selected.length && (
              <Button variant="outline" size="sm" onClick={addSlot}>
                {t("compare.addModel")}
              </Button>
            )}
          </div>
        </FadeIn>

        {!evaluationsLoading && candidates.length < 2 ? (
          <EngineEmptyState
            icon={Scale}
            title="Not enough completed evaluations yet"
            hint="Run at least two evaluations to completion, then come back here to compare them."
            action={
              <Button asChild size="sm">
                <Link to="/evaluations">{t("eval.start")}</Link>
              </Button>
            }
          />
        ) : (
          <>
            <FadeIn delay={0.1}>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {selected.map((id, i) => (
                  <div key={i} className="space-y-2">
                    <div className="flex items-center gap-2">
                      <div className="h-3 w-3 shrink-0 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />
                      <Select value={id} onValueChange={(v) => handleSelect(i, v)}>
                        <SelectTrigger className="flex-1">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {candidates.map((c) => (
                            <SelectItem key={c.id} value={c.id}>
                              <span className="font-mono text-xs">{labelFor(c)}</span>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {selected.length > 2 && (
                        <Button variant="ghost" size="sm" onClick={() => removeSlot(i)} className="text-xs">
                          ✕
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </FadeIn>

            <FadeIn delay={0.15}>
              <div className="flex justify-end">
                <Button onClick={runCompare} disabled={selected.length < 2 || compareMutation.isPending}>
                  {compareMutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  {t("eval.compareCta")}
                </Button>
              </div>
            </FadeIn>

            {compareError && (
              <FadeIn>
                <ErrorDetail error={{ detail: compareError.message, code: compareError.code }} />
              </FadeIn>
            )}

            {result && (
              <>
                <FadeIn delay={0.2}>
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">{t("compare.metricsComparison")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <CompareTable result={result} labels={compareLabels} />
                    </CardContent>
                  </Card>
                </FadeIn>

                {radarData.length > 0 && (
                  <FadeIn delay={0.25}>
                    <Card>
                      <CardHeader>
                        <CardTitle className="text-lg">{t("compare.radarOverview")}</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <div className="h-72">
                          <ResponsiveContainer width="100%" height="100%">
                            <RadarChart data={radarData}>
                              <PolarGrid stroke="hsl(var(--border))" />
                              <PolarAngleAxis dataKey="metric" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                              <PolarRadiusAxis domain={[0, 100]} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 10 }} />
                              {result.evaluation_ids.map((id, i) => (
                                <Radar
                                  key={id}
                                  name={compareLabels[id] ?? shortId(id)}
                                  dataKey={`s${i}`}
                                  stroke={COLORS[i % COLORS.length]}
                                  fill={COLORS[i % COLORS.length]}
                                  fillOpacity={0.15}
                                  strokeWidth={2}
                                />
                              ))}
                            </RadarChart>
                          </ResponsiveContainer>
                        </div>
                      </CardContent>
                    </Card>
                  </FadeIn>
                )}
              </>
            )}
          </>
        )}
      </div>
    </PageTransition>
  );
}
