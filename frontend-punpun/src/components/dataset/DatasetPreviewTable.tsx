import { ExternalLink, FileText, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { JsonlPreview } from "@/components/engine/JsonlPreview";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { useDatasetDownloadUrl, useDatasetPreview } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";

interface DatasetPreviewTableProps {
  datasetId: string;
  /** Total row count from the parent Dataset row — used to skip the preview
   *  fetch entirely and show the empty state without a round trip. */
  numSamples: number;
  limit?: number;
}

/** Fetches and renders a dataset's first rows via JsonlPreview. Dumb about
 *  task-specific shape — JsonlPreview just pretty-prints whatever comes back. */
export function DatasetPreviewTable({ datasetId, numSamples, limit = 20 }: DatasetPreviewTableProps) {
  const { t } = useLanguage();
  const { toast } = useToast();
  const enabled = numSamples > 0;
  const { data, isLoading, isError } = useDatasetPreview(datasetId, limit, enabled);
  const viewUrlMutation = useDatasetDownloadUrl();

  // "No rows to preview" doesn't mean "nothing to see": a PDF-seeded dataset
  // has 0 canonical rows but a real stored file. Mint an inline-disposition
  // presigned URL so the browser renders it (PDF viewer / plain text) instead
  // of saving it. 409 (nothing stored yet) surfaces as a toast.
  const handleOpenFile = () => {
    viewUrlMutation.mutate(
      { id: datasetId, disposition: "inline" },
      {
        onSuccess: (res) => {
          window.open(res.url, "_blank", "noopener,noreferrer");
        },
        onError: (err: unknown) => {
          toast({
            variant: "destructive",
            description: err instanceof Error ? err.message : String(err),
          });
        },
      },
    );
  };

  const openFileAction = (
    <Button variant="outline" size="sm" onClick={handleOpenFile} disabled={viewUrlMutation.isPending}>
      {viewUrlMutation.isPending ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <ExternalLink className="h-3.5 w-3.5" />
      )}
      {t("dataset.previewOpenFile")}
    </Button>
  );

  if (!enabled) {
    return (
      <EngineEmptyState
        icon={FileText}
        title={t("dataset.previewEmpty")}
        hint={t("dataset.previewOpenFileHint")}
        action={openFileAction}
      />
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <p className="py-6 text-center text-xs text-muted-foreground">
        {t("common.error")}
      </p>
    );
  }

  if (data.samples.length === 0) {
    return (
      <EngineEmptyState
        icon={FileText}
        title={t("dataset.previewEmpty")}
        hint={t("dataset.previewOpenFileHint")}
        action={openFileAction}
      />
    );
  }

  return (
    <div className="space-y-1.5">
      <p className="text-xs text-muted-foreground">
        {t("dataset.preview")} · {data.samples.length}/{data.total}
      </p>
      <JsonlPreview rows={data.samples} />
    </div>
  );
}
