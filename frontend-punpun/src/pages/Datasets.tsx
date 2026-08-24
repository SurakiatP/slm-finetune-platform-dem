import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Database, Download, Eye, Loader2, Pencil, Search, Trash2 } from "lucide-react";

import { ApiError } from "@/api/client";
import type { Dataset } from "@/api/types";
import { ConfirmDialog } from "@/components/engine/ConfirmDialog";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { FadeIn, PageTransition } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DatasetPreviewTable } from "@/components/dataset/DatasetPreviewTable";
import { DatasetRenameDialog } from "@/components/dataset/DatasetRenameDialog";
import { useDatasetDownloadUrl, useDatasets, useDeleteDataset, useProjects, useTrainings } from "@/hooks/queries";
import { useToast } from "@/hooks/use-toast";
import { useLanguage } from "@/i18n/LanguageContext";
import { formatBytes, formatDateTime } from "@/lib/format";
import { datasetRoleTag } from "@/lib/labels";
import { cn } from "@/lib/utils";

const ROLE_TAG_STYLES: Record<ReturnType<typeof datasetRoleTag>, string> = {
  seed: "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  training: "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  "hold-out": "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  uploaded: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
};

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

/** Cap on how many hops the family-root walk will follow before giving up —
 *  a malformed/self-referential `parent_dataset_id`/`seed_dataset_id` chain
 *  must not hang the page (see G1 task note on cycles). No real lineage is
 *  anywhere near this deep. */
const MAX_FAMILY_WALK_DEPTH = 50;

/** Strips a trailing lineage-role suffix off a dataset name, e.g.
 *  "orders-qa-training" -> "orders-qa" — used as the family's display name
 *  when its root member isn't (or is no longer) loaded, falling back to the
 *  oldest loaded member's name instead. */
function stripFamilySuffix(name: string): string {
  return name.replace(/-(training|hold-out|seed)$/i, "");
}

