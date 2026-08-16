import { ArrowUpDown, Coins, MessageSquareText } from "lucide-react";

import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUsageSummary } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";
import { formatNumber, formatUsd } from "@/lib/format";

/** Month + year for a UTC calendar month, from an ISO period boundary. */
function utcMonthLabel(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
}

/** Fallback shown before the summary has loaded: today's UTC calendar month. */
function currentUtcMonthLabel(): string {
  return new Date().toLocaleString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
}

/** Cross-project usage/cost rollup for the current UTC calendar month — KPI
 *  tiles plus a per (model, stage) breakdown table. Self-contained: fetches
 *  its own data via `useUsageSummary`, so it can be dropped onto any page. */
export function UsageSummaryPanel() {
  const { t } = useLanguage();
  const { data, isLoading, isError } = useUsageSummary();

  const periodLabel = data ? utcMonthLabel(data.period_start) : currentUtcMonthLabel();

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-64 rounded-lg" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <EngineEmptyState
        icon={Coins}
        title={t("common.error")}
        hint="Could not load the usage summary. Try refreshing the page."
      />
    );
  }

  const hasRows = data.items.length > 0;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Prompt tokens</CardTitle>
            <ArrowUpDown className="h-4 w-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-foreground">{formatNumber(data.prompt_tokens)}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Completion tokens</CardTitle>
            <MessageSquareText className="h-4 w-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-foreground">{formatNumber(data.completion_tokens)}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">{t("usage.totalCost")}</CardTitle>
            <Coins className="h-4 w-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-foreground">{formatUsd(data.cost_usd)}</p>
            {data.has_unpriced_usage && (
              <Badge
                variant="outline"
                className="mt-2 border-amber-500/40 text-amber-600 dark:text-amber-400"
              >
                Includes unpriced rows
              </Badge>
            )}
          </CardContent>
        </Card>
      </div>

      {data.has_unpriced_usage && (
        <p className="text-xs text-muted-foreground">
          Some rows had no configured price for their model, so the cost total above is a floor, not the true total.
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base font-semibold">{t("usage.summary")}</CardTitle>
          <p className="text-xs text-muted-foreground">
            {periodLabel} · OpenRouter usage broken down by model and pipeline stage.
          </p>
        </CardHeader>
        <CardContent className={hasRows ? undefined : "pt-0"}>
          {hasRows ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Model</TableHead>
                  <TableHead>Stage</TableHead>
                  <TableHead className="text-right">Prompt tokens</TableHead>
                  <TableHead className="text-right">Completion tokens</TableHead>
                  <TableHead className="text-right">{t("usage.cost")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((row) => (
                  <TableRow key={`${row.model}::${row.stage}`}>
                    <TableCell
                      className="max-w-[18rem] truncate font-mono text-xs text-foreground"
                      title={row.model}
                    >
                      {row.model}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{row.stage}</Badge>
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs text-muted-foreground">
                      {formatNumber(row.prompt_tokens)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs text-muted-foreground">
                      {formatNumber(row.completion_tokens)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {row.cost_usd === null ? (
                        <span className="inline-flex items-center justify-end gap-1.5">
                          <span className="text-muted-foreground">—</span>
                          <Badge variant="outline" className="text-[10px]">
                            Unpriced
                          </Badge>
                        </span>
                      ) : (
                        <span className="text-foreground">{formatUsd(row.cost_usd)}</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EngineEmptyState
              icon={Coins}
              title={t("usage.empty")}
              hint="Rows appear here once SDG generation or evaluation runs make calls to OpenRouter."
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
