import { FileText, Loader2 } from "lucide-react";

import { JsonlPreview } from "@/components/engine/JsonlPreview";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { useDatasetPreview } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";

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
  const enabled = numSamples > 0;
  const { data, isLoading, isError } = useDatasetPreview(datasetId, limit, enabled);

  if (!enabled) {
    return (
      <EngineEmptyState
        icon={FileText}
        title={t("dataset.previewEmpty")}
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
