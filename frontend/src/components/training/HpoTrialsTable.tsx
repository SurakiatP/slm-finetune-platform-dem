import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatNumber, shortId } from "@/lib/format";
import type { HpoChildSummary } from "@/api/types";
import { useLanguage } from "@/i18n/LanguageContext";

/** One row per finished HPO child run — name/run id, final eval loss, and
 *  the trial's hyperparameters as chips. Lowest final_eval_loss is marked
 *  best (nulls — trial errored before eval — sort last). */
export function HpoTrialsTable({ trials }: { trials: HpoChildSummary[] }) {
  const { t } = useLanguage();

  const sorted = [...trials].sort((a, b) => {
    if (a.final_eval_loss == null && b.final_eval_loss == null) return 0;
    if (a.final_eval_loss == null) return 1;
    if (b.final_eval_loss == null) return -1;
    return a.final_eval_loss - b.final_eval_loss;
  });
  const bestRunId = sorted.find((trial) => trial.final_eval_loss != null)?.run_id ?? null;

  if (trials.length === 0) {
    return <p className="py-8 text-center text-sm text-muted-foreground">No HPO trials recorded yet.</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("training.trial")}</TableHead>
          <TableHead>Run ID</TableHead>
          <TableHead className="text-right">Final eval loss</TableHead>
          <TableHead>{t("training.params")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((trial) => (
          <TableRow key={trial.run_id}>
            <TableCell className="text-xs font-medium">
              <span className="inline-flex items-center gap-1.5">
                {trial.name}
                {trial.run_id === bestRunId && (
                  <Badge className="text-[9px] shrink-0 bg-primary/15 text-primary border-primary/30" variant="outline">
                    {t("training.bestTrial")}
                  </Badge>
                )}
              </span>
            </TableCell>
            <TableCell className="font-mono text-xs text-muted-foreground">{shortId(trial.run_id)}</TableCell>
            <TableCell className="text-right font-mono text-xs font-medium">{formatNumber(trial.final_eval_loss)}</TableCell>
            <TableCell>
              <div className="flex flex-wrap gap-1">
                {Object.entries(trial.params).map(([k, v]) => (
                  <span key={k} className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                    {k}={v}
                  </span>
                ))}
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
