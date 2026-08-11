import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Trophy, ArrowRight, Beaker, Clock, Coins } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ScatterChart, Scatter } from "recharts";
import type { TuningReport as TuningReportData } from "@/data/tuningReportMockData";
import { useLanguage } from "@/i18n/LanguageContext";

interface TuningReportProps {
  report: TuningReportData;
}

export function TuningReport({ report }: TuningReportProps) {
  const r = report;
  const { t } = useLanguage();

  const completedTrials = r.trials.filter((x) => x.status === "completed");
  const sortedByAcc = [...completedTrials].sort((a, b) => a.trial - b.trial).map((x) => ({
    trial: x.trial,
    accuracy: x.accuracy,
    f1: x.f1Score,
    valLoss: x.valLoss,
  }));

  const scatterData = completedTrials.map((x) => ({
    lr: x.learningRate,
    accuracy: x.accuracy,
    trial: x.trial,
  }));

  const accGain = (r.best.accuracy - r.baseline.accuracy).toFixed(1);
  const f1Gain = (r.best.f1Score - r.baseline.f1Score).toFixed(1);
  const lossDrop = (r.baseline.valLoss - r.best.valLoss).toFixed(3);

  return (
    <div className="space-y-4">
      {/* Summary */}
      <Card className="border-primary/30 bg-primary/5">
        <CardContent className="p-4">
          <div className="flex items-start justify-between gap-3 mb-3">
            <div className="flex items-center gap-2">
              <Beaker className="h-5 w-5 text-primary" />
              <div>
                <p className="text-sm font-semibold text-foreground">{t("tuningReport.summary")}</p>
                <p className="text-xs text-muted-foreground">
                  {new Date(r.startedAt).toLocaleString()} → {new Date(r.completedAt).toLocaleString()}
                </p>
              </div>
            </div>
            <Badge variant="outline" className="text-[10px] shrink-0">
              {t("tuningReport.bestTrial")} #{r.bestTrial}
            </Badge>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-center">
            <div className="p-3 rounded-lg bg-background">
              <p className="text-2xl font-bold text-foreground">{r.totalTrials}</p>
              <p className="text-[10px] text-muted-foreground">{t("tuningReport.totalTrials")}</p>
            </div>
            <div className="p-3 rounded-lg bg-background">
              <p className="text-2xl font-bold text-yellow-500">{r.prunedTrials}</p>
              <p className="text-[10px] text-muted-foreground">{t("tuningReport.pruned")}</p>
            </div>
            <div className="p-3 rounded-lg bg-background flex flex-col items-center justify-center">
              <p className="text-2xl font-bold text-emerald-500 flex items-center gap-1">
                <Coins className="h-4 w-4" /> {r.estimatedSavings.credits}
              </p>
              <p className="text-[10px] text-muted-foreground">{t("tuningReport.creditsSaved")}</p>
            </div>
            <div className="p-3 rounded-lg bg-background flex flex-col items-center justify-center">
              <p className="text-2xl font-bold text-emerald-500 flex items-center gap-1">
                <Clock className="h-4 w-4" /> {r.estimatedSavings.minutes}m
              </p>
              <p className="text-[10px] text-muted-foreground">{t("tuningReport.timeSaved")}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Baseline vs Best */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Trophy className="h-4 w-4 text-yellow-500" /> {t("tuningReport.baselineVsBest")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-3 text-center">
            {[
              { label: "Accuracy", base: r.baseline.accuracy, best: r.best.accuracy, gain: `+${accGain}`, suffix: "%" },
              { label: "F1 Score", base: r.baseline.f1Score, best: r.best.f1Score, gain: `+${f1Gain}`, suffix: "%" },
              { label: "Val Loss", base: r.baseline.valLoss, best: r.best.valLoss, gain: `-${lossDrop}`, suffix: "" },
            ].map((m) => (
              <div key={m.label} className="p-3 rounded-lg border border-border space-y-1">
                <p className="text-[10px] text-muted-foreground">{m.label}</p>
                <div className="flex items-center justify-center gap-2 text-sm">
                  <span className="font-mono text-muted-foreground">{m.base}{m.suffix}</span>
                  <ArrowRight className="h-3 w-3 text-muted-foreground" />
                  <span className="font-mono font-bold text-primary">{m.best}{m.suffix}</span>
                </div>
                <Badge variant="outline" className="text-[10px] text-emerald-600 dark:text-emerald-400 border-emerald-500/30">
                  {m.gain}{m.suffix}
                </Badge>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">{t("tuningReport.trialProgress")}</CardTitle>
            <CardDescription className="text-xs">{t("tuningReport.trialProgressDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={sortedByAcc}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="trial" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12 }} />
                  <Line type="monotone" dataKey="accuracy" stroke="hsl(var(--primary))" strokeWidth={2} name="Accuracy" />
                  <Line type="monotone" dataKey="f1" stroke="hsl(var(--muted-foreground))" strokeWidth={2} name="F1" strokeDasharray="4 4" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">{t("tuningReport.lrAccuracy")}</CardTitle>
            <CardDescription className="text-xs">{t("tuningReport.lrAccuracyDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-[220px]">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis
                    dataKey="lr"
                    type="number"
                    scale="log"
                    domain={[5e-5, 1e-3]}
                    tick={{ fontSize: 10 }}
                    tickFormatter={(v) => v.toExponential(0)}
                  />
                  <YAxis dataKey="accuracy" type="number" tick={{ fontSize: 10 }} domain={[70, 92]} />
                  <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12 }} />
                  <Scatter data={scatterData} fill="hsl(var(--primary))" />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Trials table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{t("tuningReport.allTrials")}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="text-left py-2 px-2">#</th>
                  <th className="text-right py-2 px-2">LR</th>
                  <th className="text-right py-2 px-2">Epochs</th>
                  <th className="text-right py-2 px-2">Batch</th>
                  <th className="text-right py-2 px-2">Warmup</th>
                  <th className="text-right py-2 px-2">Val Loss</th>
                  <th className="text-right py-2 px-2">Acc</th>
                  <th className="text-right py-2 px-2">F1</th>
                  <th className="text-right py-2 px-2">{t("tuningReport.duration")}</th>
                  <th className="text-right py-2 px-2">{t("tuningReport.status")}</th>
                </tr>
              </thead>
              <tbody>
                {r.trials.map((tr) => (
                  <tr
                    key={tr.trial}
                    className={`border-b border-border/50 ${tr.trial === r.bestTrial ? "bg-primary/5" : ""}`}
                  >
                    <td className="py-1.5 px-2 font-mono">
                      {tr.trial === r.bestTrial && <Trophy className="inline h-3 w-3 text-yellow-500 mr-1" />}
                      {tr.trial}
                    </td>
                    <td className="py-1.5 px-2 text-right font-mono">{tr.learningRate.toExponential(0)}</td>
                    <td className="py-1.5 px-2 text-right">{tr.epochs}</td>
                    <td className="py-1.5 px-2 text-right">{tr.batchSize}</td>
                    <td className="py-1.5 px-2 text-right">{tr.warmupSteps}</td>
                    <td className="py-1.5 px-2 text-right font-mono">{tr.valLoss.toFixed(3)}</td>
                    <td className="py-1.5 px-2 text-right font-mono">{tr.accuracy ? `${tr.accuracy}%` : "—"}</td>
                    <td className="py-1.5 px-2 text-right font-mono">{tr.f1Score ? `${tr.f1Score}%` : "—"}</td>
                    <td className="py-1.5 px-2 text-right">{tr.durationMin}m</td>
                    <td className="py-1.5 px-2 text-right">
                      <Badge
                        variant="outline"
                        className={`text-[9px] ${
                          tr.status === "completed"
                            ? "text-emerald-600 dark:text-emerald-400 border-emerald-500/30"
                            : tr.status === "pruned"
                            ? "text-yellow-600 dark:text-yellow-400 border-yellow-500/30"
                            : "text-destructive border-destructive/30"
                        }`}
                      >
                        {tr.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

    </div>
  );
}
