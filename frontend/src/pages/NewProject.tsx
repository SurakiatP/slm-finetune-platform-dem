import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ArrowLeft, ArrowRight, Check, Sparkles, Loader2 } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { TaskPromptStep } from "@/components/new-project/TaskPromptStep";
import { TaskSelectionStep } from "@/components/new-project/TaskSelectionStep";
import { DataUploadStep } from "@/components/new-project/DataUploadStep";
import { ModelSelectionStep } from "@/components/new-project/ModelSelectionStep";
import { TemplateLibrary } from "@/components/new-project/TemplateLibrary";
import type { TaskType, BaseModel } from "@/types";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import { createProject, updateProject } from "@/lib/projectsApi";
import { validatePreflight } from "@/lib/trainingValidation";
import {
  engineCreateProject,
  engineUploadSeed,
  engineGenerateDataset,
  engineStartTraining,
  engineGetDataset,
} from "@/lib/engineApi";
import { setEngineMeta, patchEngineMeta } from "@/lib/engineStore";
import { TASK_TYPE_TO_ENGINE, BASE_MODEL_TO_ENGINE, buildManualConfig } from "@/lib/engineMappings";

export interface ProjectFormData {
  projectName: string;
  taskPrompt: string;
  taskType: TaskType | null;
  baseModel: BaseModel | null;
  files: File[];
}

const initialFormData: ProjectFormData = {
  projectName: "",
  taskPrompt: "",
  taskType: null,
  baseModel: null,
  files: [],
};

// Auto-tuning heuristic based on dataset size (rows)
function autoTuneParams(datasetRows: number) {
  if (datasetRows < 1000) return { epochs: 10, learningRate: 1e-4, batchSize: 8 };
  if (datasetRows <= 5000) return { epochs: 5, learningRate: 2e-4, batchSize: 16 };
  return { epochs: 3, learningRate: 3e-4, batchSize: 32 };
}

