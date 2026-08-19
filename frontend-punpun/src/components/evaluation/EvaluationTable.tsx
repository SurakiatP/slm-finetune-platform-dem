import { Fragment, useState, type ReactNode } from "react";
import { Ban, ClipboardList } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ConfirmDialog } from "@/components/engine/ConfirmDialog";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { QueueBadge } from "@/components/engine/QueueBadge";
import { StatusBadge } from "@/components/engine/StatusBadge";
import { useCancelEvaluation } from "@/hooks/queries";
import { formatNumber, formatRelativeTime, shortId } from "@/lib/format";
import type { Evaluation } from "@/api/types";
import { useLanguage } from "@/i18n/LanguageContext";

interface EvaluationTableProps {
  evaluations: Evaluation[];
  /** model_artifact_id -> display name, resolved by the caller (list of loaded models). */
  modelNames?: Record<string, string>;
  /** dataset_id -> display name, resolved by the caller (list of loaded datasets). */
  datasetNames?: Record<string, string>;
  onRowClick?: (evaluation: Evaluation) => void;
  emptyHint?: string;
  /** When set to a row's id (and renderExpanded is provided), that row grows
   *  an inline detail panel beneath it — used by the standalone Evaluations
   *  page for an accordion-style expand instead of navigating away. */
  expandedId?: string | null;
  renderExpanded?: (evaluation: Evaluation) => ReactNode;
}

/** Job/dataset/run list for the evaluation stage. Row click opens the detail
 *  view (caller decides how — dialog, drawer, etc); cancel is handled inline
 *  for pending/running rows via the shared ConfirmDialog. */
export function EvaluationTable({
  evaluations,
  modelNames,
  datasetNames,
  onRowClick,
  emptyHint,
  expandedId,
  renderExpanded,
}: EvaluationTableProps) {
  const { t } = useLanguage();
  const [cancelTarget, setCancelTarget] = useState<Evaluation | null>(null);
  const cancelMutation = useCancelEvaluation();

  if (evaluations.length === 0) {
    return (
      <EngineEmptyState
        icon={ClipboardList}
        title={t("eval.empty")}
        hint={emptyHint}
      />
    );
  }

  const handleCancelConfirm = () => {
    if (!cancelTarget) return;
    cancelMutation.mutate(cancelTarget.id, {
      onSettled: () => setCancelTarget(null),
    });
  };

  return (
    <>
      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("eval.title")}</TableHead>
              <TableHead>{t("eval.status")}</TableHead>
              <TableHead>{t("eval.dataset")}</TableHead>
              <TableHead>{t("eval.llmJudge")}</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {evaluations.map((evaluation) => {
              const cancellable = evaluation.status === "pending" || evaluation.status === "running";
              const isExpanded = expandedId === evaluation.id && !!renderExpanded;
              return (
                <Fragment key={evaluation.id}>
                  <TableRow
                    className={onRowClick ? "cursor-pointer" : undefined}
                    onClick={() => onRowClick?.(evaluation)}
                  >
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-mono text-xs text-foreground">{shortId(evaluation.id)}</span>
                      <span className="text-xs text-muted-foreground">
                        {modelNames?.[evaluation.model_artifact_id] ?? shortId(evaluation.model_artifact_id)}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-col gap-1">
                      <StatusBadge status={evaluation.status} />
                      <QueueBadge queueState={evaluation.queue_state} queuePosition={evaluation.queue_position} />
                    </div>
                  </TableCell>
                  <TableCell className="text-sm text-foreground">
                    {datasetNames?.[evaluation.dataset_id] ?? shortId(evaluation.dataset_id)}
                  </TableCell>
                  <TableCell className="text-sm">
                    {evaluation.llm_judge_score !== null ? (
                      <span className="font-medium text-foreground">
                        {formatNumber(evaluation.llm_judge_score, 2)}/5
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatRelativeTime(evaluation.created_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    {cancellable && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive hover:text-destructive"
                        onClick={(event) => {
                          event.stopPropagation();
                          setCancelTarget(evaluation);
                        }}
                      >
                        <Ban className="h-3.5 w-3.5" /> {t("eval.cancel")}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
                {isExpanded && (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={6} className="bg-muted/20 p-4">
                      {renderExpanded!(evaluation)}
                    </TableCell>
                  </TableRow>
                )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </div>

      <ConfirmDialog
        open={cancelTarget !== null}
        onOpenChange={(open) => !open && setCancelTarget(null)}
        onConfirm={handleCancelConfirm}
        title={t("eval.cancel")}
        description={
          cancelTarget
            ? `Cancel evaluation ${shortId(cancelTarget.id)}? This cannot be undone.`
            : undefined
        }
        confirmLabel={t("eval.cancel")}
        destructive
        loading={cancelMutation.isPending}
      />
    </>
  );
}