function byCreatedAtDesc<T extends { created_at: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

interface FamilyGroup {
  rootKey: string;
  /** Members sorted newest-first. */
  members: Dataset[];
  baseName: string;
  projectId: string | null;
}

/**
 * Global, cross-project dataset list (W2-T6). Datasets used to be reachable
 * only from within a project; now they outlive their project (server-side
 * orphan on project delete — `project_id` goes null instead of a cascade
 * delete, see api/types.ts's `Dataset.project_id` doc comment) so they need
 * a home of their own to be found and cleaned up.
 */
export default function Datasets() {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Dataset | null>(null);
  const [renameTarget, setRenameTarget] = useState<Dataset | null>(null);
  const [blockedReasons, setBlockedReasons] = useState<Record<string, string>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [expandedFamilies, setExpandedFamilies] = useState<Record<string, boolean>>({});

  const { data: datasetsPage, isLoading, error } = useDatasets(undefined, { limit: 200 });
  const { data: projectsPage } = useProjects({ limit: 200 });
  const { data: trainingsPage } = useTrainings({ limit: 200 });
  const deleteMutation = useDeleteDataset();
  const downloadMutation = useDatasetDownloadUrl();

  const toggleExpanded = (id: string) => setExpandedId((cur) => (cur === id ? null : id));
  const toggleFamily = (rootKey: string) =>
    setExpandedFamilies((cur) => ({ ...cur, [rootKey]: !cur[rootKey] }));

  const handleDownload = (id: string) => {
    downloadMutation.mutate(id, {
      onSuccess: (res) => {
        window.open(res.url, "_blank", "noopener,noreferrer");
      },
      onError: (err: unknown) => {
        toast({ variant: "destructive", description: errorMessage(err) });
      },
    });
  };

  const projectNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const p of projectsPage?.items ?? []) map[p.id] = p.name;
    return map;
  }, [projectsPage]);

  // Any training that references a dataset blocks its deletion server-side
  // (DELETE /datasets/{id} -> 409 "in use"), regardless of that training's
  // status — pre-compute so a blocked row reads as blocked before the user
  // even tries, rather than only after eating a failed request.
  const inUseDatasetIds = useMemo(() => {
    const ids = new Set<string>();
    for (const tr of trainingsPage?.items ?? []) ids.add(tr.dataset_id);
    return ids;
  }, [trainingsPage]);

  const datasets = useMemo(() => datasetsPage?.items ?? [], [datasetsPage]);

  // Resolve every loaded dataset's family root: walk parent_dataset_id
  // first, then seed_dataset_id (falling back to the pre-backfill nested
  // generation_metadata.seed_dataset_id for older rows), memoised per id and
  // depth-capped so a self-referential/cyclical chain can't hang the walk.
  // The walk stops as soon as a referenced id isn't itself in the loaded
  // list — that unresolved id becomes the group's root key even though no
  // Dataset object exists for it.
  const familyRootById = useMemo(() => {
    const byId = new Map(datasets.map((d) => [d.id, d] as const));
    const cache = new Map<string, string>();

    const resolve = (id: string, depth: number): string => {
      const cached = cache.get(id);
      if (cached) return cached;
      if (depth > MAX_FAMILY_WALK_DEPTH) return id;

      const d = byId.get(id);
      if (!d) return id;

      const nestedSeedId = (d.generation_metadata as { seed_dataset_id?: string } | null)?.seed_dataset_id ?? null;
      const ref = d.parent_dataset_id ?? d.seed_dataset_id ?? nestedSeedId ?? null;

      let root: string;
      if (!ref || ref === id) {
        root = id;
      } else if (!byId.has(ref)) {
        root = ref;
      } else {
        root = resolve(ref, depth + 1);
      }
      cache.set(id, root);
      return root;
    };

    for (const d of datasets) resolve(d.id, 0);
    return cache;
  }, [datasets]);

  const familyGroups = useMemo(() => {
    const byId = new Map(datasets.map((d) => [d.id, d] as const));
    const groups = new Map<string, Dataset[]>();
    for (const d of datasets) {
      const rootKey = familyRootById.get(d.id) ?? d.id;
      const members = groups.get(rootKey);
      if (members) members.push(d);
      else groups.set(rootKey, [d]);
    }

    const result: FamilyGroup[] = [];
    for (const [rootKey, members] of groups) {
      const sortedMembers = byCreatedAtDesc(members);
      const rootMember = byId.get(rootKey);
      const oldestMember = sortedMembers[sortedMembers.length - 1];
      const baseName = rootMember ? rootMember.name : stripFamilySuffix(oldestMember.name);
      const projectId = (rootMember ?? sortedMembers[0]).project_id;
      result.push({ rootKey, members: sortedMembers, baseName, projectId });
    }
    return result;
  }, [datasets, familyRootById]);

  const searchLower = search.toLowerCase();
  const visibleGroups = useMemo(() => {
    const matched = familyGroups
      .filter((g) => searchLower === "" || g.members.some((m) => m.name.toLowerCase().includes(searchLower)))
      .sort((a, b) => new Date(b.members[0].created_at).getTime() - new Date(a.members[0].created_at).getTime());
    return matched;
  }, [familyGroups, searchLower]);

  const apiError = error ? errorMessage(error) : null;

  const handleDeleteConfirm = () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    deleteMutation.mutate(target.id, {
      onSuccess: () => {
        toast({ title: t("dataset.delete"), description: target.name });
        setDeleteTarget(null);
        setExpandedId((cur) => (cur === target.id ? null : cur));
      },
      onError: (err: unknown) => {
        // The dataset stayed in use between page load and this click (or the
        // pre-known check above missed it) — surface the server's own reason
        // and keep the row disabled from here on instead of letting the user
        // retry into the same 409.
        const message = errorMessage(err);
        setBlockedReasons((prev) => ({ ...prev, [target.id]: message }));
        toast({ title: t("datasetsPage.inUseTitle"), description: message, variant: "destructive" });
        setDeleteTarget(null);
      },
    });
  };

  const renderDatasetRow = (dataset: Dataset) => {
    const role = datasetRoleTag(dataset);
    const blockedReason = blockedReasons[dataset.id];
    const preKnownBlocked = inUseDatasetIds.has(dataset.id);
    const blocked = Boolean(blockedReason) || preKnownBlocked;
    const tooltipText = blockedReason ?? (preKnownBlocked ? t("datasetsPage.inUseHint") : undefined);

    const expanded = expandedId === dataset.id;
    const downloading = downloadMutation.isPending && downloadMutation.variables === dataset.id;

    return (
      <Fragment key={dataset.id}>
      <TableRow>
        <TableCell className="font-medium text-foreground">{dataset.name}</TableCell>
        <TableCell>
          <Badge variant="outline" className={cn("capitalize", ROLE_TAG_STYLES[role])}>
            {t(`datasetsPage.role.${role}`)}
          </Badge>
        </TableCell>
        <TableCell>
          {dataset.project_id && projectNames[dataset.project_id] ? (
            <span className="text-sm text-foreground">{projectNames[dataset.project_id]}</span>
          ) : (
            <Badge variant="secondary" className="text-[10px]">
              {t("datasetsPage.noProject")}
            </Badge>
          )}
        </TableCell>
        <TableCell className="text-right text-sm text-foreground">
          {dataset.num_samples.toLocaleString()}
        </TableCell>
        <TableCell className="text-right text-sm text-muted-foreground">
          {formatBytes(dataset.size_bytes)}
        </TableCell>
        <TableCell className="text-xs text-muted-foreground">
          {formatDateTime(dataset.created_at)}
        </TableCell>
        <TableCell className="text-right">
          <div
            className="flex items-center justify-end gap-1"
            onClick={(e) => e.stopPropagation()}
          >
            <Button
              size="icon"
              variant="ghost"
              className="h-7 w-7"
              aria-label={t("dataset.preview")}
              aria-pressed={expanded}
              onClick={() => toggleExpanded(dataset.id)}
            >
              <Eye className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="h-7 w-7"
              aria-label={t("dataset.download")}
              onClick={() => handleDownload(dataset.id)}
              disabled={downloading}
            >
              {downloading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Download className="h-3.5 w-3.5" aria-hidden />
              )}
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="h-7 w-7"
              aria-label={t("dataset.rename")}
              onClick={() => setRenameTarget(dataset)}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden />
            </Button>
            {blocked ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-block" tabIndex={0}>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7 text-muted-foreground"
                      disabled
                      aria-label={t("dataset.delete")}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>{tooltipText}</TooltipContent>
              </Tooltip>
            ) : (
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7 text-muted-foreground hover:text-destructive"
                aria-label={t("dataset.delete")}
                onClick={() => setDeleteTarget(dataset)}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              </Button>
            )}
          </div>
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={7} className="bg-muted/20 p-4">
            <DatasetPreviewTable datasetId={dataset.id} numSamples={dataset.num_samples} />
          </TableCell>
        </TableRow>
      )}
      </Fragment>
    );
  };

  return (
    <PageTransition>
      <div className="max-w-7xl space-y-6">
        <FadeIn>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
                <Database className="h-6 w-6 text-primary" /> {t("datasetsPage.title")}
              </h1>
              <p className="text-sm text-muted-foreground">{t("datasetsPage.subtitle")}</p>
            </div>
          </div>
        </FadeIn>

        <FadeIn delay={0.05}>
          <div className="relative w-full sm:max-w-xs">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input
              placeholder={t("datasetsPage.searchPlaceholder")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
              aria-label={t("datasetsPage.searchPlaceholder")}
            />
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          {isLoading ? (
            <p className="py-10 text-center text-sm text-muted-foreground">{t("common.loading")}</p>
          ) : apiError ? (
            <p className="py-10 text-center text-sm text-destructive">{apiError}</p>
          ) : visibleGroups.length === 0 ? (
            <EngineEmptyState
              icon={Database}
              title={t("dataset.empty")}
              hint={search ? t("datasetsPage.noResults") : t("datasetsPage.emptyHint")}
            />
          ) : (
            <div className="rounded-lg border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("datasetsPage.colName")}</TableHead>
                    <TableHead>{t("datasetsPage.colTag")}</TableHead>
                    <TableHead>{t("datasetsPage.colProject")}</TableHead>
                    <TableHead className="text-right">{t("datasetsPage.colSamples")}</TableHead>
                    <TableHead className="text-right">{t("datasetsPage.colSize")}</TableHead>
                    <TableHead>{t("datasetsPage.colCreated")}</TableHead>
                    <TableHead className="text-right">{t("datasetsPage.colActions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visibleGroups.map((group) => {
                    if (group.members.length < 2) {
                      return renderDatasetRow(group.members[0]);
                    }

                    const isExpanded = Boolean(expandedFamilies[group.rootKey]);
                    const newest = group.members[0];
                    const toggleLabel = isExpanded
                      ? t("datasetsPage.familyCollapse")
                      : t("datasetsPage.familyExpand");

                    return (
                      <Fragment key={`family-${group.rootKey}`}>
                        <TableRow
                          className="cursor-pointer bg-muted/10 hover:bg-muted/20"
                          onClick={() => toggleFamily(group.rootKey)}
                        >
                          <TableCell className="font-medium text-foreground">
                            <div className="flex items-center gap-2">
                              <Button
                                size="icon"
                                variant="ghost"
                                className="h-6 w-6 shrink-0"
                                aria-label={toggleLabel}
                                aria-expanded={isExpanded}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toggleFamily(group.rootKey);
                                }}
                              >
                                {isExpanded ? (
                                  <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                                ) : (
                                  <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                                )}
                              </Button>
                              <span>{group.baseName}</span>
                            </div>
                          </TableCell>
                          <TableCell>
                            <Badge variant="secondary" className="text-[10px]">
                              {t("datasetsPage.familyMembers").replace("{n}", String(group.members.length))}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            {group.projectId && projectNames[group.projectId] ? (
                              <span className="text-sm text-foreground">{projectNames[group.projectId]}</span>
                            ) : (
                              <Badge variant="secondary" className="text-[10px]">
                                {t("datasetsPage.noProject")}
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className="text-right text-sm text-muted-foreground">—</TableCell>
                          <TableCell className="text-right text-sm text-muted-foreground">—</TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {t("datasetsPage.familyLatest")}: {formatDateTime(newest.created_at)}
                          </TableCell>
                          <TableCell />
                        </TableRow>
                        {isExpanded && group.members.map((member) => renderDatasetRow(member))}
                      </Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </FadeIn>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
        title={t("dataset.delete")}
        description={
          deleteTarget ? (
            <>
              {t("dataset.deleteConfirm")} <strong>{deleteTarget.name}</strong>
            </>
          ) : undefined
        }
        confirmLabel={t("dataset.delete")}
        destructive
        loading={deleteMutation.isPending}
      />

      {renameTarget && (
        <DatasetRenameDialog
          dataset={renameTarget}
          open={renameTarget !== null}
          onOpenChange={(open) => !open && setRenameTarget(null)}
        />
      )}
    </PageTransition>
  );
}
