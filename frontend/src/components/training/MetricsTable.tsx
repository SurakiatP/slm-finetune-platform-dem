import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatNumber } from "@/lib/format";
import type { MetricPoint } from "@/api/types";
import { useLanguage } from "@/i18n/LanguageContext";

interface MetricRow {
  key: string;
  count: number;
  latestValue: number | null;
  latestStep: number | null;
}

/** All metric keys recorded to MLflow for a training run, from
 *  useTrainingMetrics — key, point count, and latest value/step. */
export function MetricsTable({ metrics }: { metrics: Record<string, MetricPoint[]> }) {
  const { t } = useLanguage();

  const rows: MetricRow[] = Object.entries(metrics)
    .map(([key, points]) => {
      const latest = points.at(-1);
      return {
        key,
        count: points.length,
        latestValue: latest?.value ?? null,
        latestStep: latest?.step ?? null,
      };
    })
    .sort((a, b) => a.key.localeCompare(b.key));

  if (rows.length === 0) {
    return <p className="py-8 text-center text-sm text-muted-foreground">No metrics recorded yet.</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("training.metricKey")}</TableHead>
          <TableHead className="text-right">Points</TableHead>
          <TableHead className="text-right">{t("training.metricValue")}</TableHead>
          <TableHead className="text-right">Step</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.key}>
            <TableCell className="font-mono text-xs">{row.key}</TableCell>
            <TableCell className="text-right font-mono text-xs text-muted-foreground">{row.count}</TableCell>
            <TableCell className="text-right font-mono text-xs font-medium">{formatNumber(row.latestValue)}</TableCell>
            <TableCell className="text-right font-mono text-xs text-muted-foreground">{row.latestStep ?? "—"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
