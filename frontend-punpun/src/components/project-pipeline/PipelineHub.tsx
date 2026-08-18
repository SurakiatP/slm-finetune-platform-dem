import { Loader2, Upload } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useDatasets, useTrainings } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";
import { isTerminalStatus } from "@/api/types";
import { derivePipelineState } from "./deriveStages";
import { StageStepper } from "./StageStepper";
import { SdgStageCard } from "./SdgStageCard";
import { TrainingStageCard } from "./TrainingStageCard";
import { ExportEvalStageCard } from "./ExportEvalStageCard";

/**
 * ProjectDetail's pipeline hub: one card that always shows exactly where a
 * project sits across seed → SDG → training → export → evaluate, computed
 * entirely from server data (datasets, trainings, `auto_pipeline`) rather
 * than any wizard-local state. This is what lets a user leave mid-SDG-run
 * and come back (or hard-reload) to a page that correctly resumes at
 * whatever phase the server says it's in, instead of stranding them with no
 * way to reach "start training".
 */
export function PipelineHub({ projectId }: { projectId: string }) {
  const { t } = useLanguage();

  const { data: datasetsPage, isLoading: datasetsLoading } = useDatasets(
    projectId,
    { limit: 100 },
    {
      refetchInterval: (query) => {
        const items = query.state.data?.items ?? [];
        return items.some((d) => d.status === "pending" || d.status === "running") ? 3000 : false;
      },
    },
  );

  const { data: trainingsPage, isLoading: trainingsLoading } = useTrainings(
    { project_id: projectId, limit: 100 },
    {
      refetchInterval: (query) => {
        const items = query.state.data?.items ?? [];
        return items.some((tr) => !isTerminalStatus(tr.status)) ? 5000 : false;
      },
    },
  );

  if (datasetsLoading || trainingsLoading) {
    return (
      <Card>
        <CardContent className="flex justify-center py-10">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    );
  }

  const datasets = datasetsPage?.items ?? [];
  const trainings = trainingsPage?.items ?? [];
  const { seedDataset, currentDataset, currentTraining, stages } = derivePipelineState(datasets, trainings);

  const stepperLabels = {
    seed: t("pipelineHub.stageSeed"),
    sdg: t("pipelineHub.stageSdg"),
    training: t("pipelineHub.stageTraining"),
    export: t("pipelineHub.stageExport"),
    evaluate: t("pipelineHub.stageEval"),
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{t("pipelineHub.title")}</CardTitle>
        <CardDescription>{t("pipelineHub.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <StageStepper stages={stages} labels={stepperLabels} />

        {!seedDataset ? (
          <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border py-8 text-center">
            <Upload className="h-5 w-5 text-muted-foreground/50" aria-hidden />
            <p className="text-xs text-muted-foreground max-w-xs">{t("pipelineHub.noSeedYet")}</p>
            <Button variant="outline" size="sm" asChild>
              <Link to={`/projects/${projectId}/insights`}>{t("pipelineHub.goUploadSeed")}</Link>
            </Button>
          </div>
        ) : (
          <>
            <section className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("pipelineHub.stageSdg")}
              </h4>
              <SdgStageCard currentDataset={currentDataset} />
            </section>

            <section className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("pipelineHub.stageTraining")}
              </h4>
              <TrainingStageCard
                projectId={projectId}
                currentDataset={currentDataset}
                currentTraining={currentTraining}
              />
            </section>

            <section className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("pipelineHub.stageExportEval")}
              </h4>
              <ExportEvalStageCard training={currentTraining} />
            </section>
          </>
        )}
      </CardContent>
    </Card>
  );
}
