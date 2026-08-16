import { Loader2, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { useBaseModels } from "@/hooks/queries";
import type { ProjectFormData } from "@/pages/NewProject";
import { toErrorDetail } from "@/pages/NewProject";

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
  seqlen: "text-fuchsia-700 dark:text-fuchsia-300",
  quantization: "text-amber-700 dark:text-amber-300",
};

export function ModelSelectionStep({ formData, updateForm }: ModelSelectionStepProps) {
  const { data: models, isLoading, isError, error, refetch } = useBaseModels();

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-semibold text-foreground">Choose Base Model</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Select the pre-trained model to fine-tune. Larger models are more capable but slower to train.
        </p>
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

                <div className="grid grid-cols-4 gap-2 text-[11px] mb-2.5">
                  <div>
                    <p className="text-muted-foreground">Family</p>
                    <p className="font-medium text-foreground capitalize">{model.family}</p>
                  </div>
                  <div>
                    <p className={detailTitleColors.context}>Context</p>
                    <p className="font-medium text-foreground">{model.context_length.toLocaleString()}</p>
                  </div>
                  <div>
                    <p className={detailTitleColors.seqlen}>Rec. seq len</p>
                    <p className="font-medium text-foreground">{model.recommended_max_seq_length.toLocaleString()}</p>
                  </div>
                  <div>
                    <p className={detailTitleColors.quantization}>Quant</p>
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
