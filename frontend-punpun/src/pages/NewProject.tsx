import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ArrowLeft, ArrowRight, Check, Sparkles, Loader2 } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { TaskPromptStep } from "@/components/new-project/TaskPromptStep";
import { TaskSelectionStep } from "@/components/new-project/TaskSelectionStep";
import { DataUploadStep } from "@/components/new-project/DataUploadStep";
import { ModelSelectionStep, TRAINING_NAME_PATTERN } from "@/components/new-project/ModelSelectionStep";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { ApiError } from "@/api/client";
import { useCreateProject, useStartTraining } from "@/hooks/queries";
import type { JobStatus, Project, TaskType } from "@/api/types";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";

export interface ProjectFormData {
  projectName: string;
  taskPrompt: string;
  taskType: TaskType | null;
  baseModel: string | null;
  maxSeqLength: number | null;
  /** Ollama-tag-safe name for this training run; sent as training_name.
   *  Prefilled once from the project name, then user-editable. */
  trainingName: string;
  /** Set once the seed file has been uploaded via useUploadSeedDataset. */
  seedDatasetId: string | null;
  /** The dataset actually used for training — the SDG job's target dataset,
   *  produced via useGenerateDataset in with_seed mode. */
  trainingDatasetId: string | null;
  trainingDatasetStatus: JobStatus | null;
}

const initialFormData: ProjectFormData = {
  projectName: "",
  taskPrompt: "",
  taskType: null,
  baseModel: null,
  maxSeqLength: null,
  trainingName: "",
  seedDatasetId: null,
  trainingDatasetId: null,
  trainingDatasetStatus: null,
};

/** Normalizes a caught error into the ApiErrorLike shape ErrorDetail expects. */
export function toErrorDetail(err: unknown): { detail: string; code?: string | null } {
  if (err instanceof ApiError) return { detail: err.message, code: err.code };
  return { detail: err instanceof Error ? err.message : String(err) };
}

