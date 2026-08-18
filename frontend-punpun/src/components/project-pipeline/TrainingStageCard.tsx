import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, PlayCircle, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/engine/StatusBadge";
import TrainingCreateDialog from "@/components/training/TrainingCreateDialog";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import { formatDuration } from "@/lib/format";
import { isTerminalStatus, type Dataset, type Training } from "@/api/types";

interface TrainingStageCardProps {
  projectId: string;
  currentDataset: Dataset | null;
  currentTraining: Training | null;
}

/** Training stage detail: the "user leaves mid-SDG, comes back, can't get to
 *  training" fix lives here — the CTA's enabled/disabled state and the
 *  progress it shows afterwards are both computed straight from
 *  `currentDataset`/`currentTraining` (server data passed down from
 *  `PipelineHub`), never from wizard-local state, so it renders correctly on
 *  a cold page load at any point in the pipeline. */
export function TrainingStageCard({ projectId, currentDataset, currentTraining }: TrainingStageCardProps) {
  const { t } = useLanguage();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);

  const datasetReady = currentDataset?.status === "completed";

  if (!datasetReady) {
    return <p className="text-xs text-muted-foreground">{t("pipelineHub.waitingForSdg")}</p>;
  }

  const handleStarted = (_trainingId: string) => {
    setDialogOpen(false);
    void queryClient.invalidateQueries({ queryKey: ["trainings"] });
    toast({ title: t("pipelineHub.trainingStarted") });
  };

  if (!currentTraining) {
    return (
      <div className="flex flex-col items-start gap-2 rounded-lg border border-primary/30 bg-primary/5 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-medium text-foreground">{t("pipelineHub.readyForTraining")}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{t("pipelineHub.readyForTrainingHint")}</p>
        </div>
        <Button size="sm" className="gap-2 shrink-0" onClick={() => setDialogOpen(true)}>
          <Sparkles className="h-3.5 w-3.5" /> {t("pipelineHub.startTraining")}
        </Button>
        {currentDataset && (
          <TrainingCreateDialog
            projectId={projectId}
            datasetId={currentDataset.id}
            open={dialogOpen}
            onOpenChange={setDialogOpen}
            onStarted={handleStarted}
          />
        )}
      </div>
    );
  }

  const terminal = isTerminalStatus(currentTraining.status);

  return (
    <div className="space-y-2 rounded-lg border border-border p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-foreground truncate">
          {currentTraining.training_name || currentTraining.base_model}
        </p>
        <StatusBadge status={currentTraining.status} />
      </div>
      <p className="text-xs text-muted-foreground">
        {currentTraining.mode === "hpo" ? "HPO" : t("pipelineHub.manualMode")}
        {terminal && <> · {formatDuration(currentTraining.started_at, currentTraining.ended_at)}</>}
        {currentTraining.status === "running" && (
          <>
            {" "}
            · {t("pipelineHub.trainingRunning")}
            <Loader2 className="ml-1.5 inline h-3 w-3 animate-spin align-[-2px]" aria-hidden />
          </>
        )}
      </p>
      <Button variant="link" size="sm" className="h-auto p-0 gap-1" asChild>
        <Link to={`/projects/${projectId}/training`}>
          <PlayCircle className="h-3.5 w-3.5" /> {t("pipelineHub.viewTrainingMonitor")}
        </Link>
      </Button>
    </div>
  );
}
