import { useEffect, useRef } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { useBaseModels } from "@/hooks/queries";
import { toErrorDetail, type ProjectFormData } from "@/pages/NewProject";
import { useLanguage } from "@/i18n/LanguageContext";

interface ModelSelectionStepProps {
  formData: ProjectFormData;
  updateForm: (p: Partial<ProjectFormData>) => void;
}

// Mirrors TrainingCreateDialog.tsx:37 / api/schemas/training.py:28 —
// training_name doubles as the MLflow run name and the derived Ollama model
// tag, so it must stay ollama-tag-safe: lowercase letters, digits, '.', '_',
// '-' only, starting with a letter or digit, 1-63 chars total.
export const TRAINING_NAME_PATTERN = /^[a-z0-9][a-z0-9._-]{0,62}$/;

/** Lowercases and strips anything outside [a-z0-9._-], collapsing runs of
 *  disallowed characters into a single "-" and trimming leading/trailing "-". */
function slugify(input: string): string {
  return input
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Builds an ollama-tag-safe suggested training name from a project name,
 *  truncated so the result always satisfies TRAINING_NAME_PATTERN. */
function suggestTrainingName(projectName: string): string {
  const suffix = "-v1";
  const base = slugify(projectName) || "training";
  const maxBaseLen = 63 - suffix.length;
  const truncatedBase = base.length > maxBaseLen ? base.slice(0, maxBaseLen).replace(/-+$/, "") : base;
  return `${truncatedBase}${suffix}`;
}

const familyTitleColors: Record<string, string> = {
  qwen: "text-sky-700 dark:text-sky-300",
  llama: "text-orange-700 dark:text-orange-300",
  gemma: "text-emerald-700 dark:text-emerald-300",
  smollm: "text-violet-700 dark:text-violet-300",
};

const detailTitleColors = {
  context: "text-violet-700 dark:text-violet-300",
  quantization: "text-amber-700 dark:text-amber-300",
};

export function ModelSelectionStep({ formData, updateForm }: ModelSelectionStepProps) {
  const { data: models, isLoading, isError, error, refetch } = useBaseModels();
  const { t } = useLanguage();
  const hasPrefilledTrainingName = useRef(false);

  useEffect(() => {
    if (!hasPrefilledTrainingName.current && formData.trainingName === "") {
      hasPrefilledTrainingName.current = true;
      updateForm({ trainingName: suggestTrainingName(formData.projectName) });
    }
    // Prefill exactly once, on first render with an empty value — never
    // re-derive after that, so user edits are never clobbered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const trimmedTrainingName = formData.trainingName.trim();
  const isTrainingNameValid = TRAINING_NAME_PATTERN.test(trimmedTrainingName);

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-semibold text-foreground">Choose Base Model</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Select the pre-trained model to fine-tune. Larger models are more capable but slower to train.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="training-name">{t("newProject.trainingNameLabel")}</Label>
        <Input
          id="training-name"
          value={formData.trainingName}
          onChange={(e) => updateForm({ trainingName: e.target.value })}
          aria-invalid={!isTrainingNameValid}
        />
        {!isTrainingNameValid ? (
          <p role="alert" className="text-xs text-destructive">
            {t("newProject.trainingNameError")}
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">{t("newProject.trainingNameHint")}</p>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" role="status">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading available base models…
        </div>
      ) : isError ? (
        <div className="space-y-3">
          <ErrorDetail error={toErrorDetail(error)} />
          <Button type="button" variant="outline" size="sm" className="gap-2" onClick={() => void refetch()}>
            <RefreshCw className="h-3.5 w-3.5" /> Try again
          </Button>
        </div>
      ) : !models || models.length === 0 ? (
        <p className="py-8 text-sm text-muted-foreground">No base models are currently available from the Engine.</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {models.map((model) => {
            const selected = formData.baseModel === model.id;
            const modelTitleColor = familyTitleColors[model.family.toLowerCase()] ?? "text-foreground";
            return (
              <button
                key={model.id}
                type="button"
                onClick={() => updateForm({ baseModel: model.id, maxSeqLength: model.recommended_max_seq_length })}
                className={`text-left p-4 rounded-lg border-2 transition-all ${
                  selected
                    ? "border-primary bg-accent"
                    : "border-border hover:border-primary/40 bg-background"
                }`}
              >
                <div className="flex items-center justify-between gap-2 mb-2">
                  <div>
                    <span className={`font-semibold text-sm ${modelTitleColor}`}>{model.display_name}</span>
                    <span className="ml-1.5 text-xs text-muted-foreground">{model.params_billions}B</span>
                  </div>
                  {selected && <Badge className="text-[10px] shrink-0">Selected</Badge>}
                </div>

                <div className="grid grid-cols-3 gap-2 text-[11px] mb-2.5">
                  <div>
                    <p className="text-muted-foreground">Family</p>
                    <p className="font-medium text-foreground capitalize">{model.family}</p>
                  </div>
                  <div>
                    <p className={detailTitleColors.context}>Context</p>
                    <p className="font-medium text-foreground">{model.context_length.toLocaleString()}</p>
                  </div>
                  <div>
                    <p className={detailTitleColors.quantization}>Quantization</p>
                    <p className="font-medium text-foreground">{model.quantization}</p>
                  </div>
                </div>

                {model.license && (
                  <div className="flex flex-wrap gap-1">
                    <Badge variant="outline" className="text-[9px] px-1.5 py-0">{model.license}</Badge>
                  </div>
                )}
                {model.notes && <p className="mt-2.5 text-xs text-muted-foreground">{model.notes}</p>}
              </button>
            );
          })}
        </div>
      )}

      {formData.baseModel && formData.maxSeqLength && (
        <p className="text-xs text-muted-foreground">
          Training will use a max sequence length of{" "}
          <span className="font-mono text-foreground">{formData.maxSeqLength}</span>, pre-filled from the selected
          model's recommendation.
        </p>
      )}
    </div>
  );
}
