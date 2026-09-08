import { Sparkles } from "lucide-react";

import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { SdgJobCard } from "@/components/dataset/SdgJobCard";
import { useLanguage } from "@/i18n/LanguageContext";
import type { Dataset } from "@/api/types";

/** SDG stage detail: delegates the running/failed states to the existing
 *  `SdgJobCard` (already wired to `useJobProgress` for live phase/sample
 *  counts) so this hub doesn't duplicate that WebSocket + REST-fallback
 *  logic — it only adds the pending/completed framing `SdgJobCard` doesn't
 *  cover (it assumes a dataset row already exists and is in flight or just
 *  failed). */
export function SdgStageCard({ currentDataset }: { currentDataset: Dataset | null }) {
  const { t } = useLanguage();

  if (!currentDataset) {
    return (
      <div className="rounded-lg border border-dashed border-border p-4 text-center">
        <Sparkles className="mx-auto h-5 w-5 text-muted-foreground/50" aria-hidden />
        <p className="mt-2 text-xs text-muted-foreground">{t("pipelineHub.sdgNotStarted")}</p>
      </div>
    );
  }

  if (currentDataset.status === "pending" || currentDataset.status === "running" || currentDataset.status === "failed") {
    return <SdgJobCard dataset={currentDataset} />;
  }

  if (currentDataset.status === "cancelled") {
    return (
      <ErrorDetail
        error={{ detail: t("pipelineHub.sdgCancelled") }}
      />
    );
  }

  // completed
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-4">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground truncate">{currentDataset.name}</p>
        <p className="text-xs text-muted-foreground">
          {currentDataset.num_samples.toLocaleString()} {t("pipelineHub.samplesReady")}
        </p>
      </div>
    </div>
  );
}
