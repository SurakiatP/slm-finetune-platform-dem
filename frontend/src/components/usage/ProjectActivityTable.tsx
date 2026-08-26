import { useState } from "react";
import { History } from "lucide-react";

import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useProjectActivity } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";
import { formatDateTime, shortId } from "@/lib/format";

const PAGE_SIZE = 25;

/** Audit trail for one project — actor, action, target — newest first,
 *  paginated via the `limit`/`offset` the `useProjectActivity` hook supports. */
export function ProjectActivityTable({ projectId }: { projectId: string }) {
  const { t } = useLanguage();
  const [offset, setOffset] = useState(0);
  const { data, isLoading, isError } = useProjectActivity(projectId, { limit: PAGE_SIZE, offset });

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
        icon={History}
        title={t("common.error")}
        hint="Could not load activity for this project. Try refreshing the page."
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
                <TableHead>{t("activity.actor")}</TableHead>
                <TableHead>{t("activity.action")}</TableHead>
                <TableHead>{t("activity.target")}</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead>{t("activity.when")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((event) => (
                <TableRow key={event.id}>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {event.actor_id ? shortId(event.actor_id) : "anonymous"}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-foreground">{event.action}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {event.resource_type} <span className="font-mono">{shortId(event.resource_id)}</span>
                  </TableCell>
                  <TableCell>
                    <Badge variant={event.outcome === "success" ? "default" : "destructive"}>{event.outcome}</Badge>
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
              icon={History}
              title={t("activity.empty")}
              hint="Actions taken on this project — datasets, trainings, exports, evaluations — will appear here."
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
