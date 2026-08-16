import { useState } from "react";
import { Ban, Loader2, PackageOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatusBadge } from "@/components/engine/StatusBadge";
import { ConfirmDialog } from "@/components/engine/ConfirmDialog";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { useCancelModelExport, useExportModel } from "@/hooks/queries";
import type { ArtifactFormat, ModelArtifact } from "@/api/types";
import type { JobProgressState } from "@/hooks/useJobProgress";
import { useLanguage } from "@/i18n/LanguageContext";

// GGUF quantization methods supported by llama-quantize, ordered by typical
// usefulness. The backend passes the value straight to llama-quantize.
const GGUF_QUANTIZATIONS = [
  { value: "q4_k_m", label: "q4_k_m — recommended" },
  { value: "q5_k_m", label: "q5_k_m — higher quality" },
  { value: "q6_k", label: "q6_k — very high quality" },
  { value: "q8_0", label: "q8_0 — near-lossless" },
  { value: "q3_k_m", label: "q3_k_m — smaller" },
  { value: "q4_0", label: "q4_0 — legacy 4-bit" },
  { value: "q5_0", label: "q5_0 — legacy 5-bit" },
  { value: "f16", label: "f16 — full precision" },
];

/** Export/re-export controls + cancel + failure detail for a model artifact.
 *  Progress (`progress`) and in-flight state (`exportInFlight`) are lifted
 *  to the parent page since both are derived from a single shared
 *  `useJobProgress(model.export_celery_task_id)` subscription. */
export function ExportPanel({
  model,
  exportInFlight,
  progress,
}: {
  model: ModelArtifact;
  exportInFlight: boolean;
  progress: JobProgressState;
}) {
  const { t } = useLanguage();
  const [format, setFormat] = useState<ArtifactFormat>("gguf");
  const [quantization, setQuantization] = useState("q4_k_m");
  const [confirmCancelOpen, setConfirmCancelOpen] = useState(false);

  const exportMutation = useExportModel();
  const cancelMutation = useCancelModelExport();

  const hasExported = Boolean(model.gguf_uri || model.safetensors_uri);

  const handleExport = () => {
    exportMutation.mutate({
      id: model.id,
      body: {
        format,
        quantization: format === "gguf" ? quantization.trim() || null : null,
      },
    });
  };

  const handleCancel = () => {
    cancelMutation.mutate(model.id, {
      onSuccess: () => setConfirmCancelOpen(false),
    });
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm">{hasExported ? t("model.reExport") : t("model.export")}</CardTitle>
          {model.export_status && <StatusBadge status={model.export_status} />}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {model.export_status === "failed" && model.export_error_message && (
          <ErrorDetail error={{ detail: model.export_error_message, code: t("model.exportFailed") }} />
        )}

        {exportInFlight ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {t("model.exportRunning")}
              {progress.exportProgress && (
                <span className="capitalize">— {progress.exportProgress.stage}</span>
              )}
              {!progress.socketOpen && <span className="text-[10px]">(reconnecting…)</span>}
            </div>
            {progress.exportProgress?.detail && (
              <p className="text-xs text-muted-foreground">{progress.exportProgress.detail}</p>
            )}
            <Button
              variant="destructive"
              size="sm"
              className="gap-2"
              onClick={() => setConfirmCancelOpen(true)}
            >
              <Ban className="h-3.5 w-3.5" /> {t("model.cancelExport")}
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Format</Label>
              <Select value={format} onValueChange={(v) => setFormat(v as ArtifactFormat)}>
                <SelectTrigger className="w-36">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="gguf">gguf</SelectItem>
                  <SelectItem value="safetensors">safetensors</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {format === "gguf" && (
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Quantization</Label>
                <Select value={quantization} onValueChange={setQuantization}>
                  <SelectTrigger className="w-44 font-mono text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {GGUF_QUANTIZATIONS.map((q) => (
                      <SelectItem key={q.value} value={q.value} className="font-mono text-xs">
                        {q.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <Button size="sm" className="gap-2" onClick={handleExport} disabled={exportMutation.isPending}>
              {exportMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <PackageOpen className="h-3.5 w-3.5" />
              )}
              {hasExported ? t("model.reExport") : t("model.export")}
            </Button>
          </div>
        )}

        {exportMutation.isError && (
          <p role="alert" className="text-xs text-destructive">
            {exportMutation.error instanceof Error ? exportMutation.error.message : t("model.exportFailed")}
          </p>
        )}
      </CardContent>

      <ConfirmDialog
        open={confirmCancelOpen}
        onOpenChange={setConfirmCancelOpen}
        onConfirm={handleCancel}
        title={t("model.cancelExport")}
        description="This will stop the in-progress export job."
        confirmLabel={t("common.confirm")}
        destructive
        loading={cancelMutation.isPending}
      />
    </Card>
  );
}
