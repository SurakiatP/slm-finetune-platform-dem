import { useState } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDatasetUsage } from "@/hooks/useDatasetUsage";
import { Ban, Download, Eye, Loader2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/engine/ConfirmDialog";
import {
  useCancelDatasetGeneration,
  useDatasetDownload,
  useDeleteDataset,
} from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import type { Dataset } from "@/api/types";
import { EmptyDownloadError } from "@/api/endpoints/datasets";

interface DatasetActionsProps {
  dataset: Dataset;
  /** Whether this row's preview/detail panel is currently expanded. */
  previewOpen?: boolean;
  /** Toggle the row's expanded preview/detail panel. */
  onTogglePreview?: () => void;
  /** Called after a delete succeeds, e.g. to clear a selection. */
  onDeleted?: () => void;
}

/** Per-row action buttons for a dataset: preview toggle, presigned download,
 *  delete, and (for in-flight SDG jobs) cancel generation. Each destructive
 *  action goes through ConfirmDialog. */
export function DatasetActions({ dataset, previewOpen, onTogglePreview, onDeleted }: DatasetActionsProps) {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);

  const downloadMutation = useDatasetDownload();
  const deleteMutation = useDeleteDataset();
  const cancelMutation = useCancelDatasetGeneration();

  const usage = useDatasetUsage().get(dataset.id);
  const [blockedReason, setBlockedReason] = useState<string | null>(null);
  const deleteBlockedText = usage
    ? t(usage === "training" ? "datasetsPage.inUseHint" : "datasetsPage.inUseEvalHint")
    : blockedReason;

  const canCancel = dataset.source === "sdg" && (dataset.status === "pending" || dataset.status === "running");

  const handleDownload = () => {
    downloadMutation.mutate(dataset.id, {
      onSuccess: () => toast({ description: t("dataset.downloadStarted") }),
      onError: (err: unknown) => {
        const description = err instanceof EmptyDownloadError
          ? t("dataset.downloadEmpty")
          : err instanceof Error ? err.message : String(err);
        toast({ variant: "destructive", description });
      },
    });
  };

  const handleDelete = () => {
    deleteMutation.mutate(dataset.id, {
      onSuccess: () => {
        setDeleteOpen(false);
        onDeleted?.();
      },
      onError: (err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        // Keep the button disabled after a 409 instead of letting the user retry into it.
        setBlockedReason(message);
        toast({ title: t("datasetsPage.inUseTitle"), variant: "destructive", description: message });
        setDeleteOpen(false);
      },
    });
  };

  const handleCancel = () => {
    cancelMutation.mutate(dataset.id, {
      onSuccess: () => setCancelOpen(false),
      onError: (err: unknown) => {
        toast({ variant: "destructive", description: err instanceof Error ? err.message : String(err) });
        setCancelOpen(false);
      },
    });
  };

  return (
    <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
      <Button
        size="icon"
        variant="ghost"
        className="h-7 w-7"
        aria-label={t("dataset.preview")}
        aria-pressed={previewOpen}
        onClick={onTogglePreview}
      >
        <Eye className="h-3.5 w-3.5" aria-hidden />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="h-7 w-7"
        aria-label={t("dataset.download")}
        onClick={handleDownload}
        disabled={downloadMutation.isPending}
      >
        {downloadMutation.isPending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <Download className="h-3.5 w-3.5" aria-hidden />
        )}
      </Button>
      {canCancel && (
        <Button
          size="icon"
          variant="ghost"
          className="h-7 w-7 text-muted-foreground hover:text-destructive"
          aria-label={t("dataset.cancelGeneration")}
          onClick={() => setCancelOpen(true)}
        >
          <Ban className="h-3.5 w-3.5" aria-hidden />
        </Button>
      )}
      {deleteBlockedText ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-block" tabIndex={0}>
              <Button size="icon" variant="ghost" className="h-7 w-7 text-muted-foreground" disabled aria-label={t("dataset.delete")}>
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent>{deleteBlockedText}</TooltipContent>
        </Tooltip>
      ) : (
        <Button
          size="icon"
          variant="ghost"
          className="h-7 w-7 text-muted-foreground hover:text-destructive"
          aria-label={t("dataset.delete")}
          onClick={() => setDeleteOpen(true)}
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden />
        </Button>
      )}

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        onConfirm={handleDelete}
        title={t("dataset.delete")}
        description={t("dataset.deleteConfirm")}
        confirmLabel={t("dataset.delete")}
        destructive
        loading={deleteMutation.isPending}
      />
      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        onConfirm={handleCancel}
        title={t("dataset.cancelGeneration")}
        description={t("dataset.cancelConfirm")}
        confirmLabel={t("dataset.cancelGeneration")}
        destructive
        loading={cancelMutation.isPending}
      />
    </div>
  );
}
