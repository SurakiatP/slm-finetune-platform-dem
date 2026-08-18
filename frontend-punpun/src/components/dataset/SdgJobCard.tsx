import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCircle2, Loader2, Radio } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ConfirmDialog } from "@/components/engine/ConfirmDialog";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { useCancelDatasetGeneration, queryKeys } from "@/hooks/queries";
import { useJobProgress } from "@/hooks/useJobProgress";
import { getJobProgress } from "@/api/endpoints/jobs";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";
import type { Dataset, SDGProgressMsg } from "@/api/types";

/** Extracts the Celery task id persisted on the dataset row. It lives inside
 *  the free-form `generation_metadata` blob rather than as a typed column. */
function extractJobId(dataset: Dataset): string | null {
  const raw = dataset.generation_metadata?.celery_task_id;
  return typeof raw === "string" ? raw : null;
}

/** `phase` is a plain `string` on the wire (see `SDGProgressMsg` in
 *  `api/types.ts`) so the frontend never hard-fails `tsc` when the backend
 *  ships a phase value this build hasn't heard of yet. Known phases get a
 *  localized i18n label (values below are literal `sdgPhase.*` keys, so
 *  `npm run check:i18n` sees them as used even though they're looked up
 *  dynamically); anything unrecognized falls back to the raw phase string
 *  with underscores turned into spaces. */
const SDG_PHASE_LABEL_KEYS: Record<string, string> = {
  format_detection: "sdgPhase.formatDetection",
  meta_prompting: "sdgPhase.metaPrompting",
  generating: "sdgPhase.generating",
  validating: "sdgPhase.validating",
  judging: "sdgPhase.judging",
  dedup: "sdgPhase.dedup",
  deduplicating: "sdgPhase.deduplicating",
  persisting: "sdgPhase.persisting",
  // New phases added in parallel on the backend (train/hold-out split).
  splitting_holdout: "sdgPhase.splittingHoldout",
  persisting_train: "sdgPhase.persistingTrain",
  persisting_holdout: "sdgPhase.persistingHoldout",
};

function sdgPhaseLabel(phase: string, t: (key: string) => string): string {
  const key = SDG_PHASE_LABEL_KEYS[phase];
  if (key) return t(key);
  return phase.replace(/_/g, " ");
}

/** `VITE_MOCK=1` has no WS server pushing frames (see `useJobProgress`,
 *  which skips the socket entirely in this mode and only fetches the
 *  progress snapshot once on mount) — poll the same REST snapshot the
 *  mock's ticking simulation (`mockEngine.ts`) updates so the phase/percent
 *  display actually moves during `dev:mock` instead of freezing at the
 *  first render. */
const isMockMode = import.meta.env.VITE_MOCK === "1";
const MOCK_POLL_MS = 1200;

/** Live progress card for an in-flight (or just-failed) SDG generation job.
 *  Renders phase + samples-generated/target from the WS, with a cancel
 *  action while the job is still pending/running. Failed jobs render their
 *  error via ErrorDetail instead of progress. */
