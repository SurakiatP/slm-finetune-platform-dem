import { useLanguage } from "@/i18n/LanguageContext";
import { Loader2, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { useBaseModels } from "@/hooks/queries";
import { toErrorDetail, type ProjectFormData } from "@/pages/NewProject";

interface ModelSelectionStepProps {
  formData: ProjectFormData;
  updateForm: (p: Partial<ProjectFormData>) => void;
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

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-semibold text-foreground">{t("newProject.model.title")}</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {t("newProject.model.hint")}
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" role="status">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("newProject.model.loading")}
        </div>
      ) : isError ? (
        <div className="space-y-3">
          <ErrorDetail error={toErrorDetail(error)} />
          <Button type="button" variant="outline" size="sm" className="gap-2" onClick={() => void refetch()}>
            <RefreshCw className="h-3.5 w-3.5" /> {t("common.tryAgain")}
          </Button>
        </div>
      ) : !models || models.length === 0 ? (
        <p className="py-8 text-sm text-muted-foreground">{t("newProject.model.empty")}</p>
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
                  {selected && <Badge className="text-[10px] shrink-0">{t("newProject.task.selected")}</Badge>}
                </div>

                <div className="grid grid-cols-3 gap-2 text-[11px] mb-2.5">
                  <div>
                    <p className="text-muted-foreground">{t("newProject.model.family")}</p>
                    <p className="font-medium text-foreground capitalize">{model.family}</p>
                  </div>
                  <div>
                    <p className={detailTitleColors.context}>{t("newProject.model.context")}</p>
                    <p className="font-medium text-foreground">{model.context_length.toLocaleString()}</p>
                  </div>
                  <div>
                    <p className={detailTitleColors.quantization}>{t("newProject.model.quantization")}</p>
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
          {t("newProject.model.seqLenBefore")}{" "}
          <span className="font-mono text-foreground">{formData.maxSeqLength}</span>{" "}
          {t("newProject.model.seqLenAfter")}
        </p>
      )}
    </div>
  );
}
