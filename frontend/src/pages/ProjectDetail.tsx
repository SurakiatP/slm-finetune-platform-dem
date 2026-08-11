import { useParams, Link } from "react-router-dom";
import { useEffect, useRef } from "react";
import { PageTransition } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ArrowLeft, RotateCcw, Wand2, Loader2, CheckCircle2, AlertCircle, Activity } from "lucide-react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { taskTypeLabels, baseModelLabels } from "@/data/mockData";
import { mockVersionHistory } from "@/data/deploymentMockData";
import { TuningReport } from "@/components/training/TuningReport";
import { TuningHistory } from "@/components/training/TuningHistory";
import { getLatestTuningRun } from "@/lib/tuningGenerator";
import { useProject } from "@/hooks/useProjects";
import { useTrainingSimulator } from "@/hooks/useTrainingSimulator";
import type { ProjectStatus } from "@/types";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";

const statusVariant: Record<ProjectStatus, "default" | "secondary" | "destructive" | "outline"> = {
  completed: "default",
  training: "secondary",
  queued: "outline",
  paused: "outline",
  failed: "destructive",
};

const lossCurve = Array.from({ length: 50 }, (_, i) => ({
  step: (i + 1) * 20,
  loss: 2.5 * Math.exp(-i * 0.06) + 0.15 + Math.random() * 0.08,
}));

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

  // Live training simulator — drives status & progress for prototype projects
  useTrainingSimulator(project, setProject);

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

  // Generate metrics deterministically from project (in lieu of real eval data per project)
  const seed = project.id.split("").reduce((s, c) => s + c.charCodeAt(0), 0);
  const r = (offset: number) => 85 + ((seed + offset) % 12);
  const metrics = {
    accuracy: r(1),
    f1Score: r(2),
    precision: r(3),
    recall: r(4),
    rouge1: r(5),
    latencyMs: 100 + (seed % 80),
  };
  const versions = mockVersionHistory[project.id as keyof typeof mockVersionHistory] || [];
  const suggestion = getSuggestions(project.datasetSize);

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
                  [t("projectDetail.baseModel"), baseModelLabels[project.baseModel]],
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
            <CardContent>
              <div className="h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={lossCurve}>
                    <defs>
                      <linearGradient id="lossGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="hsl(243,75%,59%)" stopOpacity={0.2} />
                        <stop offset="95%" stopColor="hsl(243,75%,59%)" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                    <XAxis dataKey="step" tick={{ fill: "hsl(220,9%,46%)", fontSize: 11 }} />
                    <YAxis tick={{ fill: "hsl(220,9%,46%)", fontSize: 11 }} />
                    <Tooltip contentStyle={{ backgroundColor: "hsl(0,0%,100%)", border: "1px solid hsl(220,13%,91%)", borderRadius: "8px", fontSize: 12 }} />
                    <Area type="monotone" dataKey="loss" stroke="hsl(243,75%,59%)" fill="url(#lossGrad)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="evaluation" className="space-y-4 mt-4">
          {metrics ? (
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">{t("projectDetail.evalMetrics")}</CardTitle></CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                  {[
                    ["Accuracy", `${metrics.accuracy}%`],
                    ["F1 Score", `${metrics.f1Score}%`],
                    ["Precision", `${metrics.precision}%`],
                    ["Recall", `${metrics.recall}%`],
                    ...(metrics.rouge1 > 0 ? [["ROUGE-1", `${metrics.rouge1}%`]] : []),
                    ["Latency", `${metrics.latencyMs}ms`],
                  ].map(([label, value]) => (
                    <div key={String(label)} className="text-center p-3 rounded-lg bg-accent">
                      <p className="text-xl font-bold text-foreground">{value}</p>
                      <p className="text-xs text-muted-foreground">{label}</p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ) : (
            <div className="text-center py-12 text-muted-foreground text-sm">
              {t("projectDetail.evalPending")}
            </div>
          )}
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
