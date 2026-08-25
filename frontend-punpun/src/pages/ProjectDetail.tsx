import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ClipboardList, Database, FlaskConical, Loader2, PlayCircle, Pencil, Trash2 } from "lucide-react";

import { PageTransition } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConfirmDialog } from "@/components/engine/ConfirmDialog";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { QueueBadge } from "@/components/engine/QueueBadge";
import { StatusBadge } from "@/components/engine/StatusBadge";
import { EvaluationTable } from "@/components/evaluation/EvaluationTable";
import { StartEvaluationDialog } from "@/components/evaluation/StartEvaluationDialog";
import { EditProjectDialog } from "@/components/project/EditProjectDialog";
import { PipelineHub } from "@/components/project-pipeline/PipelineHub";
import {
  queryKeys,
  useDatasets,
  useEvaluations,
  useModels,
  useProject,
  useTrainings,
} from "@/hooks/queries";
import { useToast } from "@/hooks/use-toast";
import { useLanguage } from "@/i18n/LanguageContext";
import { useTaskTypeLabel } from "@/lib/labels";
import { deleteProjectCascade } from "@/lib/projectDelete";

// ProjectActivityTable / ProjectUsageTable are owned by another W3 lane and
// land under src/components/usage/*. Per the task brief this import may show
// as unresolved under `tsc` until that lane merges — expected, not a bug in
// this file. Contract: single prop `projectId: string`.
import { ProjectActivityTable } from "@/components/usage/ProjectActivityTable";
import { ProjectUsageTable } from "@/components/usage/ProjectUsageTable";

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const { t } = useLanguage();
  const { toast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const taskTypeLabel = useTaskTypeLabel();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [editOpen, setEditOpen] = useState(false);

  const { data: project, isLoading, isError, error } = useProject(id ?? "");

  const handleDelete = async () => {
    if (!id) return;
    setDeleting(true);
    try {
      await deleteProjectCascade(id);
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      toast({ title: t("project.delete"), description: project?.name });
      navigate("/projects");
    } catch (err) {
      toast({
        title: t("common.error"),
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
      setDeleting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (isError || !project) {
    return (
      <div className="text-center py-20 space-y-3">
        <p className="text-muted-foreground">{t("projectDetail.notFound")}</p>
        {error && (
          <p className="text-xs text-destructive">{error instanceof Error ? error.message : String(error)}</p>
        )}
        <Button variant="link" asChild><Link to="/projects">{t("projectDetail.backToProjects")}</Link></Button>
      </div>
    );
  }

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
              <Badge variant="outline" className="text-[10px]">{taskTypeLabel(project.task_type)}</Badge>
              <QueueBadge queueState={project.queue_state} queuePosition={project.queue_position} />
            </div>
            <p className="text-sm text-muted-foreground mt-0.5">{project.description}</p>
            {project.external_project_id && (
              <p className="text-[11px] font-mono text-muted-foreground mt-0.5">
                external id: {project.external_project_id}
              </p>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            className="gap-2 shrink-0"
            onClick={() => setEditOpen(true)}
          >
            <Pencil className="h-3.5 w-3.5" /> {t("project.edit")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="gap-2 text-destructive hover:text-destructive shrink-0"
            onClick={() => setDeleteOpen(true)}
          >
            <Trash2 className="h-3.5 w-3.5" /> {t("project.delete")}
          </Button>
        </div>

        <Tabs defaultValue="overview">
          <TabsList>
            <TabsTrigger value="overview">{t("projectDetail.overview")}</TabsTrigger>
            <TabsTrigger value="datasets">{t("project.datasetsTab")}</TabsTrigger>
            <TabsTrigger value="training">{t("projectDetail.training")}</TabsTrigger>
            <TabsTrigger value="evaluation">{t("projectDetail.evaluation")}</TabsTrigger>
            <TabsTrigger value="activity">{t("project.activityTab")}</TabsTrigger>
            <TabsTrigger value="usage">{t("project.usageTab")}</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="space-y-4 mt-4">
            <PipelineHub projectId={project.id} />

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-sm">{t("projectDetail.configuration")}</CardTitle></CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {[
                    [t("projectDetail.taskType"), taskTypeLabel(project.task_type)],
                    [t("projectDetail.created"), new Date(project.created_at).toLocaleString()],
                    [t("projectDetail.lastUpdated"), new Date(project.updated_at).toLocaleString()],
                  ].map(([label, value]) => (
                    <div key={String(label)} className="flex justify-between">
                      <span className="text-muted-foreground">{label}</span>
                      <span className="font-medium text-foreground">{String(value)}</span>
                    </div>
                  ))}
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-sm">{t("project.queue")}</CardTitle></CardHeader>
                <CardContent className="text-sm">
                  {project.queue_state ? (
                    <div className="flex items-center gap-2">
                      <QueueBadge queueState={project.queue_state} queuePosition={project.queue_position} />
                      {project.owner_queue_position != null && (
                        <span className="text-xs text-muted-foreground">
                          (#{project.owner_queue_position} among your projects)
                        </span>
                      )}
                    </div>
                  ) : (
                    <p className="text-muted-foreground">No GPU job in flight for this project.</p>
                  )}
                </CardContent>
              </Card>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Card>
                <CardContent className="p-4 flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground">{t("project.datasetsTab")}</p>
                    <p className="text-xs text-muted-foreground">Seed, SDG, and merged datasets</p>
                  </div>
                  <Button variant="outline" size="sm" asChild>
                    <Link to={`/projects/${project.id}/insights`}>
                      <Database className="h-3.5 w-3.5 mr-1.5" /> View
                    </Link>
                  </Button>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="p-4 flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground">{t("projectDetail.training")}</p>
                    <p className="text-xs text-muted-foreground">Manual and HPO training runs</p>
                  </div>
                  <Button variant="outline" size="sm" asChild>
                    <Link to={`/projects/${project.id}/training`}>
                      <PlayCircle className="h-3.5 w-3.5 mr-1.5" /> View
                    </Link>
                  </Button>
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          <TabsContent value="datasets" className="mt-4">
            <DatasetsPreview projectId={project.id} />
          </TabsContent>

          <TabsContent value="training" className="mt-4">
            <TrainingsPreview projectId={project.id} />
          </TabsContent>

          <TabsContent value="evaluation" className="mt-4">
            <ProjectEvaluations projectId={project.id} />
          </TabsContent>

          <TabsContent value="activity" className="mt-4">
            <ProjectActivityTable projectId={project.id} />
          </TabsContent>

          <TabsContent value="usage" className="mt-4">
            <ProjectUsageTable projectId={project.id} />
          </TabsContent>
        </Tabs>
      </div>

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        onConfirm={handleDelete}
        title={t("project.delete")}
        description={
          <>
            {t("project.deleteConfirm")} {t("project.deleteCancelsJobs")} {t("pipelineHub.deleteDatasetsNote")}{" "}
            {t("project.deleteKeepsModelsNote")}
          </>
        }
        confirmLabel={t("project.delete")}
        destructive
        loading={deleting}
      />

      <EditProjectDialog project={project} open={editOpen} onOpenChange={setEditOpen} />
    </PageTransition>
  );
}

function DatasetsPreview({ projectId }: { projectId: string }) {
  const { data, isLoading, isError, error } = useDatasets(projectId, { limit: 5 });
  const items = data?.items ?? [];

  if (isLoading) {
    return (
      <div className="flex justify-center py-10">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (isError) {
    return <ErrorDetail error={{ detail: error instanceof Error ? error.message : String(error) }} />;
  }

  if (items.length === 0) {
    return (
      <EngineEmptyState
        icon={Database}
        title="No datasets yet"
        hint="Upload a seed dataset or run SDG generation to get started."
        action={
          <Button variant="outline" size="sm" asChild>
            <Link to={`/projects/${projectId}/insights`}>Open Datasets</Link>
          </Button>
        }
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {items.map((d) => (
          <Card key={d.id}>
            <CardContent className="p-3 space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium text-foreground truncate">{d.name}</p>
                <StatusBadge status={d.status} />
              </div>
              <p className="text-xs text-muted-foreground">{d.source} · {d.num_samples} samples</p>
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="text-right">
        <Button variant="link" size="sm" asChild>
          <Link to={`/projects/${projectId}/insights`}>View all datasets →</Link>
        </Button>
      </div>
    </div>
  );
}

/** Restores the original's `evaluation` tab, now backed by the real evaluation
 *  stage: evaluations are keyed by model artifact, so scope them to this
 *  project by way of its model artifacts. */
function ProjectEvaluations({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const [evalOpen, setEvalOpen] = useState(false);
  const { data: modelsPage, isLoading: modelsLoading } = useModels(projectId, { limit: 100 });
  const { data: evalsPage, isLoading: evalsLoading } = useEvaluations({ limit: 100 });

  if (modelsLoading || evalsLoading) {
    return (
      <div className="flex justify-center py-10">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const models = modelsPage?.items ?? [];
  const modelIds = new Set(models.map((m) => m.id));
  const modelNames = Object.fromEntries(models.map((m) => [m.id, m.name]));
  const rows = (evalsPage?.items ?? []).filter((e) => modelIds.has(e.model_artifact_id));

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => setEvalOpen(true)} className="gap-2">
          <FlaskConical className="h-3.5 w-3.5" /> {t("eval.start")}
        </Button>
      </div>

      {rows.length === 0 ? (
        <EngineEmptyState
          icon={ClipboardList}
          title="No evaluations yet"
          hint="Export a model, then evaluation runs automatically and results appear on that model's detail page."
          action={
            <Button variant="outline" size="sm" onClick={() => setEvalOpen(true)} className="gap-2">
              <FlaskConical className="h-3.5 w-3.5" /> {t("eval.start")}
            </Button>
          }
        />
      ) : (
        <EvaluationTable
          evaluations={rows}
          modelNames={modelNames}
          onRowClick={(evaluation) => navigate(`/models/${evaluation.model_artifact_id}`)}
        />
      )}

      <StartEvaluationDialog open={evalOpen} onOpenChange={setEvalOpen} />
    </div>
  );
}

function TrainingsPreview({ projectId }: { projectId: string }) {
  const { data, isLoading, isError, error } = useTrainings({ project_id: projectId, limit: 5 });
  const items = data?.items ?? [];

  if (isLoading) {
    return (
      <div className="flex justify-center py-10">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (isError) {
    return <ErrorDetail error={{ detail: error instanceof Error ? error.message : String(error) }} />;
  }

  if (items.length === 0) {
    return (
      <EngineEmptyState
        icon={PlayCircle}
        title="No training runs yet"
        hint="Start a training run once you have a dataset ready."
        action={
          <Button variant="outline" size="sm" asChild>
            <Link to={`/projects/${projectId}/training`}>Open Trainings</Link>
          </Button>
        }
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {items.map((tr) => (
          <Card key={tr.id}>
            <CardContent className="p-3 space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium text-foreground truncate">
                  {tr.training_name || tr.base_model}
                </p>
                <StatusBadge status={tr.status} />
              </div>
              <p className="text-xs text-muted-foreground">
                {tr.mode === "hpo" ? "HPO" : "Manual"} · {tr.base_model}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="text-right">
        <Button variant="link" size="sm" asChild>
          <Link to={`/projects/${projectId}/training`}>View training monitor →</Link>
        </Button>
      </div>
    </div>
  );
}
