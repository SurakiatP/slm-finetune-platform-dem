import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Trophy, ArrowRight, History, RotateCcw, Eye, CheckCircle2 } from "lucide-react";
import { TuningReport } from "./TuningReport";
import {
  getTuningRunsForProject,
  setAppliedRun,
  type TuningRun,
} from "@/lib/tuningGenerator";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";

interface TuningHistoryProps {
  project: import("@/types").Project;
}

export function TuningHistory({ project }: TuningHistoryProps) {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [runs, setRuns] = useState<TuningRun[]>(() => getTuningRunsForProject(project));
  const [viewing, setViewing] = useState<TuningRun | null>(null);

  const handleRestore = (run: TuningRun) => {
    setAppliedRun(project.id, run.runId);
    setRuns(getTuningRunsForProject(project));
    toast({
      title: t("tuningHistory.restored"),
      description: `${run.label} • Trial #${run.bestTrial} → ${run.bestAccuracy}%`,
    });
  };

  if (viewing) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-foreground">
              {viewing.label} · {new Date(viewing.completedAt).toLocaleDateString()}
            </p>
            <p className="text-xs text-muted-foreground">
              {t("tuningReport.bestTrial")} #{viewing.bestTrial} · {viewing.bestAccuracy}% acc
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setViewing(null)}>
            ← {t("tuningHistory.backToHistory")}
          </Button>
        </div>
        <TuningReport report={viewing.report} />
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground text-sm">
        {t("tuningHistory.empty")}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <History className="h-4 w-4" />
        {t("tuningHistory.subtitle").replace("{n}", String(runs.length))}
      </div>

      {runs.map((run) => {
        const accGain = (run.bestAccuracy - run.baselineAccuracy).toFixed(1);
        const f1Gain = (run.bestF1 - run.baselineF1).toFixed(1);
        return (
          <Card key={run.runId} className={run.applied ? "border-primary/40" : ""}>
            <CardContent className="p-4 space-y-3">
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-foreground">{run.label}</span>
                    {run.applied && (
                      <Badge className="gap-1 text-[10px]">
                        <CheckCircle2 className="h-3 w-3" /> {t("tuningHistory.applied")}
                      </Badge>
                    )}
                    <Badge variant="outline" className="text-[10px]">
                      <Trophy className="h-3 w-3 mr-1 text-yellow-500" />
                      Trial #{run.bestTrial}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {new Date(run.completedAt).toLocaleString()} · {run.totalTrials} trials
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setViewing(run)}>
                    <Eye className="h-3 w-3" /> {t("tuningHistory.viewDetails")}
                  </Button>
                  {!run.applied && (
                    <Button size="sm" className="gap-1.5" onClick={() => handleRestore(run)}>
                      <RotateCcw className="h-3 w-3" /> {t("tuningHistory.restore")}
                    </Button>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                {[
                  { label: "Accuracy", base: run.baselineAccuracy, best: run.bestAccuracy, gain: accGain },
                  { label: "F1 Score", base: run.baselineF1, best: run.bestF1, gain: f1Gain },
                ].map((m) => (
                  <div key={m.label} className="p-2.5 rounded-lg border border-border">
                    <p className="text-[10px] text-muted-foreground mb-1">{m.label}</p>
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-mono text-muted-foreground">{m.base}%</span>
                      <ArrowRight className="h-3 w-3 text-muted-foreground" />
                      <span className="font-mono font-bold text-primary">{m.best}%</span>
                      <Badge
                        variant="outline"
                        className="ml-auto text-[10px] text-emerald-600 dark:text-emerald-400 border-emerald-500/30"
                      >
                        +{m.gain}%
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
