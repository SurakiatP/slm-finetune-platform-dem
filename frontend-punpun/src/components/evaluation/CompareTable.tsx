import { Minus, TrendingDown, TrendingUp, Trophy } from "lucide-react";

import type { EvaluationCompareResponse } from "@/api/types";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { metricMeta } from "@/lib/metrics";
import { formatNumber, shortId } from "@/lib/format";
import { useLanguage } from "@/i18n/LanguageContext";
import { cn } from "@/lib/utils";

interface CompareTableProps {
  result: EvaluationCompareResponse;
  /** evaluation_id -> display label (e.g. "model name · dataset name"). Falls back to a short id. */
  labels?: Record<string, string>;
}

/** Per-metric comparison across the evaluations picked in ModelComparison.
 *  With exactly two evaluations selected, the columns are framed as
 *  Baseline vs Fine-tuned with an explicit delta column (the common case:
 *  comparing a model's eval run against an earlier/base one); with more than
 *  two, every column is shown side by side and the best value per metric
 *  (ratio/judge-score metrics only — "higher is better") is starred instead,
 *  since "delta" isn't well-defined across >2 points. */
export function CompareTable({ result, labels }: CompareTableProps) {
  const { t } = useLanguage();
  const ids = result.evaluation_ids;
  const twoWay = ids.length === 2;
  const metricNames = Object.keys(result.metrics);
  const hasJudge = Object.values(result.judge_scores).some((v) => v !== null);

  const labelFor = (id: string) => labels?.[id] ?? shortId(id);

  const rows = metricNames.map((name) => ({
    name,
    values: ids.map((id) => result.metrics[name]?.[id] ?? null),
  }));
  if (hasJudge) {
    rows.push({ name: "llm_judge_score", values: ids.map((id) => result.judge_scores[id] ?? null) });
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-40">{t("compare.metric")}</TableHead>
            {ids.map((id, i) => (
              <TableHead key={id} className="text-right">
                {twoWay && (
                  <div className="text-[10px] font-normal uppercase tracking-wide text-muted-foreground">
                    {i === 0 ? t("eval.baseline") : t("eval.finetuned")}
                  </div>
                )}
                <div className="font-mono text-xs" title={id}>
                  {labelFor(id)}
                </div>
              </TableHead>
            ))}
            {twoWay && <TableHead className="text-right">{t("eval.delta")}</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map(({ name, values }) => {
            const sample = values.find((v): v is number => v !== null) ?? 0;
            const meta = metricMeta(name, sample);
            const higherIsBetter = meta.kind !== "count";
            const numericValues = values.filter((v): v is number => v !== null);
            const best = higherIsBetter && numericValues.length > 1 ? Math.max(...numericValues) : null;

            const delta = twoWay && values[0] !== null && values[1] !== null ? values[1] - values[0] : null;

            return (
              <TableRow key={name}>
                <TableCell className="font-medium text-muted-foreground">{name}</TableCell>
                {values.map((v, i) => (
                  <TableCell key={ids[i]} className="text-right">
                    <span className={cn(v !== null && best !== null && v === best && "font-semibold text-primary")}>
                      {meta.kind === "score5" ? (v === null ? "—" : `${formatNumber(v, 2)}/5`) : formatNumber(v)}
                    </span>
                    {v !== null && best !== null && v === best && numericValues.length > 1 && (
                      <Trophy className="ml-1 inline h-3 w-3 text-warning" aria-hidden />
                    )}
                  </TableCell>
                ))}
                {twoWay && (
                  <TableCell className="text-right">
                    {delta === null ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      <span
                        className={cn(
                          "inline-flex items-center gap-1 font-medium",
                          !higherIsBetter
                            ? "text-muted-foreground"
                            : delta > 0
                              ? "text-emerald-600 dark:text-emerald-400"
                              : delta < 0
                                ? "text-destructive"
                                : "text-muted-foreground",
                        )}
                      >
                        {higherIsBetter && delta > 0 && <TrendingUp className="h-3 w-3" aria-hidden />}
                        {higherIsBetter && delta < 0 && <TrendingDown className="h-3 w-3" aria-hidden />}
                        {delta === 0 && <Minus className="h-3 w-3" aria-hidden />}
                        {delta > 0 ? "+" : ""}
                        {formatNumber(delta)}
                      </span>
                    )}
                  </TableCell>
                )}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