export default function NewProject() {
  const [currentStep, setCurrentStep] = useState(0);
  const [formData, setFormData] = useState<ProjectFormData>(initialFormData);
  const [engineProject, setEngineProject] = useState<Project | null>(null);
  const [projectError, setProjectError] = useState<{ detail: string; code?: string | null } | null>(null);
  const [launchError, setLaunchError] = useState<{ detail: string; code?: string | null } | null>(null);
  const [launching, setLaunching] = useState(false);
  const { t } = useLanguage();
  const { toast } = useToast();
  const navigate = useNavigate();

  const createProjectMutation = useCreateProject();
  const startTrainingMutation = useStartTraining();

  const updateForm = (partial: Partial<ProjectFormData>) => {
    setFormData((prev) => {
      const next = { ...prev, ...partial };
      // Task type is immutable on the Engine project once created — if it
      // changes, every downstream selection (project, datasets, model) is
      // invalidated so the next "Next" click creates a fresh project.
      if (partial.taskType !== undefined && partial.taskType !== prev.taskType) {
        next.seedDatasetId = null;
        next.trainingDatasetId = null;
        next.trainingDatasetStatus = null;
        next.baseModel = null;
        next.maxSeqLength = null;
      }
      return next;
    });
    if (partial.taskType !== undefined) {
      setEngineProject(null);
      setProjectError(null);
    }
  };

  const steps = [
    { id: "prompt", label: t("newProject.taskPrompt") },
    { id: "task", label: t("newProject.taskType") },
    { id: "data", label: t("newProject.uploadData") },
    { id: "model", label: t("newProject.baseModel") },
  ];

  const canProceed = () => {
    switch (currentStep) {
      case 0:
        return formData.taskPrompt.trim().length > 10;
      case 1:
        return formData.taskType !== null;
      case 2:
        return formData.trainingDatasetId !== null && formData.trainingDatasetStatus === "completed";
      case 3:
        return formData.baseModel !== null && TRAINING_NAME_PATTERN.test(formData.trainingName.trim());
      default:
        return false;
    }
  };

  const isLastStep = currentStep === steps.length - 1;

  const handleNext = async () => {
    // Leaving the task-selection step is what actually creates the Engine
    // project — it's the first point we have every field ProjectCreate needs.
    if (currentStep === 1 && !engineProject) {
      setProjectError(null);
      try {
        const created = await createProjectMutation.mutateAsync({
          name: formData.projectName.trim() || formData.taskPrompt.slice(0, 60) || "Untitled Project",
          description: formData.taskPrompt,
          task_type: formData.taskType!,
        });
        setEngineProject(created);
      } catch (e) {
        setProjectError(toErrorDetail(e));
        return;
      }
    }
    setCurrentStep((s) => s + 1);
  };

  const handleLaunch = async () => {
    if (launching || !engineProject || !formData.trainingDatasetId || !formData.baseModel) return;

    setLaunching(true);
    setLaunchError(null);
    try {
      await startTrainingMutation.mutateAsync({
        project_id: engineProject.id,
        dataset_id: formData.trainingDatasetId,
        base_model: formData.baseModel,
        training_name: formData.trainingName.trim(),
        mode: "manual",
        manual_config: formData.maxSeqLength ? { max_seq_length: formData.maxSeqLength } : undefined,
      });
      toast({ title: t("newProject.launched"), description: engineProject.name });
      navigate(`/projects/${engineProject.id}`);
    } catch (e) {
      const detail = toErrorDetail(e);
      setLaunchError(detail);
      toast({ title: t("newProject.launchFailed"), description: detail.detail, variant: "destructive" });
      setLaunching(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/projects" aria-label={t("common.back")}><ArrowLeft className="h-4 w-4" /></Link>
          </Button>
          <div>
            <h1 className="text-xl font-bold text-foreground">{t("newProject.title")}</h1>
            <p className="text-sm text-muted-foreground">{t("newProject.subtitle")}</p>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1">
        {steps.map((step, i) => (
          <div key={step.id} className="flex items-center flex-1">
            <button
              onClick={() => i <= currentStep && setCurrentStep(i)}
              className={`flex items-center gap-2 text-xs font-medium px-3 py-2 rounded-md transition-colors w-full ${
                i === currentStep
                  ? "bg-primary text-primary-foreground"
                  : i < currentStep
                  ? "bg-accent text-accent-foreground cursor-pointer"
                  : "bg-secondary text-muted-foreground"
              }`}
            >
              <span className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold border border-current/30 shrink-0">
                {i < currentStep ? <Check className="h-3 w-3" /> : i + 1}
              </span>
              <span className="hidden sm:inline truncate">{step.label}</span>
            </button>
            {i < steps.length - 1 && <div className="w-2 shrink-0" />}
          </div>
        ))}
      </div>

      <Card>
        <CardContent className="p-6">
          {currentStep === 0 && <TaskPromptStep formData={formData} updateForm={updateForm} />}
          {currentStep === 1 && <TaskSelectionStep formData={formData} updateForm={updateForm} />}
          {currentStep === 2 && (
            <DataUploadStep formData={formData} updateForm={updateForm} projectId={engineProject?.id ?? null} />
          )}
          {currentStep === 3 && <ModelSelectionStep formData={formData} updateForm={updateForm} />}
        </CardContent>
      </Card>

      {currentStep === 1 && projectError && <ErrorDetail error={projectError} />}
      {isLastStep && launchError && <ErrorDetail error={launchError} />}

      <div className="flex justify-between">
        <Button
          variant="outline"
          onClick={() => setCurrentStep((s) => s - 1)}
          disabled={currentStep === 0 || launching}
          className="gap-2"
        >
          <ArrowLeft className="h-4 w-4" /> {t("common.back")}
        </Button>
        {!isLastStep ? (
          <Button onClick={handleNext} disabled={!canProceed() || createProjectMutation.isPending} className="gap-2">
            {createProjectMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("common.next")} <ArrowRight className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            className="gap-2"
            onClick={handleLaunch}
            disabled={launching || !canProceed()}
            aria-label={t("newProject.launchTraining")}
          >
            {launching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            {t("newProject.launchTraining")}
          </Button>
        )}
      </div>
    </div>
  );
}
