import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Clock, FlaskConical, Trophy } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/engine/StatusBadge";
import type { HpoChildSummary, Training } from "@/api/types";
import { useLanguage } from "@/i18n/LanguageContext";
import { formatDateTime, formatDuration, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Best trial = lowest final eval loss; trials that never reached eval sort last. */
function bestTrial(trials: HpoChildSummary[]): HpoChildSummary | null {
  return (
    [...trials]
      .filter((trial) => trial.final_eval_loss != null)
      .sort((a, b) => (a.final_eval_loss as number) - (b.final_eval_loss as number))[0] ?? null
  );
}

function formatParam(value: unknown): string {
  if (typeof value === "number") return formatNumber(value);
  return String(value);
}

/**
 * Tuning report for one HPO run, built from real Engine data (the training
 * row + MLflow child-run summaries) — replaces the mock-data TuningReport
 * that no page used (UAT DEF-023).
 */
export function HpoTuningReport({ run, trials }: { run: Training; trials: HpoChildSummary[] }) {
  const { t } = useLanguage();
  const best = bestTrial(trials);
  const bestParams: Record<string, unknown> = run.best_params_json ?? best?.params ?? {};
  const finished = trials.filter((trial) => trial.final_eval_loss != null).length;
  const objective = typeof run.config_json?.objective_metric === "string" ? run.config_json.objective_metric : "eval_loss";

  // Trial order as MLflow reports it; the chart shows how the loss moved.
  const chartData = trials
    .map((trial, i) => ({ trial: i + 1, name: trial.name, loss: trial.final_eval_loss }))
    .filter((point) => point.loss != null);

  const stats = [
    { icon: FlaskConical, label: t("hpoReport.trials"), value: `${finished} / ${trials.length}` },
    { icon: Trophy, label: t("hpoReport.bestValue").replace("{metric}", objective), value: formatNumber(run.best_metric_value ?? best?.final_eval_loss ?? null) },
    { icon: Clock, label: t("hpoReport.duration"), value: formatDuration(run.started_at, run.ended_at) },
  ];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{t("hpoReport.title")}</CardTitle>
        <CardDescription className="text-xs">{t("hpoReport.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {stats.map((s) => (
            <div key={s.label} className="flex items-center gap-3 rounded-lg border border-border p-3">
              <s.icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
              <div className="min-w-0">
                <p className="text-sm font-semibold text-foreground">{s.value}</p>
                <p className="truncate text-[11px] text-muted-foreground">{s.label}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="space-y-1.5">
          <p className="text-xs font-medium text-foreground">
            {t("hpoReport.bestParams")}
            {best && <span className="ml-1.5 font-normal text-muted-foreground">({best.name})</span>}
          </p>
          {Object.keys(bestParams).length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(bestParams).map(([key, value]) => (
                <Badge key={key} variant="outline" className="font-mono text-[10px]">
                  {key}={formatParam(value)}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">{t("hpoReport.noParams")}</p>
          )}
        </div>

        {chartData.length >= 2 && (
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-foreground">{t("hpoReport.lossByTrial")}</p>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="trial" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} allowDecimals={false} />
                  <YAxis tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} width={48} domain={["auto", "auto"]} />
                  <Tooltip
                    formatter={(value: number) => [formatNumber(value), "eval_loss"]}
                    labelFormatter={(trial) => `${t("hpoReport.trial")} ${trial}`}
                    contentStyle={{ backgroundColor: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 }}
                  />
                  <Line type="monotone" dataKey="loss" stroke="hsl(var(--primary))" strokeWidth={2} dot={{ r: 3 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Every HPO run of the project, newest first; clicking one selects it. */
export function HpoTuningHistory({
  runs,
  selectedId,
  onSelect,
}: {
  runs: Training[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const { t } = useLanguage();
  const sorted = [...runs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{t("hpoReport.historyTitle")}</CardTitle>
        <CardDescription className="text-xs">
          {t("hpoReport.historyCount").replace("{n}", String(runs.length))}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {sorted.map((run) => (
          <button
            key={run.id}
            type="button"
            onClick={() => onSelect(run.id)}
            aria-pressed={run.id === selectedId}
            className={cn(
              "flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border p-3 text-left text-xs transition-colors hover:bg-accent/50",
              run.id === selectedId && "border-primary/40 bg-accent/60",
            )}
          >
            <span className="font-medium text-foreground">{run.training_name ?? run.id.slice(0, 8)}</span>
            <StatusBadge status={run.status} />
            <span className="text-muted-foreground">{formatDateTime(run.created_at)}</span>
            <span className="ml-auto text-muted-foreground">
              {t("hpoReport.best")}: <span className="font-mono text-foreground">{formatNumber(run.best_metric_value)}</span>
            </span>
          </button>
        ))}
      </CardContent>
    </Card>
  );
}
