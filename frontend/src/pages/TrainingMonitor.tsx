import { useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { PageTransition, StaggerContainer, StaggerItem } from "@/components/motion";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ArrowLeft, Clock, Cpu, Database, Gauge } from "lucide-react";
import { PipelineSteps } from "@/components/training/PipelineSteps";
import { TrainingLog } from "@/components/training/TrainingLog";
import { LossCurveChart } from "@/components/training/LossCurveChart";
import { EvaluationViewer } from "@/components/training/EvaluationViewer";
import { mockPipelineSteps, mockTrainingLog, mockLossCurve, mockComparisonResults } from "@/data/trainingMockData";
import { baseModelLabels, taskTypeLabels } from "@/data/mockData";
import { TrainingMonitorSkeleton } from "@/components/skeletons/TrainingMonitorSkeleton";
import { DiagnosticPanel } from "@/components/training/DiagnosticPanel";
import { useLanguage } from "@/i18n/LanguageContext";
import { useProject } from "@/hooks/useProjects";
import { useTrainingWebSocket } from "@/hooks/useTrainingWebSocket";
import { engineGetLossHistory } from "@/lib/engineApi";
import { getEngineMeta } from "@/lib/engineStore";

export default function TrainingMonitor() {
  const { id } = useParams<{ id: string }>();
  const { project } = useProject(id);
  const [loading, setLoading] = useState(true);
  const { t } = useLanguage();

  const engineMeta = id ? getEngineMeta(id) : null;
  const { latestProgress } = useTrainingWebSocket(
    project?.status === "training" ? (engineMeta?.jobId ?? null) : null,
  );

  // Real loss curve from engine — refreshed every 10 s while training
  const [realLossCurve, setRealLossCurve] = useState<typeof mockLossCurve | null>(null);
  useEffect(() => {
    if (!engineMeta?.trainingId) return;
    let active = true;
    const fetchLoss = async () => {
      try {
        const hist = await engineGetLossHistory(engineMeta.trainingId!);
        if (!active) return;
        if (hist.train_loss.length > 0) {
          const points = hist.train_loss.map((p, i) => ({
            step: p.step,
            trainLoss: p.value,
            valLoss: hist.eval_loss[i]?.value ?? null,
          }));
          setRealLossCurve(points);
        }
      } catch {
        // Engine unreachable — keep using mock data
      }
    };
    void fetchLoss();
    const interval = project?.status === "training" ? setInterval(fetchLoss, 10000) : null;
    return () => {
      active = false;
      if (interval) clearInterval(interval);
    };
  }, [engineMeta?.trainingId, project?.status]);

  const lossCurveData = realLossCurve ?? mockLossCurve;
  const currentTrainLoss = latestProgress?.train_loss
    ?? (realLossCurve?.at(-1)?.trainLoss ?? 0.485);
  const currentValLoss = latestProgress?.eval_loss
    ?? (realLossCurve?.at(-1)?.valLoss ?? 0.471);

  useEffect(() => {
    const timer = setTimeout(() => setLoading(false), 1500);
    return () => clearTimeout(timer);
  }, []);

  if (!project) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">{t("projectDetail.notFound")}</p>
        <Button variant="link" asChild><Link to="/projects">{t("projectDetail.backToProjects")}</Link></Button>
      </div>
    );
  }

  if (loading) return <TrainingMonitorSkeleton />;

  const isTraining = project.status === "training";

  return (
    <PageTransition>
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" asChild>
          <Link to={`/projects/${id}`}><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-foreground">{project.name}</h1>
            <Badge variant={isTraining ? "secondary" : "default"}>
              {isTraining ? "Training" : project.status}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">{t("training.title")}</p>
        </div>
      </div>

      <StaggerContainer className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: t("training.baseModel"), value: baseModelLabels[project.baseModel], icon: Cpu },
          { label: t("training.taskType"), value: taskTypeLabels[project.taskType], icon: Database },
          { label: t("training.epochProgress"), value: latestProgress ? `${Math.round(latestProgress.epoch)} / ${latestProgress.epochs_total}` : isTraining ? "… / …" : `${project.epochs} / ${project.epochs}`, icon: Gauge },
          { label: t("training.elapsedTime"), value: isTraining ? "Live" : "Done", icon: Clock },
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

      {isTraining && (
        <Card>
          <CardContent className="p-4 space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">{t("training.overallProgress")}</span>
              <span className="font-semibold text-foreground">{project.progress}%</span>
            </div>
            <Progress value={project.progress} className="h-2.5" />
            <p className="text-[10px] text-muted-foreground">{t("training.estimatedCompletion")}</p>
          </CardContent>
        </Card>
      )}

      <DiagnosticPanel projectStatus={project.status} />

      <Tabs defaultValue="pipeline">
        <TabsList>
          <TabsTrigger value="pipeline">{t("training.pipeline")}</TabsTrigger>
          <TabsTrigger value="loss">{t("training.lossCurve")}</TabsTrigger>
          <TabsTrigger value="logs">{t("training.trainingLog")}</TabsTrigger>
          <TabsTrigger value="evaluation">{t("training.evaluation")}</TabsTrigger>
        </TabsList>

        <TabsContent value="pipeline" className="mt-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t("training.trainingPipeline")}</CardTitle>
            </CardHeader>
            <CardContent>
              <PipelineSteps steps={mockPipelineSteps} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="loss" className="mt-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">{t("training.lossCurve")}</CardTitle>
                <div className="flex gap-3 text-[10px] text-muted-foreground">
                  <span>Current train loss: <span className="font-bold text-foreground">{currentTrainLoss.toFixed(3)}</span></span>
                  <span>Current val loss: <span className="font-bold text-foreground">{currentValLoss.toFixed(3)}</span></span>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <LossCurveChart data={lossCurveData} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="logs" className="mt-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">{t("training.trainingLog")}</CardTitle>
                <Badge variant="outline" className="text-[10px]">{mockTrainingLog.length} entries</Badge>
              </div>
            </CardHeader>
            <CardContent>
              <TrainingLog logs={mockTrainingLog} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="evaluation" className="mt-4">
          <EvaluationViewer comparisons={mockComparisonResults} />
        </TabsContent>
      </Tabs>
    </div>
    </PageTransition>
  );
}
