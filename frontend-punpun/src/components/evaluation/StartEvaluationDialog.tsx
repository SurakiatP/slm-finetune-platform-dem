import { useState } from "react";
import { Loader2 } from "lucide-react";

import { ApiError } from "@/api/client";
import type { EvaluationAccepted } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { useDatasets, useModels, useStartEvaluation } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";

interface StartEvaluationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Preselect a model artifact (e.g. dialog opened from a model detail page). */
  defaultModelArtifactId?: string;
  onCreated?: (accepted: EvaluationAccepted) => void;
}

/** Modal to launch a new evaluation run: pick a GGUF-exported model artifact
 *  + an eval dataset, optionally score with the platform's LLM judge. */
export function StartEvaluationDialog({
  open,
  onOpenChange,
  defaultModelArtifactId,
  onCreated,
}: StartEvaluationDialogProps) {
  const { t } = useLanguage();
  const [modelId, setModelId] = useState(defaultModelArtifactId ?? "");
  const [datasetId, setDatasetId] = useState("");
  const [useJudge, setUseJudge] = useState(false);

  const { data: models, isLoading: modelsLoading } = useModels(undefined, { limit: 200 });
  const { data: datasets, isLoading: datasetsLoading } = useDatasets(undefined, { limit: 200 });
  const startEvaluation = useStartEvaluation();

  // Only artifacts registered with Ollama (ollama_model_tag set) can serve inference.
  const evaluableModels = (models?.items ?? []).filter((m) => m.ollama_model_tag);
  const usableDatasets = (datasets?.items ?? []).filter((d) => d.num_samples > 0);

  const reset = () => {
    setModelId(defaultModelArtifactId ?? "");
    setDatasetId("");
    setUseJudge(false);
    startEvaluation.reset();
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const handleSubmit = () => {
    if (!modelId || !datasetId) return;
    startEvaluation.mutate(
      {
        model_artifact_id: modelId,
        dataset_id: datasetId,
        use_llm_judge: useJudge,
        judge_model: null,
      },
      {
        onSuccess: (accepted) => {
          onCreated?.(accepted);
          handleOpenChange(false);
        },
      },
    );
  };

  const error = startEvaluation.error instanceof ApiError ? startEvaluation.error : null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("eval.start")}</DialogTitle>
          <DialogDescription>{t("eval.startDescription")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>{t("eval.modelArtifact")}</Label>
            <Select value={modelId} onValueChange={setModelId} disabled={modelsLoading}>
              <SelectTrigger>
                <SelectValue
                  placeholder={
                    modelsLoading
                      ? t("common.loading")
                      : evaluableModels.length
                        ? t("eval.selectModelPlaceholder")
                        : t("eval.noExportableModels")
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {evaluableModels.map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{t("eval.modelHint")}</p>
          </div>

          <div className="space-y-1.5">
            <Label>{t("eval.dataset")}</Label>
            <Select value={datasetId} onValueChange={setDatasetId} disabled={datasetsLoading}>
              <SelectTrigger>
                <SelectValue
                  placeholder={
                    datasetsLoading
                      ? t("common.loading")
                      : usableDatasets.length
                        ? t("eval.selectDatasetPlaceholder")
                        : t("eval.noUsableDatasets")
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {usableDatasets.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name} ({t("eval.datasetRows").replace("{n}", d.num_samples.toLocaleString())})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between rounded-lg border border-border p-3">
            <div className="space-y-0.5">
              <Label htmlFor="use-judge">{t("eval.llmJudge")}</Label>
              <p className="text-xs text-muted-foreground">{t("eval.judgeHint")}</p>
            </div>
            <Switch id="use-judge" checked={useJudge} onCheckedChange={setUseJudge} />
          </div>

          {error && <ErrorDetail error={{ detail: error.message, code: error.code }} />}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={!modelId || !datasetId || startEvaluation.isPending}>
            {startEvaluation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {t("eval.startCta")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
