import { useState } from "react";
import { History } from "lucide-react";

import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useProjectActivity } from "@/hooks/queries";
import { useOptionalAuth } from "@/contexts/AuthContext";
import { useLanguage } from "@/i18n/LanguageContext";
import { formatDateTime, shortId } from "@/lib/format";

const PAGE_SIZE = 25;

const RESOURCE_KEYS: Record<string, string> = {
  project: "activity.resource.project",
  dataset: "activity.resource.dataset",
  training: "activity.resource.training",
  model: "activity.resource.model",
  export: "activity.resource.export",
  evaluation: "activity.resource.evaluation",
  deployment: "activity.resource.deployment",
};

const VERB_KEYS: Record<string, string> = {
  create: "activity.verb.create",
  created: "activity.verb.create",
  submit: "activity.verb.submit",
  start: "activity.verb.submit",
  update: "activity.verb.update",
  delete: "activity.verb.delete",
  cancel: "activity.verb.cancel",
  completed: "activity.verb.completed",
  failed: "activity.verb.failed",
  upload: "activity.verb.upload",
  generate: "activity.verb.generate",
};

/** Audit trail for one project — actor, action, target — newest first,
 *  paginated via the `limit`/`offset` the `useProjectActivity` hook supports. */
export function ProjectActivityTable({ projectId }: { projectId: string }) {
  const { t } = useLanguage();
  const auth = useOptionalAuth();
  const [offset, setOffset] = useState(0);
  // Audit codes look like `export.failed`; render them as words, keeping the
  // raw code available as a tooltip for support.
  const describeAction = (action: string) => {
    const [resource, ...rest] = action.split(".");
    const verb = rest.join(".");
    const resourceLabel = RESOURCE_KEYS[resource] ? t(RESOURCE_KEYS[resource]) : resource.replace(/_/g, " ");
    const verbLabel = VERB_KEYS[verb] ? t(VERB_KEYS[verb]) : verb.replace(/[._]/g, " ");
    return verb ? `${resourceLabel} · ${verbLabel}` : resourceLabel;
  };
  const describeActor = (actorId: string | null) => {
    if (!actorId) return t("activity.anonymous");
    return auth?.user?.id === actorId ? t("activity.you") : shortId(actorId);
  };
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
        hint={t("activity.loadError")}
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
                <TableHead>{t("activity.outcome")}</TableHead>
                <TableHead>{t("activity.when")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((event) => (
                <TableRow key={event.id}>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {describeActor(event.actor_id)}
                  </TableCell>
                  <TableCell className="text-xs text-foreground" title={event.action}>{describeAction(event.action)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {RESOURCE_KEYS[event.resource_type] ? t(RESOURCE_KEYS[event.resource_type]) : event.resource_type} <span className="font-mono">{shortId(event.resource_id)}</span>
                  </TableCell>
                  <TableCell>
                    <Badge variant={event.outcome === "success" ? "default" : "destructive"}>{event.outcome === "success" ? t("activity.success") : event.outcome === "failure" ? t("activity.failure") : event.outcome}</Badge>
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
              hint={t("activity.emptyHint")}
            />
          </div>
        )}
      </CardContent>

      {hasMultiplePages && data && (
        <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-3">
          <p className="text-xs text-muted-foreground">
            {t("activity.range").replace("{from}", String(data.offset + 1)).replace("{to}", String(Math.min(data.offset + data.limit, data.total))).replace("{total}", String(data.total))}
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
