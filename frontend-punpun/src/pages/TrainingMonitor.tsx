import { useEffect, useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { PageTransition, StaggerContainer, StaggerItem } from "@/components/motion";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ArrowLeft, Ban, Clock, Cpu, Database, Gauge, Layers, Plus, Trash2 } from "lucide-react";
import { PipelineSteps } from "@/components/training/PipelineSteps";
import TrainingCreateDialog from "@/components/training/TrainingCreateDialog";
import { derivePipelineState } from "@/components/project-pipeline/deriveStages";
import { LossCurveChart, type LossChartPoint } from "@/components/training/LossCurveChart";
import { DiagnosticPanel } from "@/components/training/DiagnosticPanel";
import { EvaluationViewer } from "@/components/training/EvaluationViewer";
import { MetricsTable } from "@/components/training/MetricsTable";
import { HpoTrialsTable } from "@/components/training/HpoTrialsTable";
import { TrainingLog } from "@/components/training/TrainingLog";
import type { PipelineStep, TrainingLogEntry } from "@/data/trainingMockData";
import { getBaseModelLabel } from "@/data/mockData";
import { TrainingMonitorSkeleton } from "@/components/skeletons/TrainingMonitorSkeleton";
import { StatusBadge } from "@/components/engine/StatusBadge";
import { QueueBadge } from "@/components/engine/QueueBadge";
import { ConfirmDialog } from "@/components/engine/ConfirmDialog";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import {
  useProject,
  useTrainings,
  useLossHistory,
  useTrainingMetrics,
  useCancelTraining,
  useDeleteTraining,
  useModels,
  useDatasets,
} from "@/hooks/queries";
import { useJobProgress } from "@/hooks/useJobProgress";
import { isTerminalStatus, type Training, type MetricPoint } from "@/api/types";
import { ApiError } from "@/api/client";
import { formatDuration, formatNumber, shortId } from "@/lib/format";
import { useTaskTypeLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";

const clockTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : "--:--:--";

/** Builds the terminal-style log feed the original rendered with `TrainingLog`,
 *  from real Engine state (run lifecycle + live WS steps) instead of the mock
 *  `mockTrainingLog` fixture. */
function buildTrainingLog(
  run: Training,
  live: { step: number; steps_total: number; epoch: number; epochs_total: number; train_loss: number | null } | null,
  errorMessage: string | null,
): TrainingLogEntry[] {
  const entries: TrainingLogEntry[] = [
    {
      timestamp: clockTime(run.created_at),
      level: "info",
      message: `Queued ${run.mode === "hpo" ? "HPO search" : "fine-tuning"} run ${shortId(run.id)}`,
    },
    {
      timestamp: clockTime(run.created_at),
      level: "info",
      message: `Base model: ${run.base_model} | dataset: ${shortId(run.dataset_id)}`,
    },
  ];
  if (run.started_at) {
    entries.push({ timestamp: clockTime(run.started_at), level: "info", message: "Training started on the GPU worker" });
  }
  if (run.mlflow_run_id) {
    entries.push({ timestamp: clockTime(run.started_at), level: "info", message: `MLflow run: ${run.mlflow_run_id}` });
  }
  if (live) {
    entries.push({
      timestamp: clockTime(new Date().toISOString()),
      level: "info",
      message:
        `step ${live.step}/${live.steps_total} · epoch ${live.epoch.toFixed(2)}/${live.epochs_total}` +
        (live.train_loss !== null ? ` · train_loss ${live.train_loss.toFixed(4)}` : ""),
    });
  }
  if (run.best_metric_value !== null) {
    entries.push({
      timestamp: clockTime(run.ended_at ?? run.updated_at),
      level: "info",
      message: `Best metric: ${formatNumber(run.best_metric_value)}`,
    });
  }
  if (run.status === "completed") {
    entries.push({ timestamp: clockTime(run.ended_at), level: "success", message: "Training completed" });
  } else if (run.status === "failed") {
    entries.push({ timestamp: clockTime(run.ended_at), level: "error", message: errorMessage ?? "Training failed" });
  } else if (run.status === "cancelled") {
    entries.push({ timestamp: clockTime(run.ended_at), level: "warning", message: "Training cancelled" });
  }
  return entries;
}

/** Merges MLflow's separate train/eval loss series (from useLossHistory) into
 *  one step-indexed array for the chart — used to backfill when the WS
 *  socket has no history yet (revisits, page reload mid-run). */
function mergeLossHistory(train: MetricPoint[], evalPts: MetricPoint[]): LossChartPoint[] {
  const byStep = new Map<number, LossChartPoint>();
  for (const p of train) byStep.set(p.step, { step: p.step, trainLoss: p.value, valLoss: null });
  for (const p of evalPts) {
    const existing = byStep.get(p.step);
    if (existing) existing.valLoss = p.value;
    else byStep.set(p.step, { step: p.step, trainLoss: null, valLoss: p.value });
  }
  return [...byStep.values()].sort((a, b) => a.step - b.step);
}

export default function TrainingMonitor() {
  const { id: projectId } = useParams<{ id: string }>();
  const { t } = useLanguage();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const taskTypeLabel = useTaskTypeLabel();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const { data: project, isLoading: projectLoading } = useProject(projectId ?? "");

  const { data: trainingsPage, isLoading: trainingsLoading } = useTrainings(
    { project_id: projectId, limit: 50 },
    {
      refetchInterval: (query) => {
        const items = query.state.data?.items ?? [];
        return items.some((tr) => !isTerminalStatus(tr.status)) ? 6000 : false;
      },
    },
  );
  const trainingList = useMemo(() => trainingsPage?.items ?? [], [trainingsPage]);

  // Auto-select the active run, falling back to the most recent one; keeps
  // the current selection if it's still present in a refetched list.
  useEffect(() => {
    if (trainingList.length === 0) return;
    if (selectedId && trainingList.some((tr) => tr.id === selectedId)) return;
    const active = trainingList.find((tr) => !isTerminalStatus(tr.status));
    setSelectedId((active ?? trainingList[0]).id);
  }, [trainingList, selectedId]);

  const selected: Training | null = trainingList.find((tr) => tr.id === selectedId) ?? null;
  const selectedTerminal = selected ? isTerminalStatus(selected.status) : true;

  // Dataset to launch a new run against — same pipeline-derivation logic as
  // the project overview's TrainingStageCard, so "new run" here targets the
  // same dataset a fresh training launched from the overview would use.
  // Falls back to the currently selected run's dataset when no SDG output
  // exists yet (e.g. project only has older/hold-out datasets on file).
  const { data: datasetsPage } = useDatasets(projectId, { limit: 100 });
  const datasetList = useMemo(() => datasetsPage?.items ?? [], [datasetsPage]);
  const pipelineDataset = useMemo(
    () => derivePipelineState(datasetList, trainingList).currentDataset,
    [datasetList, trainingList],
  );
  const resolvedDatasetId = pipelineDataset?.id ?? selected?.dataset_id ?? null;

  // REST list is authoritative for status; WS enriches with per-step detail
  // while the run is live. No socket is opened once terminal.
  const progress = useJobProgress(selectedTerminal ? null : (selected?.celery_task_id ?? null), {
    onTerminal: () => {
      void queryClient.invalidateQueries({ queryKey: ["trainings"] });
    },
  });

  // MLflow-backed backfill — only needed when the WS has nothing yet
  // (fresh page load on a run that already has history).
  const { data: storedLoss } = useLossHistory(
    selected?.id ?? "",
    !!selected && !!selected.mlflow_run_id && progress.lossHistory.length === 0,
  );

  const { data: trainingMetrics, isLoading: metricsLoading } = useTrainingMetrics(
    selected?.id ?? "",
    !!selected,
  );

  // Model artifact produced by the selected run — feeds the evaluation tab.
  const { data: projectModels } = useModels(projectId, { limit: 100 });
  const selectedArtifact = (projectModels?.items ?? []).find((m) => m.training_job_id === selectedId) ?? null;

  const lossData: LossChartPoint[] =
    progress.lossHistory.length > 0
      ? progress.lossHistory.map((p) => ({ step: p.step, trainLoss: p.train_loss, valLoss: p.eval_loss }))
      : mergeLossHistory(storedLoss?.train_loss ?? [], storedLoss?.eval_loss ?? []);

  const liveTraining = progress.hpo?.inner_progress ?? progress.training ?? null;
  const currentTrainLoss = liveTraining?.train_loss ?? lossData.at(-1)?.trainLoss ?? null;
  const currentValLoss = liveTraining?.eval_loss ?? lossData.at(-1)?.valLoss ?? null;

  const cancelMutation = useCancelTraining();
  const deleteMutation = useDeleteTraining();

  if (projectLoading || trainingsLoading) return <TrainingMonitorSkeleton />;

  if (!project) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">{t("projectDetail.notFound")}</p>
        <Button variant="link" asChild><Link to="/projects">{t("projectDetail.backToProjects")}</Link></Button>
      </div>
    );
  }

  const canCancel = !!selected && (selected.status === "pending" || selected.status === "running");
  const canDelete = !!selected && isTerminalStatus(selected.status);

  const pipelineSteps: PipelineStep[] = selected
    ? [
        {
          id: "dataset",
          label: "Dataset prepared",
          description: `Dataset ${shortId(selected.dataset_id)} attached to this run`,
          status: "completed",
        },
        {
          id: "training",
          label: selected.mode === "hpo" ? "Hyperparameter search" : "Fine-tuning",
          description:
            selected.mode === "hpo"
              ? "LoRA fine-tuning across HPO trials"
              : "LoRA fine-tuning on the selected base model",
          status:
            selected.status === "completed"
              ? "completed"
              : selected.status === "failed" || selected.status === "cancelled"
                ? "failed"
                : selected.status === "running"
                  ? "active"
                  : "pending",
          duration: selectedTerminal ? formatDuration(selected.started_at, selected.ended_at) : undefined,
        },
        {
          id: "export",
          label: "Export & registration",
          description: "GGUF export and Ollama registration",
          status: progress.completed?.model_artifact_id ? "completed" : "pending",
        },
      ]
    : [];

  return (
    <PageTransition>
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" asChild>
          <Link to={`/projects/${projectId}`}><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-foreground">{project.name}</h1>
            {selected && <StatusBadge status={selected.status} />}
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">{t("training.title")}</p>
        </div>
        {resolvedDatasetId && (
          <Button size="sm" className="gap-2 shrink-0" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3.5 w-3.5" /> {t("training.newRun")}
          </Button>
        )}
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Training runs</CardTitle>
        </CardHeader>
        <CardContent>
          {trainingList.length === 0 ? (
            <EngineEmptyState
              icon={Layers}
              title="No training runs yet"
              hint={t("training.newRunEmptyHint")}
              action={
                resolvedDatasetId ? (
                  <Button size="sm" className="gap-2" onClick={() => setCreateOpen(true)}>
                    <Plus className="h-3.5 w-3.5" /> {t("training.newRun")}
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Mode</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Base model</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Run</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trainingList.map((tr) => (
                  <TableRow
                    key={tr.id}
                    onClick={() => setSelectedId(tr.id)}
                    aria-selected={tr.id === selectedId}
                    className={cn("cursor-pointer", tr.id === selectedId && "bg-accent/60 hover:bg-accent/60")}
                  >
                    <TableCell>
                      <Badge variant={tr.mode === "hpo" ? "secondary" : "outline"} className="text-[10px] capitalize">
                        {tr.mode}
                      </Badge>
                    </TableCell>
                    <TableCell><StatusBadge status={tr.status} /></TableCell>
                    <TableCell className="font-mono text-xs">{getBaseModelLabel(tr.base_model)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {tr.started_at ? new Date(tr.started_at).toLocaleString() : "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {tr.training_name ?? shortId(tr.id)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {selected && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant={selected.mode === "hpo" ? "secondary" : "outline"} className="capitalize">
                {selected.mode}
              </Badge>
              <StatusBadge status={selected.status} />
              <QueueBadge queueState={selected.queue_state} queuePosition={selected.queue_position} />
            </div>
            {canCancel && (
              <Button variant="destructive" size="sm" onClick={() => setCancelOpen(true)} disabled={cancelMutation.isPending}>
                <Ban className="h-3.5 w-3.5" /> {t("training.cancel")}
              </Button>
            )}
            {canDelete && (
              <Button variant="destructive" size="sm" onClick={() => setDeleteOpen(true)} disabled={deleteMutation.isPending}>
                <Trash2 className="h-3.5 w-3.5" /> {t("training.delete")}
              </Button>
            )}
          </div>

          <StaggerContainer className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: t("training.baseModel"), value: getBaseModelLabel(selected.base_model), icon: Cpu },
              { label: t("training.taskType"), value: taskTypeLabel(project.task_type), icon: Database },
              {
                label: t("training.epochProgress"),
                value: liveTraining ? `${liveTraining.epoch.toFixed(1)} / ${liveTraining.epochs_total}` : "—",
                icon: Gauge,
              },
              { label: t("training.elapsedTime"), value: formatDuration(selected.started_at, selected.ended_at), icon: Clock },
            ].map((s) => (
              <StaggerItem key={s.label}>
                <Card>
                  <CardContent className="p-3.5 flex items-center gap-3">
                    <div className="p-2 rounded-lg bg-accent">
                      <s.icon className="h-4 w-4 text-accent-foreground" />
                    </div>
                    <div>
                      <p className="text-sm font-bold text-foreground">{s.value}</p>
                      <p className="text-[10px] text-muted-foreground">{s.label}</p>
                    </div>
                  </CardContent>
                </Card>
              </StaggerItem>
            ))}
          </StaggerContainer>

          {liveTraining && (
            <Card>
              <CardContent className="p-4 space-y-3">
                <div className="space-y-1.5">
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">Steps</span>
                    <span className="font-semibold text-foreground">
                      {liveTraining.step} / {liveTraining.steps_total}
                    </span>
                  </div>
                  <Progress
                    value={liveTraining.steps_total > 0 ? (liveTraining.step / liveTraining.steps_total) * 100 : 0}
                    className="h-2.5"
                  />
                </div>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">{t("training.epochProgress")}</span>
                    <span className="font-semibold text-foreground">
                      {liveTraining.epoch.toFixed(1)} / {liveTraining.epochs_total}
                    </span>
                  </div>
                  <Progress
                    value={liveTraining.epochs_total > 0 ? (liveTraining.epoch / liveTraining.epochs_total) * 100 : 0}
                    className="h-2.5"
                  />
                </div>
                {progress.hpo && (
                  <div className="space-y-1.5">
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">Trials</span>
                      <span className="font-semibold text-foreground">
                        {progress.hpo.trial_number + 1} / {progress.hpo.trials_total}
                      </span>
                    </div>
                    <Progress
                      value={
                        progress.hpo.trials_total > 0
                          ? ((progress.hpo.trial_number + 1) / progress.hpo.trials_total) * 100
                          : 0
                      }
                      className="h-2.5"
                    />
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          <DiagnosticPanel status={selected.status} errorMessage={progress.failed?.error ?? selected.error_message} />

          <Tabs defaultValue="pipeline">
            <TabsList>
              <TabsTrigger value="pipeline">{t("training.pipeline")}</TabsTrigger>
              <TabsTrigger value="loss">{t("training.lossCurve")}</TabsTrigger>
              <TabsTrigger value="metrics">{t("training.metrics")}</TabsTrigger>
              <TabsTrigger value="logs">{t("training.trainingLog")}</TabsTrigger>
              <TabsTrigger value="evaluation">{t("training.evaluation")}</TabsTrigger>
            </TabsList>

            <TabsContent value="pipeline" className="mt-4">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">{t("training.trainingPipeline")}</CardTitle>
                </CardHeader>
                <CardContent>
                  <PipelineSteps steps={pipelineSteps} />
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="loss" className="mt-4">
              <Card>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm">{t("training.lossCurve")}</CardTitle>
                    <div className="flex gap-3 text-[10px] text-muted-foreground">
                      <span>Current train loss: <span className="font-bold text-foreground">{formatNumber(currentTrainLoss)}</span></span>
                      <span>Current val loss: <span className="font-bold text-foreground">{formatNumber(currentValLoss)}</span></span>
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  {lossData.length > 0 ? (
                    <LossCurveChart data={lossData} />
                  ) : (
                    <p className="py-16 text-center text-sm text-muted-foreground">
                      No loss metrics have been recorded by the Engine yet.
                    </p>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="metrics" className="mt-4 space-y-4">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">{t("training.metrics")}</CardTitle>
                </CardHeader>
                <CardContent>
                  {metricsLoading ? (
                    <p className="py-8 text-center text-sm text-muted-foreground">{t("common.loading")}</p>
                  ) : (
                    <MetricsTable metrics={trainingMetrics?.metrics ?? {}} />
                  )}
                </CardContent>
              </Card>

              {selected.mode === "hpo" && trainingMetrics?.hpo_children && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">{t("training.hpoTrials")}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <HpoTrialsTable trials={trainingMetrics.hpo_children} />
                  </CardContent>
                </Card>
              )}
            </TabsContent>

            <TabsContent value="logs" className="mt-4">
              <Card>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm">{t("training.trainingLog")}</CardTitle>
                    <Badge variant="outline" className="text-[10px]">Engine data only</Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <TrainingLog
                    logs={buildTrainingLog(
                      selected,
                      liveTraining,
                      progress.failed?.error ?? selected.error_message,
                    )}
                  />
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="evaluation" className="mt-4">
              {selectedArtifact ? (
                <EvaluationViewer modelArtifactId={selectedArtifact.id} modelName={selectedArtifact.name} />
              ) : (
                <Card>
                  <CardContent className="py-16 text-center text-sm text-muted-foreground">
                    Export this training run to a model artifact first — evaluations run against an exported model.
                  </CardContent>
                </Card>
              )}
            </TabsContent>
          </Tabs>
        </>
      )}

      {resolvedDatasetId && (
        <TrainingCreateDialog
          projectId={projectId ?? ""}
          datasetId={resolvedDatasetId}
          open={createOpen}
          onOpenChange={setCreateOpen}
          onStarted={() => {
            setCreateOpen(false);
            void queryClient.invalidateQueries({ queryKey: ["trainings"] });
            toast({ title: t("training.newRun") });
          }}
        />
      )}

      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        onConfirm={() => {
          if (!selected) return;
          cancelMutation.mutate(selected.id, {
            onSuccess: () => {
              setCancelOpen(false);
              toast({ title: t("training.cancel") });
            },
            onError: (err) => {
              toast({
                title: t("common.error"),
                description: err instanceof Error ? err.message : String(err),
                variant: "destructive",
              });
            },
          });
        }}
        title={t("training.cancel")}
        description={t("training.cancelConfirm")}
        confirmLabel={t("common.confirm")}
        destructive
        loading={cancelMutation.isPending}
      />

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        onConfirm={() => {
          if (!selected) return;
          deleteMutation.mutate(selected.id, {
            onSuccess: () => {
              setDeleteOpen(false);
              setSelectedId(null);
              toast({ title: t("training.delete") });
            },
            onError: (err) => {
              setDeleteOpen(false);
              if (err instanceof ApiError && err.status === 409) {
                toast({
                  title: t("training.deleteBlocked"),
                  description: err.message,
                  variant: "destructive",
                });
              } else {
                toast({
                  title: t("common.error"),
                  description: err instanceof Error ? err.message : String(err),
                  variant: "destructive",
                });
              }
            },
          });
        }}
        title={t("training.delete")}
        description={t("training.deleteConfirm")}
        confirmLabel={t("common.confirm")}
        destructive
        loading={deleteMutation.isPending}
      />
    </div>
    </PageTransition>
  );
}
