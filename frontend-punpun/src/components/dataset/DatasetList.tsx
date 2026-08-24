import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight, Database, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusBadge } from "@/components/engine/StatusBadge";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { DatasetActions } from "@/components/dataset/DatasetActions";
import { DatasetPreviewTable } from "@/components/dataset/DatasetPreviewTable";
import { SdgJobCard } from "@/components/dataset/SdgJobCard";
import { useDatasets } from "@/hooks/queries";
import { isTerminalStatus, type Dataset } from "@/api/types";
import { formatRelativeTime } from "@/lib/format";
import { useLanguage } from "@/i18n/LanguageContext";
import { cn } from "@/lib/utils";

const sourceTone: Record<Dataset["source"], string> = {
  seed: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  sdg: "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  merged: "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  uploaded: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
};

interface DatasetListProps {
  projectId: string;
}

/** Table of datasets for a project. Rows expand in place to show either the
 *  live SDG progress card (in-flight / failed generation) or a JSONL
 *  preview, driven either by clicking the row or the preview action. */
export function DatasetList({ projectId }: DatasetListProps) {
  const { t } = useLanguage();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, isLoading, isError } = useDatasets(
    projectId,
    { limit: 100 },
    {
      refetchInterval: (query) =>
        query.state.data?.items.some((d) => !isTerminalStatus(d.status)) ? 5_000 : false,
    },
  );

  const toggle = (id: string) => setExpandedId((cur) => (cur === id ? null : id));

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (isError) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">{t("common.error")}</p>
    );
  }

  const items = data?.items ?? [];

  if (items.length === 0) {
    return <EngineEmptyState icon={Database} title={t("dataset.empty")} />;
  }

  return (
    <div className="rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8" />
            <TableHead>Name</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">{t("dataset.rows")}</TableHead>
            <TableHead>Created</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((dataset) => {
            const expanded = expandedId === dataset.id;
            const showJobCard =
              dataset.source === "sdg" &&
              (dataset.status === "pending" || dataset.status === "running" || dataset.status === "failed");
            return (
              <Fragment key={dataset.id}>
                <TableRow
                  className="cursor-pointer"
                  onClick={() => toggle(dataset.id)}
                  aria-expanded={expanded}
                >
                  <TableCell>
                    {expanded ? (
                      <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                    )}
                  </TableCell>
                  <TableCell className="font-medium text-foreground">{dataset.name}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={cn("capitalize", sourceTone[dataset.source])}>
                      {dataset.source === "sdg" ? t("dataset.sourceGenerated") : dataset.source === "seed" ? t("dataset.sourceSeed") : dataset.source}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={dataset.status} />
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    {dataset.num_samples.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatRelativeTime(dataset.created_at)}
                  </TableCell>
                  <TableCell>
                    <DatasetActions
                      dataset={dataset}
                      previewOpen={expanded}
                      onTogglePreview={() => toggle(dataset.id)}
                      onDeleted={() => setExpandedId((cur) => (cur === dataset.id ? null : cur))}
                    />
                  </TableCell>
                </TableRow>
                {expanded && (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={7} className="bg-muted/20 p-4">
                      {showJobCard ? (
                        <SdgJobCard dataset={dataset} />
                      ) : (
                        <DatasetPreviewTable datasetId={dataset.id} numSamples={dataset.num_samples} />
                      )}
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
