import { useEffect, useRef, useState } from "react";
import { Ban, Radio, Wifi, WifiOff } from "lucide-react";

import { isTerminalStatus } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/engine/ConfirmDialog";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { QueueBadge } from "@/components/engine/QueueBadge";
import { StatusBadge } from "@/components/engine/StatusBadge";
import { queryKeys, useCancelEvaluation, useEvaluation } from "@/hooks/queries";
import { jobRefetchInterval, useJobProgress } from "@/hooks/useJobProgress";
import { useQueryClient } from "@tanstack/react-query";
import { extractConfusionMatrix, metricMeta, scalarMetrics } from "@/lib/metrics";
import { formatDateTime, formatDuration, formatNumber, shortId } from "@/lib/format";
import { useLanguage } from "@/i18n/LanguageContext";

interface EvaluationDetailProps {
  evaluationId: string;
  /** Optional resolved display names, so the header reads "gpt-mini-lora" instead of a uuid. */
  modelName?: string;
  datasetName?: string;
}

/** Full detail view of one evaluation run: status/queue chips, live progress
 *  while running, the metric grid, LLM-judge score, and raw error on
 *  failure. Reused as the row-click target from EvaluationTable and as the
 *  standalone body of a "view evaluation" dialog/drawer. */
export function EvaluationDetail({ evaluationId, modelName, datasetName }: EvaluationDetailProps) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const [cancelling, setCancelling] = useState(false);

  // useEvaluation's refetchInterval is evaluated by react-query on every tick
  // against the *latest* function passed in, so closing over this ref (kept
  // current by the effect below) lets REST polling back off exactly like the
  // training/export views: fast while the socket is down, slow once it's
  // healthy, off once terminal — without a circular hook-ordering problem
  // (useJobProgress needs `evaluation.celery_task_id`, which only exists
  // after this query resolves).
  const socketOpenRef = useRef(false);

  const { data: evaluation, isLoading } = useEvaluation(evaluationId, {
    refetchInterval: (query) => {
      const ev = query.state.data;
      if (!ev) return 3000;
      return jobRefetchInterval(isTerminalStatus(ev.status), socketOpenRef.current);
    },
  });

  const terminal = evaluation ? isTerminalStatus(evaluation.status) : false;

  const progress = useJobProgress(evaluation?.celery_task_id ?? null, {
    enabled: !!evaluation && !terminal,
    onTerminal: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.evaluation(evaluationId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.evaluations });
    },
  });

  useEffect(() => {
    socketOpenRef.current = progress.socketOpen;
  }, [progress.socketOpen]);

  const cancelMutation = useCancelEvaluation();

  if (isLoading || !evaluation) {
    return <p className="py-10 text-center text-sm text-muted-foreground">{t("common.loading")}</p>;
  }

  const cancellable = evaluation.status === "pending" || evaluation.status === "running";
  const baseMetrics = evaluation.metrics_json ? scalarMetrics(evaluation.metrics_json) : {};
  const metrics =
    evaluation.llm_judge_score !== null
      ? { ...baseMetrics, llm_judge_score: evaluation.llm_judge_score }
      : baseMetrics;
  const confusion = evaluation.metrics_json ? extractConfusionMatrix(evaluation.metrics_json) : null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">{shortId(evaluation.id, 12)}</span>
            <StatusBadge status={evaluation.status} />
            <QueueBadge queueState={evaluation.queue_state} queuePosition={evaluation.queue_position} />
            {!terminal && (
              <Badge variant="outline" className="gap-1 text-[10px]">
                {progress.socketOpen ? (
                  <Wifi className="h-3 w-3 text-emerald-500" aria-hidden />
                ) : (
                  <WifiOff className="h-3 w-3 text-muted-foreground" aria-hidden />
                )}
                {progress.socketOpen ? "live" : "polling"}
              </Badge>
            )}
          </div>
          <div className="text-sm text-foreground">
            <span className="text-muted-foreground">Model:</span> {modelName ?? shortId(evaluation.model_artifact_id)}
            <span className="mx-2 text-muted-foreground">·</span>
            <span className="text-muted-foreground">{t("eval.dataset")}:</span>{" "}
            {datasetName ?? shortId(evaluation.dataset_id)}
          </div>
        </div>

        {cancellable && (
          <Button variant="outline" size="sm" className="text-destructive" onClick={() => setCancelling(true)}>
            <Ban className="h-3.5 w-3.5" /> {t("eval.cancel")}
          </Button>
        )}
      </div>

      {evaluation.status === "running" && (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          <Radio className="h-3.5 w-3.5 animate-pulse text-sky-500" aria-hidden />
          {t("eval.running")} — {progress.socketOpen ? "streaming live updates" : "reconnecting, falling back to polling"}
          {progress.evaluationProgress && (
            <span className="ml-auto font-mono capitalize">
              {progress.evaluationProgress.phase} {progress.evaluationProgress.rows_done}/{progress.evaluationProgress.rows_total}
            </span>
          )}
        </div>
      )}

      {evaluation.error_message && (
        <ErrorDetail error={{ detail: evaluation.error_message, code: evaluation.status === "failed" ? "evaluation_failed" : null }} />
      )}

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("eval.metrics")}
        </h4>
        {Object.keys(metrics).length > 0 ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {Object.entries(metrics).map(([name, value]) => {
              const meta = metricMeta(name, value);
              return (
                <Card key={name}>
                  <CardContent className="p-3.5">
                    <p className="text-lg font-bold text-foreground">
                      {meta.kind === "score5" ? `${formatNumber(value, 2)}/5` : formatNumber(value, meta.kind === "count" ? 0 : 4)}
                    </p>
                    <p className="mt-0.5 truncate text-[11px] font-medium text-foreground/80" title={name}>
                      {name}
                    </p>
                    <p className="text-[10px] text-muted-foreground">{meta.range}</p>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            {terminal ? "No scalar metrics reported." : "Metrics appear once the evaluation completes."}
          </p>
        )}
      </div>

      {evaluation.llm_judge_model && (
        <p className="text-xs text-muted-foreground">
          {t("eval.llmJudge")} model: <span className="font-mono text-foreground">{evaluation.llm_judge_model}</span>
        </p>
      )}

      {confusion && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Confusion matrix
          </h4>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="text-xs">
              <tbody>
                {confusion.matrix.map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <td
                        key={j}
                        className={
                          i === j
                            ? "border border-border/60 bg-emerald-500/10 px-2.5 py-1.5 text-center font-medium text-emerald-600 dark:text-emerald-400"
                            : "border border-border/60 px-2.5 py-1.5 text-center text-muted-foreground"
                        }
                      >
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {confusion.labels && (
            <p className="mt-1 text-[10px] text-muted-foreground">Labels: {confusion.labels.join(", ")}</p>
          )}
        </div>
      )}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Timing
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 pt-0 text-xs sm:grid-cols-4">
          <div>
            <p className="text-muted-foreground">Created</p>
            <p className="text-foreground">{formatDateTime(evaluation.created_at)}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Started</p>
            <p className="text-foreground">{formatDateTime(evaluation.started_at)}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Ended</p>
            <p className="text-foreground">{formatDateTime(evaluation.ended_at)}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Duration</p>
            <p className="text-foreground">{formatDuration(evaluation.started_at, evaluation.ended_at)}</p>
          </div>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={cancelling}
        onOpenChange={setCancelling}
        onConfirm={() =>
          cancelMutation.mutate(evaluation.id, {
            onSettled: () => setCancelling(false),
          })
        }
        title={t("eval.cancel")}
        description={`Cancel evaluation ${shortId(evaluation.id)}? This cannot be undone.`}
        confirmLabel={t("eval.cancel")}
        destructive
        loading={cancelMutation.isPending}
      />
    </div>
  );
}