async function pollDatasetReady(datasetId: string, maxAttempts = 60, intervalMs = 5000): Promise<string> {
  for (let i = 0; i < maxAttempts; i++) {
    const ds = await engineGetDataset(datasetId);
    if (ds.status === "ready" || ds.status === "completed") return datasetId;
    if (ds.status === "failed") throw new Error("Dataset generation failed");
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error("Dataset generation timed out");
}

// Runs in background after navigation — calls Engine API chain independently of UI
async function runEngineFlow(
  supabaseProjectId: string,
  engineProjectId: string,
  taskType: TaskType,
  taskDescription: string,
  seedFile: File | null,
  epochs: number,
  learningRate: number,
  baseModel: BaseModel,
  projectName: string,
) {
  const engineTaskType = TASK_TYPE_TO_ENGINE[taskType];
  const engineBaseModel = BASE_MODEL_TO_ENGINE[baseModel];
  if (!engineTaskType) return; // task type not supported by engine

  try {
    let trainDatasetId: string | null = null;

    if (seedFile) {
      // Node 3a — upload seed
      const seedResult = await engineUploadSeed(seedFile, engineProjectId, engineTaskType, "seed");
      patchEngineMeta(supabaseProjectId, { seedDatasetId: seedResult.dataset_id });

      // Node 4 — generate synthetic dataset
      const sdgResult = await engineGenerateDataset(
        engineProjectId,
        engineTaskType,
        taskDescription,
        seedResult.dataset_id,
        200,
        50,
      );
      patchEngineMeta(supabaseProjectId, { sdgJobId: sdgResult.job_id });

      // Poll until SDG dataset is ready
      trainDatasetId = await pollDatasetReady(sdgResult.dataset_id);
      patchEngineMeta(supabaseProjectId, { trainDatasetId });
    }

    if (!trainDatasetId) return; // no dataset → cannot train

    // Node 6 — start training
    const config = buildManualConfig(epochs, learningRate);
    const trainingResult = await engineStartTraining(
      engineProjectId,
      trainDatasetId,
      engineBaseModel,
      projectName,
      config,
    );
    patchEngineMeta(supabaseProjectId, {
      trainingId: trainingResult.training_id,
      jobId: trainingResult.job_id,
    });

    // Sync Supabase project status to training
    await updateProject(supabaseProjectId, { status: "training", progress: 0 });
  } catch {
    // Background failures are silent — the simulator fallback continues
  }
}

export default function NewProject() {
  const [currentStep, setCurrentStep] = useState(0);
  const [formData, setFormData] = useState<ProjectFormData>(initialFormData);
  const [showTemplates, setShowTemplates] = useState(false);
  const [launching, setLaunching] = useState(false);
  const { t } = useLanguage();
  const { toast } = useToast();
  const navigate = useNavigate();

  const handleLaunch = async () => {
    if (launching) return;

    // Pre-flight validation — block launch on errors, surface warnings as info toasts
    const result = validatePreflight(formData, t);
    if (!result.ok) {
      // Show up to 3 errors so the toast stays readable; remainder summarized
      const shown = result.errors.slice(0, 3).map((e) => `• ${e.message}`).join("\n");
      const extra = result.errors.length > 3 ? `\n+${result.errors.length - 3} more` : "";
      toast({
        title: t("preflight.failedTitle"),
        description: shown + extra,
        variant: "destructive",
      });
      return;
    }
    for (const w of result.warnings) {
      toast({ title: t("preflight.heads_up"), description: w.message });
    }

    setLaunching(true);
    try {
      const datasetRows = Math.max(formData.files.length * 500, 100);
      const tuned = autoTuneParams(datasetRows);
      const projectName = formData.projectName.trim() || formData.taskPrompt.slice(0, 60) || "Untitled Project";

      // 1. Create Supabase project (source of truth for UI)
      const created = await createProject({
        name: projectName,
        description: formData.taskPrompt,
        taskType: formData.taskType!,
        baseModel: formData.baseModel!,
        epochs: tuned.epochs,
        learningRate: tuned.learningRate,
        datasetSize: datasetRows,
      });

      // 2. Create Engine project and store its ID
      const engineTaskType = TASK_TYPE_TO_ENGINE[formData.taskType!];
      if (engineTaskType) {
        try {
          const engineProject = await engineCreateProject(
            projectName,
            formData.taskPrompt,
            engineTaskType,
          );
          setEngineMeta(created.id, { engineProjectId: engineProject.id });

          // 3. Fire background chain: upload seed → SDG → training
          const seedFile = formData.files[0] ?? null;
          void runEngineFlow(
            created.id,
            engineProject.id,
            formData.taskType!,
            formData.taskPrompt,
            seedFile,
            tuned.epochs,
            tuned.learningRate,
            formData.baseModel!,
            projectName,
          );
        } catch {
          // Engine unavailable — UI continues with simulator fallback
        }
      }

      toast({ title: t("newProject.launched"), description: created.name });
      navigate(`/projects/${created.id}`);
    } catch (e) {
      toast({
        title: t("newProject.launchFailed"),
        description: (e as Error).message,
        variant: "destructive",
      });
      setLaunching(false);
    }
  };

  useEffect(() => {
    const stored = sessionStorage.getItem("template-prefill");
    if (stored) {
      try {
        const tpl = JSON.parse(stored);
        setFormData((p) => ({
          ...p,
          projectName: tpl.name ?? p.projectName,
          taskPrompt: tpl.prompt ?? p.taskPrompt,
          taskType: tpl.taskType ?? p.taskType,
          baseModel: tpl.baseModel ?? p.baseModel,
        }));
        sessionStorage.removeItem("template-prefill");
      } catch {
        // ignore malformed prefill
      }
    }
  }, []);

  const steps = [
    { id: "prompt", label: t("newProject.taskPrompt") },
    { id: "task", label: t("newProject.taskType") },
    { id: "data", label: t("newProject.uploadData") },
    { id: "model", label: t("newProject.baseModel") },
  ];

  const updateForm = (partial: Partial<ProjectFormData>) => {
    setFormData((prev) => ({ ...prev, ...partial }));
  };

  const canProceed = () => {
    switch (currentStep) {
      case 0: return formData.taskPrompt.trim().length > 10;
      case 1: return formData.taskType !== null;
      case 2: return true;
      case 3: return formData.baseModel !== null;
      default: return false;
    }
  };

  const handleTemplateSelect = (template: { name: string; prompt: string; taskType: TaskType; baseModel: BaseModel }) => {
    setFormData({
      ...initialFormData,
      projectName: template.name,
      taskPrompt: template.prompt,
      taskType: template.taskType,
      baseModel: template.baseModel,
    });
    setShowTemplates(false);
    setCurrentStep(2);
  };

  const isLastStep = currentStep === steps.length - 1;

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
        <Button variant="outline" size="sm" className="gap-2" onClick={() => setShowTemplates(!showTemplates)}>
          <Sparkles className="h-3.5 w-3.5" />
          {showTemplates ? t("newProject.hideTemplates") : t("newProject.useTemplate")}
        </Button>
      </div>

      {showTemplates && <TemplateLibrary onSelect={handleTemplateSelect} />}

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
          {currentStep === 2 && <DataUploadStep formData={formData} updateForm={updateForm} />}
          {currentStep === 3 && <ModelSelectionStep formData={formData} updateForm={updateForm} />}
        </CardContent>
      </Card>

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
          <Button onClick={() => setCurrentStep((s) => s + 1)} disabled={!canProceed()} className="gap-2">
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
