import { useParams, Link } from "react-router-dom";
import { useEffect, useRef } from "react";
import { PageTransition } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ArrowLeft, RotateCcw, Wand2, Loader2, CheckCircle2, AlertCircle, Activity } from "lucide-react";
import { getBaseModelLabel, taskTypeLabels } from "@/data/mockData";
import { mockVersionHistory } from "@/data/deploymentMockData";
import { TuningReport } from "@/components/training/TuningReport";
import { TuningHistory } from "@/components/training/TuningHistory";
import { getLatestTuningRun } from "@/lib/tuningGenerator";
import { useProject } from "@/hooks/useProjects";
import { useEngineWorkflowSync } from "@/hooks/useEngineWorkflowSync";
import type { ProjectStatus } from "@/types";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import { getEngineMeta } from "@/lib/engineStore";

const statusVariant: Record<ProjectStatus, "default" | "secondary" | "destructive" | "outline"> = {
  completed: "default",
  training: "secondary",
  queued: "outline",
  paused: "outline",
  failed: "destructive",
};

function getSuggestions(datasetSize: number) {
  if (datasetSize < 1000) return { lr: 1e-4, epochs: 10, batch: 8, label: "small" };
  if (datasetSize <= 5000) return { lr: 2e-4, epochs: 5, batch: 16, label: "medium" };
  return { lr: 3e-4, epochs: 3, batch: 32, label: "large" };
}

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const { project, loading, setProject } = useProject(id);
  const { t } = useLanguage();
  const { toast } = useToast();
  const completionToastedRef = useRef(false);

  // Synchronizes only real Engine/Supabase state; no simulated progress.
  const { retryExport } = useEngineWorkflowSync(project, setProject);

  // Notify once when training completes
  useEffect(() => {
    if (project?.status === "completed" && !completionToastedRef.current) {
      completionToastedRef.current = true;
      toast({ title: t("training.completedTitle"), description: project.name });
    }
  }, [project?.status, project?.name, toast, t]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">{t("projectDetail.notFound")}</p>
        <Button variant="link" asChild><Link to="/projects">{t("projectDetail.backToProjects")}</Link></Button>
      </div>
    );
  }

  const versions = mockVersionHistory[project.id as keyof typeof mockVersionHistory] || [];
  const suggestion = getSuggestions(project.datasetSize);
  const engineMeta = getEngineMeta(project.id);

  const handleRollback = (version: string) => {
    toast({ title: t("versions.rolledBack"), description: `→ ${version}` });
  };

  return (
    <PageTransition>
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" asChild>
          <Link to="/projects"><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-foreground">{project.name}</h1>
            <Badge variant={statusVariant[project.status]}>{project.status}</Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">{project.description}</p>
        </div>
      </div>

      {/* Live training status banner — visible on every tab while job runs */}
      {(project.status === "queued" || project.status === "training" || project.status === "completed" || project.status === "failed") && (
        <LiveStatusBanner project={project} />
      )}

      {project.status === "failed" && engineMeta?.error && (
        <Card className="border-destructive/40 bg-destructive/5" role="alert">
          <CardContent className="p-4 flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-destructive">Engine workflow failed</p>
              <p className="text-xs text-muted-foreground mt-1 break-words">{engineMeta.error}</p>
            </div>
          </CardContent>
        </Card>
      )}

      {engineMeta?.phase === "export_failed" && engineMeta.error && (
        <Card className="border-destructive/40 bg-destructive/5" role="alert">
          <CardContent className="p-4 flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-sm font-semibold text-destructive">Model export failed</p>
              <p className="text-xs text-muted-foreground mt-1 break-words">{engineMeta.error}</p>
            </div>
            <Button size="sm" variant="outline" onClick={retryExport}>Retry export</Button>
          </CardContent>
        </Card>
      )}

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">{t("projectDetail.overview")}</TabsTrigger>
          <TabsTrigger value="training">{t("projectDetail.training")}</TabsTrigger>
          <TabsTrigger value="evaluation">{t("projectDetail.evaluation")}</TabsTrigger>
          <TabsTrigger value="versions">{t("versions.title")}</TabsTrigger>
          <TabsTrigger value="tuning">{t("projectDetail.autoTuning")}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4 mt-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">{t("projectDetail.configuration")}</CardTitle></CardHeader>
              <CardContent className="space-y-2 text-sm">
                {[
                  [t("projectDetail.taskType"), taskTypeLabels[project.taskType]],
                  [t("projectDetail.baseModel"), getBaseModelLabel(project.baseModel)],
                  [t("projectDetail.epochs"), project.epochs],
                  [t("projectDetail.learningRate"), project.learningRate],
                  [t("projectDetail.datasetSize"), `${project.datasetSize} ${t("calc.samples")}`],
                ].map(([label, value]) => (
                  <div key={String(label)} className="flex justify-between">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="font-medium text-foreground">{String(value)}</span>
                  </div>
                ))}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">{t("projectDetail.status")}</CardTitle></CardHeader>
              <CardContent className="space-y-2 text-sm">
                {[
                  [t("projectDetail.created"), new Date(project.createdAt).toLocaleString()],
                  [t("projectDetail.lastUpdated"), new Date(project.updatedAt).toLocaleString()],
                  [t("projectDetail.creditsUsed"), project.creditsCost],
                  [t("projectDetail.progress"), `${project.progress}%`],
                ].map(([label, value]) => (
                  <div key={String(label)} className="flex justify-between">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="font-medium text-foreground">{String(value)}</span>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          {/* Tuning Suggestions */}
          <Card className="border-primary/20 bg-accent/30">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Wand2 className="h-4 w-4 text-primary" /> {t("tuning.suggestions")}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-3">
                {t("tuning.datasetLabel")}: {suggestion.label} ({project.datasetSize} {t("calc.samples")})
              </p>
              <div className="grid grid-cols-3 gap-3">
                {[
                  { label: "Learning Rate", value: suggestion.lr, current: project.learningRate },
                  { label: "Epochs", value: suggestion.epochs, current: project.epochs },
                  { label: "Batch Size", value: suggestion.batch, current: "—" },
                ].map((s) => (
                  <div key={s.label} className="text-center p-2 rounded-lg bg-background border border-border">
                    <p className="text-xs text-muted-foreground">{s.label}</p>
                    <p className="text-sm font-bold text-primary">{s.value}</p>
                    <p className="text-[10px] text-muted-foreground">{t("tuning.current")}: {s.current}</p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="training" className="space-y-4 mt-4">
          {project.status === "training" && (
            <Card>
              <CardContent className="p-5 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">{t("projectDetail.trainingProgress")}</span>
                  <span className="font-medium text-foreground">{project.progress}%</span>
                </div>
                <Progress value={project.progress} className="h-2" />
              </CardContent>
            </Card>
          )}
          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-sm">{t("projectDetail.lossCurve")}</CardTitle></CardHeader>
            <CardContent className="py-8 text-center">
              <p className="text-sm text-muted-foreground mb-3">Loss metrics are loaded from the Engine on the training monitor.</p>
              <Button variant="outline" asChild><Link to={`/projects/${project.id}/training`}>{t("training.title")}</Link></Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="evaluation" className="space-y-4 mt-4">
          <div className="text-center py-12 text-muted-foreground text-sm">
            Evaluation is not connected in this workflow. No simulated metrics are shown.
          </div>
        </TabsContent>

        <TabsContent value="versions" className="space-y-4 mt-4">
          {versions.length > 0 ? (
            <div className="space-y-3">
              {versions.map((v) => (
                <Card key={v.version} className={v.current ? "border-primary/30" : ""}>
                  <CardContent className="p-4">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-foreground">{v.version}</span>
                        {v.current && <Badge>{t("versions.current")}</Badge>}
                      </div>
                      {!v.current && (
                        <Button variant="outline" size="sm" className="gap-1.5" onClick={() => handleRollback(v.version)}>
                          <RotateCcw className="h-3 w-3" /> {t("versions.rollback")}
                        </Button>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground mb-2">{new Date(v.date).toLocaleString()}</p>
                    <p className="text-xs text-muted-foreground italic mb-2">{v.notes}</p>
                    <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs">
                      {[
                        ["Epochs", v.epochs],
                        ["LR", v.learningRate],
                        ["Batch", v.batchSize],
                        ["Accuracy", v.accuracy ? `${v.accuracy}%` : "—"],
                        ["F1", v.f1Score ? `${v.f1Score}%` : "—"],
                      ].map(([label, val]) => (
                        <div key={String(label)} className="text-center p-1.5 rounded bg-muted">
                          <p className="text-muted-foreground">{label}</p>
                          <p className="font-medium text-foreground">{String(val)}</p>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <div className="text-center py-12 text-muted-foreground text-sm">
              {t("versions.noVersions")}
            </div>
          )}
        </TabsContent>

        <TabsContent value="tuning" className="space-y-4 mt-4">
          <Tabs defaultValue="latest">
            <TabsList>
              <TabsTrigger value="latest">{t("tuningHistory.latestRun")}</TabsTrigger>
              <TabsTrigger value="history">{t("tuningHistory.title")}</TabsTrigger>
            </TabsList>
            <TabsContent value="latest" className="mt-4">
              {(() => {
                const latest = getLatestTuningRun(project);
                if (!latest) {
                  return (
                    <div className="text-center py-12 text-muted-foreground text-sm">
                      {t("tuningHistory.empty")}
                    </div>
                  );
                }
                return <TuningReport report={latest.report} />;
              })()}
            </TabsContent>
            <TabsContent value="history" className="mt-4">
              <TuningHistory project={project} />
            </TabsContent>
          </Tabs>
        </TabsContent>
      </Tabs>
    </div>
    </PageTransition>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// LiveStatusBanner — shows queued / training (with progress + eta + epoch) /
// completed / failed states. Auto-updates as the simulator pushes new state.
// ─────────────────────────────────────────────────────────────────────────────
function LiveStatusBanner({ project }: { project: import("@/types").Project }) {
  const { t } = useLanguage();
  const status = project.status;

  if (status === "completed") {
    return (
      <Card className="border-primary/30 bg-primary/5">
        <CardContent className="p-4 flex items-center gap-3">
          <CheckCircle2 className="h-5 w-5 text-primary shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-foreground">{t("training.completedTitle")}</p>
            <p className="text-xs text-muted-foreground">{t("training.completedDesc")}</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (status === "failed") {
    return (
      <Card className="border-destructive/30 bg-destructive/5">
        <CardContent className="p-4 flex items-center gap-3">
          <AlertCircle className="h-5 w-5 text-destructive shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-foreground">{t("training.failedTitle")}</p>
            <p className="text-xs text-muted-foreground">{t("training.failedDesc")}</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  // queued or training
  const isQueued = status === "queued";
  const progress = isQueued ? 0 : project.progress;
  const currentEpoch = Math.max(1, Math.ceil((progress / 100) * project.epochs));
  const remainingPct = Math.max(0, 100 - progress);
  // Heuristic ETA: simulator advances ~5%/2s → ~24s/100% baseline scaled by epochs
  const etaSeconds = Math.round((remainingPct / 5) * 2 * Math.max(1, project.epochs / 5));
  const etaLabel = etaSeconds > 60 ? `~${Math.ceil(etaSeconds / 60)}m` : `~${etaSeconds}s`;

  return (
    <Card className="border-primary/30 bg-primary/5">
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center gap-3">
          {isQueued ? (
            <Loader2 className="h-5 w-5 text-primary animate-spin shrink-0" />
          ) : (
            <Activity className="h-5 w-5 text-primary shrink-0 animate-pulse" />
          )}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-foreground">
              {isQueued ? t("training.queuedTitle") : t("training.runningTitle")}
            </p>
            <p className="text-xs text-muted-foreground">
              {isQueued
                ? t("training.queuedDesc")
                : `${t("training.epoch")} ${currentEpoch}/${project.epochs} · ${t("training.eta")} ${etaLabel}`}
            </p>
          </div>
          <span className="text-sm font-semibold text-foreground tabular-nums shrink-0">{progress}%</span>
        </div>
        <Progress value={progress} className="h-2" />
      </CardContent>
    </Card>
  );
}
