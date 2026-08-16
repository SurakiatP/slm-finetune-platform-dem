import { useState } from "react";
import { Coins } from "lucide-react";

import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useProjectUsage } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";
import { formatDateTime, formatNumber, formatUsd } from "@/lib/format";

const PAGE_SIZE = 25;

/** Per-event OpenRouter usage/cost log for one project, newest first,
 *  paginated via the `limit`/`offset` the `useProjectUsage` hook supports. */
export function ProjectUsageTable({ projectId }: { projectId: string }) {
  const { t } = useLanguage();
  const [offset, setOffset] = useState(0);
  const { data, isLoading, isError } = useProjectUsage(projectId, { limit: PAGE_SIZE, offset });

  if (isLoading && !data) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full rounded-md" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <EngineEmptyState
        icon={Coins}
        title={t("common.error")}
        hint="Could not load usage for this project. Try refreshing the page."
      />
    );
  }

  const items = data?.items ?? [];
  const hasRows = items.length > 0;
  const hasMultiplePages = !!data && data.total > data.limit;

  return (
    <Card>
      <CardContent className="p-0">
        {hasRows ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Model</TableHead>
                <TableHead>Stage</TableHead>
                <TableHead className="text-right">Prompt tokens</TableHead>
                <TableHead className="text-right">Completion tokens</TableHead>
                <TableHead className="text-right">{t("usage.cost")}</TableHead>
                <TableHead>{t("activity.when")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((event) => (
                <TableRow key={event.id}>
                  <TableCell className="max-w-[16rem] truncate font-mono text-xs text-foreground" title={event.model}>
                    {event.model}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{event.stage}</Badge>
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs text-muted-foreground">
                    {formatNumber(event.prompt_tokens)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs text-muted-foreground">
                    {formatNumber(event.completion_tokens)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    {event.cost_usd === null ? (
                      <span className="inline-flex items-center justify-end gap-1.5">
                        <span className="text-muted-foreground">—</span>
                        <Badge variant="outline" className="text-[10px]">
                          Unpriced
                        </Badge>
                      </span>
                    ) : (
                      <span className="text-foreground">{formatUsd(event.cost_usd)}</span>
                    )}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {formatDateTime(event.created_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="p-6">
            <EngineEmptyState
              icon={Coins}
              title={t("usage.empty")}
              hint="Rows appear here once SDG generation or evaluation runs make calls to OpenRouter for this project."
            />
          </div>
        )}
      </CardContent>

      {hasMultiplePages && data && (
        <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-3">
          <p className="text-xs text-muted-foreground">
            {data.offset + 1}–{Math.min(data.offset + data.limit, data.total)} of {data.total}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              {t("common.back")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={data.offset + data.limit >= data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              {t("common.next")}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