export function SdgJobCard({ dataset }: { dataset: Dataset }) {
  const { t } = useLanguage();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const isFailed = dataset.status === "failed";
  const jobId = extractJobId(dataset);
  const progress = useJobProgress(isFailed ? null : jobId, {
    onTerminal: () => {
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dataset(dataset.id) });
    },
  });

  // Mock-mode-only fallback: `useJobProgress` fetches the REST snapshot
  // once on mount (no WS in this mode to keep pushing frames), so without
  // this the phase/percent readout below would freeze at whatever the
  // snapshot said when the row was expanded. Polling here — scoped to this
  // component, distinct from the hook's one-shot hydration fetch — is what
  // makes mockEngine's ticking SDG simulation visible while a job runs.
  const [polledSdg, setPolledSdg] = useState<SDGProgressMsg | null>(null);
  useEffect(() => {
    setPolledSdg(null);
    if (!isMockMode || isFailed || !jobId) return;
    let cancelled = false;
    const tick = () => {
      getJobProgress(jobId)
        .then((msg) => {
          if (cancelled) return;
          if (msg.type === "sdg_progress") setPolledSdg(msg);
        })
        .catch(() => {
          // No snapshot yet (404) — keep waiting for the next tick.
        });
    };
    tick();
    const interval = window.setInterval(tick, MOCK_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [jobId, isFailed]);

  const cancelMutation = useCancelDatasetGeneration();

  const handleCancel = () => {
    cancelMutation.mutate(dataset.id, {
      onSuccess: () => setConfirmOpen(false),
      onError: (err: unknown) => {
        toast({ variant: "destructive", description: err instanceof Error ? err.message : String(err) });
        setConfirmOpen(false);
      },
    });
  };

  if (isFailed) {
    return (
      <ErrorDetail
        error={{ detail: dataset.error_message ?? progress.failed?.error ?? null, code: progress.failed?.error_type ?? null }}
      />
    );
  }

  // Prefer the locally-polled mock snapshot (ticks live in dev:mock) over
  // the WS-hydrated one so the display keeps moving in mock mode; in real
  // mode `polledSdg` stays null and `progress.sdg` (pushed over WS) wins.
  const sdg = polledSdg ?? progress.sdg;
  const target = Math.max(1, sdg?.samples_target ?? 0);
  const generated = sdg?.samples_generated ?? 0;
  const pct = Math.max(0, Math.min(100, Math.round((generated / target) * 100)));

  return (
    <div className="space-y-3 rounded-lg border border-border p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className="flex items-center gap-1 font-mono text-[11px] text-muted-foreground"
            title={progress.socketOpen ? "Live WebSocket connected" : "Polling (socket offline)"}
          >
            <Radio className={progress.socketOpen ? "h-3 w-3 text-emerald-500" : "h-3 w-3 text-yellow-500"} aria-hidden />
            {progress.socketOpen ? "live" : "connecting"}
          </span>
          {sdg && (
            <Badge variant="outline" className="text-[10px] capitalize">
              {sdgPhaseLabel(sdg.phase, t)}
            </Badge>
          )}
        </div>
        {(dataset.status === "pending" || dataset.status === "running") && (
          <Button size="sm" variant="outline" onClick={() => setConfirmOpen(true)}>
            <Ban className="h-3.5 w-3.5" aria-hidden />
            {t("dataset.cancelGeneration")}
          </Button>
        )}
      </div>

      {progress.completed && (
        <div className="flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm">
          <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0" aria-hidden />
          <span className="text-foreground">Job completed</span>
        </div>
      )}

      {sdg ? (
        <div className="space-y-2">
          <div className="flex justify-between text-xs">
            <span className="text-muted-foreground">Samples generated</span>
            <span className="font-mono text-foreground">
              {generated.toLocaleString()} / {target.toLocaleString()} ({pct}%)
            </span>
          </div>
          <Progress value={pct} className="h-2" />
          <dl className="grid grid-cols-3 gap-2 text-[11px] font-mono sm:grid-cols-4">
            <div className="rounded-md bg-muted/50 p-2">
              <dt className="text-muted-foreground">valid</dt>
              <dd className="mt-0.5 text-emerald-600 dark:text-emerald-400">{sdg.samples_valid.toLocaleString()}</dd>
            </div>
            <div className="rounded-md bg-muted/50 p-2">
              <dt className="text-muted-foreground">rejected</dt>
              <dd className="mt-0.5 text-yellow-600 dark:text-yellow-400">{sdg.samples_rejected.toLocaleString()}</dd>
            </div>
            <div className="rounded-md bg-muted/50 p-2">
              <dt className="text-muted-foreground">duplicates</dt>
              <dd className="mt-0.5 text-foreground">{sdg.duplicates_removed.toLocaleString()}</dd>
            </div>
          </dl>
        </div>
      ) : (
        <div className="flex items-center justify-center gap-2 py-6 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          {dataset.status === "pending" ? "Waiting for a worker to pick up the job…" : "Waiting for progress updates…"}
        </div>
      )}

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
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
