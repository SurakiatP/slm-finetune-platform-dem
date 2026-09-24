import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import {
  Trophy,
  Medal,
  ArrowUpDown,
  Eye,
  MessageSquare,
  GitCompare,
  Plus,
  Search,
  X,
  Star,
  Box,
  TrendingUp,
  ClipboardList,
} from "lucide-react";

import type { TaskType } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PageTransition, FadeIn } from "@/components/motion";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { TaskTypeBadge } from "@/components/engine/TaskTypeBadge";
import { useModels, useTrainings, useProjects, useEvaluations } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";
import { useTaskTypeLabel, useBaseModelLabel } from "@/lib/labels";
import { buildTrainingNameMap, modelDisplayName } from "@/components/model/modelNaming";
import { formatBytes, shortId } from "@/lib/format";
import { scalarMetrics } from "@/lib/metrics";

type SortKey = "primaryScore" | "secondaryScore" | "judge" | "size" | "name" | "created";

interface LeaderboardEntry {
  id: string;
  name: string;
  displayName: string;
  baseModel: string;
  baseModelLabel: string;
  projectId: string | null;
  projectName: string;
  taskType?: TaskType;
  sizeMb: number | null;
  ollamaTag: string | null;
  isDeployable: boolean;
  createdAt: string;
  evaluationCount: number;
  primaryMetricName: string | null;
  primaryScore: number | null;
  secondaryMetricName: string | null;
  secondaryScore: number | null;
  llmJudgeScore: number | null;
}

function extractEvaluationMetrics(metricsJson: Record<string, unknown> | null, taskType?: TaskType | string) {
  if (!metricsJson) return { primaryName: null, primaryVal: null, secondaryName: null, secondaryVal: null };

  let primaryName: string | null = null;
  let primaryVal: number | null = null;
  let secondaryName: string | null = null;
  let secondaryVal: number | null = null;

  if (taskType === "classification" || ("accuracy" in metricsJson && typeof metricsJson.accuracy === "number")) {
    if (typeof metricsJson.accuracy === "number") {
      primaryName = "Accuracy";
      primaryVal = metricsJson.accuracy <= 1 ? metricsJson.accuracy * 100 : metricsJson.accuracy;
    } else if (typeof metricsJson.exact_match === "number") {
      primaryName = "Exact Match";
      primaryVal = metricsJson.exact_match <= 1 ? metricsJson.exact_match * 100 : metricsJson.exact_match;
    }
    const f1 = metricsJson.f1_macro ?? metricsJson.f1;
    if (typeof f1 === "number") {
      secondaryName = "F1 Macro";
      secondaryVal = f1 <= 1 ? f1 * 100 : f1;
    }
  } else if (taskType === "qa" || "rougeL" in metricsJson || "rouge1" in metricsJson || "bleu" in metricsJson) {
    if (typeof metricsJson.rougeL === "number") {
      primaryName = "ROUGE-L";
      primaryVal = metricsJson.rougeL <= 1 ? metricsJson.rougeL * 100 : metricsJson.rougeL;
    } else if (typeof metricsJson.rouge1 === "number") {
      primaryName = "ROUGE-1";
      primaryVal = metricsJson.rouge1 <= 1 ? metricsJson.rouge1 * 100 : metricsJson.rouge1;
    } else if (typeof metricsJson.exact_match === "number") {
      primaryName = "Exact Match";
      primaryVal = metricsJson.exact_match <= 1 ? metricsJson.exact_match * 100 : metricsJson.exact_match;
    }
    if (typeof metricsJson.bleu === "number") {
      secondaryName = "BLEU";
      secondaryVal = metricsJson.bleu <= 1 ? metricsJson.bleu * 100 : metricsJson.bleu;
    } else if (typeof metricsJson.rouge1 === "number" && primaryName !== "ROUGE-1") {
      secondaryName = "ROUGE-1";
      secondaryVal = metricsJson.rouge1 <= 1 ? metricsJson.rouge1 * 100 : metricsJson.rouge1;
    }
  } else if (taskType === "tool_calling" || "json_validity" in metricsJson || "name_accuracy" in metricsJson) {
    if (typeof metricsJson.json_validity === "number") {
      primaryName = "JSON Validity";
      primaryVal = metricsJson.json_validity <= 1 ? metricsJson.json_validity * 100 : metricsJson.json_validity;
    } else if (typeof metricsJson.name_accuracy === "number") {
      primaryName = "Name Acc";
      primaryVal = metricsJson.name_accuracy <= 1 ? metricsJson.name_accuracy * 100 : metricsJson.name_accuracy;
    }
    if (typeof metricsJson.arg_accuracy === "number") {
      secondaryName = "Arg Acc";
      secondaryVal = metricsJson.arg_accuracy <= 1 ? metricsJson.arg_accuracy * 100 : metricsJson.arg_accuracy;
    } else if (typeof metricsJson.name_accuracy === "number" && primaryName !== "Name Acc") {
      secondaryName = "Name Acc";
      secondaryVal = metricsJson.name_accuracy <= 1 ? metricsJson.name_accuracy * 100 : metricsJson.name_accuracy;
    }
  }

  if (primaryVal === null) {
    const scalars = scalarMetrics(metricsJson);
    const keys = Object.keys(scalars).filter((k) => k !== "n" && k !== "llm_judge_score");
    if (keys.length > 0) {
      primaryName = keys[0];
      const v = scalars[keys[0]];
      primaryVal = v <= 1 ? v * 100 : v;
      if (keys.length > 1) {
        secondaryName = keys[1];
        const v2 = scalars[keys[1]];
        secondaryVal = v2 <= 1 ? v2 * 100 : v2;
      }
    }
  }

  return { primaryName, primaryVal, secondaryName, secondaryVal };
}

export default function Leaderboard() {
  const { t } = useLanguage();
  const getTaskTypeLabel = useTaskTypeLabel();
  const getBaseModelLabel = useBaseModelLabel();

  const [search, setSearch] = useState("");
  const [taskFilter, setTaskFilter] = useState("all");
  const [evalFilter, setEvalFilter] = useState<"all" | "evaluated">("all");
  const [sortBy, setSortBy] = useState<SortKey>("primaryScore");
  const [sortAsc, setSortAsc] = useState(false);

  const { data: modelsPage, isLoading: modelsLoading, error: modelsError } = useModels(undefined, { limit: 200 });
  const { data: trainingsPage, isLoading: trainingsLoading } = useTrainings({ limit: 200 });
  const { data: projectsPage, isLoading: projectsLoading } = useProjects({ limit: 200 });
  const { data: evaluationsPage, isLoading: evaluationsLoading } = useEvaluations({ limit: 200 });

  const isLoading = modelsLoading || trainingsLoading || projectsLoading || evaluationsLoading;

  const trainingMap = useMemo(() => {
    const map = new Map<string, (typeof trainingsPage.items)[0]>();
    for (const tr of trainingsPage?.items ?? []) {
      map.set(tr.id, tr);
    }
    return map;
  }, [trainingsPage]);

  const projectMap = useMemo(() => {
    const map = new Map<string, (typeof projectsPage.items)[0]>();
    for (const pr of projectsPage?.items ?? []) {
      map.set(pr.id, pr);
    }
    return map;
  }, [projectsPage]);

  const trainingNames = useMemo(() => buildTrainingNameMap(trainingsPage?.items), [trainingsPage]);

  const evaluationsByModel = useMemo(() => {
    const map = new Map<string, (typeof evaluationsPage.items)>();
    for (const ev of evaluationsPage?.items ?? []) {
      if (ev.status === "completed") {
        const arr = map.get(ev.model_artifact_id) ?? [];
        arr.push(ev);
        map.set(ev.model_artifact_id, arr);
      }
    }
    return map;
  }, [evaluationsPage]);

  const entries: LeaderboardEntry[] = useMemo(() => {
    const models = modelsPage?.items ?? [];
    return models.map((model) => {
      const training = trainingMap.get(model.training_job_id);
      const project = training?.project_id ? projectMap.get(training.project_id) : undefined;
      const modelEvals = evaluationsByModel.get(model.id) ?? [];

      let bestPrimaryVal: number | null = null;
      let bestPrimaryName: string | null = null;
      let bestSecondaryVal: number | null = null;
      let bestSecondaryName: string | null = null;
      let bestJudge: number | null = null;

      for (const ev of modelEvals) {
        const { primaryName, primaryVal, secondaryName, secondaryVal } = extractEvaluationMetrics(
          ev.metrics_json,
          project?.task_type
        );
        if (primaryVal !== null && (bestPrimaryVal === null || primaryVal > bestPrimaryVal)) {
          bestPrimaryVal = primaryVal;
          bestPrimaryName = primaryName;
          bestSecondaryVal = secondaryVal;
          bestSecondaryName = secondaryName;
        }
        if (ev.llm_judge_score !== null && (bestJudge === null || ev.llm_judge_score > bestJudge)) {
          bestJudge = ev.llm_judge_score;
        }
      }

      if (bestPrimaryVal === null && training?.best_metric_value != null) {
        bestPrimaryVal =
          training.best_metric_value <= 1 ? training.best_metric_value * 100 : training.best_metric_value;
        bestPrimaryName = "Validation";
      }

      const dispName = modelDisplayName(model, trainingNames);

      return {
        id: model.id,
        name: model.name,
        displayName: dispName,
        baseModel: model.base_model,
        baseModelLabel: getBaseModelLabel(model.base_model),
        projectId: project?.id ?? null,
        projectName: project?.name ?? "—",
        taskType: project?.task_type,
        sizeMb: model.size_mb,
        ollamaTag: model.ollama_model_tag,
        isDeployable: !!(model.ollama_model_tag || model.gguf_uri),
        createdAt: model.created_at,
        evaluationCount: modelEvals.length,
        primaryMetricName: bestPrimaryName,
        primaryScore: bestPrimaryVal,
        secondaryMetricName: bestSecondaryName,
        secondaryScore: bestSecondaryVal,
        llmJudgeScore: bestJudge,
      };
    });
  }, [modelsPage, trainingMap, projectMap, evaluationsByModel, trainingNames, getBaseModelLabel]);

  const evaluatedEntries = useMemo(() => {
    return entries.filter((e) => e.primaryScore !== null || e.llmJudgeScore !== null);
  }, [entries]);

  const topModel = useMemo(() => {
    if (evaluatedEntries.length === 0) return null;
    return [...evaluatedEntries].sort((a, b) => (b.primaryScore ?? 0) - (a.primaryScore ?? 0))[0];
  }, [evaluatedEntries]);

  const avgScore = useMemo(() => {
    const scored = evaluatedEntries.filter((e) => e.primaryScore !== null);
    if (scored.length === 0) return null;
    return scored.reduce((acc, e) => acc + (e.primaryScore ?? 0), 0) / scored.length;
  }, [evaluatedEntries]);

  const filtered = useMemo(() => {
    return entries.filter((entry) => {
      if (evalFilter === "evaluated" && entry.primaryScore === null && entry.llmJudgeScore === null) {
        return false;
      }
      if (taskFilter !== "all" && entry.taskType !== taskFilter) {
        return false;
      }
      if (search.trim()) {
        const query = search.toLowerCase();
        const matchesName = entry.displayName.toLowerCase().includes(query) || entry.name.toLowerCase().includes(query);
        const matchesProject = entry.projectName.toLowerCase().includes(query);
        const matchesBase = entry.baseModelLabel.toLowerCase().includes(query) || entry.baseModel.toLowerCase().includes(query);
        if (!matchesName && !matchesProject && !matchesBase) {
          return false;
        }
      }
      return true;
    });
  }, [entries, evalFilter, taskFilter, search]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let cmp = 0;
      if (sortBy === "primaryScore") {
        const va = a.primaryScore;
        const vb = b.primaryScore;
        if (va === null && vb === null) cmp = 0;
        else if (va === null) return 1;
        else if (vb === null) return -1;
        else cmp = va - vb;
      } else if (sortBy === "secondaryScore") {
        const va = a.secondaryScore;
        const vb = b.secondaryScore;
        if (va === null && vb === null) cmp = 0;
        else if (va === null) return 1;
        else if (vb === null) return -1;
        else cmp = va - vb;
      } else if (sortBy === "judge") {
        const va = a.llmJudgeScore;
        const vb = b.llmJudgeScore;
        if (va === null && vb === null) cmp = 0;
        else if (va === null) return 1;
        else if (vb === null) return -1;
        else cmp = va - vb;
      } else if (sortBy === "size") {
        const va = a.sizeMb ?? 0;
        const vb = b.sizeMb ?? 0;
        cmp = va - vb;
      } else if (sortBy === "name") {
        cmp = a.displayName.localeCompare(b.displayName);
      } else if (sortBy === "created") {
        cmp = new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
      }
      return sortAsc ? cmp : -cmp;
    });
  }, [filtered, sortBy, sortAsc]);

  const rankedEntries = useMemo(() => {
    let currentRank = 0;
    return sorted.map((entry) => {
      let rank: number | null = null;
      if (entry.primaryScore !== null || entry.llmJudgeScore !== null) {
        currentRank += 1;
        rank = currentRank;
      }
      return { ...entry, rank };
    });
  }, [sorted]);

  const handleSort = (key: SortKey) => {
    if (sortBy === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortBy(key);
      setSortAsc(false);
    }
  };

  const handleClearFilters = () => {
    setSearch("");
    setTaskFilter("all");
    setEvalFilter("all");
  };

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        {/* Header */}
        <FadeIn>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                <Trophy className="h-6 w-6 text-amber-500" /> {t("leaderboard.title")}
              </h1>
              <p className="text-sm text-muted-foreground">{t("leaderboard.subtitle")}</p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" asChild>
                <Link to="/models/compare" className="gap-2">
                  <GitCompare className="h-4 w-4" /> {t("leaderboard.compareAction")}
                </Link>
              </Button>
              <Button size="sm" asChild>
                <Link to="/projects/new" className="gap-2">
                  <Plus className="h-4 w-4" /> {t("leaderboard.newProjectAction")}
                </Link>
              </Button>
            </div>
          </div>
        </FadeIn>

        {/* Overview Stats Cards */}
        <FadeIn delay={0.05}>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <Card>
              <CardContent className="p-4 flex items-center justify-between">
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">{t("leaderboard.totalModels")}</p>
                  <p className="text-2xl font-bold text-foreground">{entries.length}</p>
                  <p className="text-[11px] text-muted-foreground">
                    {t("leaderboard.totalModelsDesc").replace("{count}", String(entries.length))}
                  </p>
                </div>
                <div className="p-3 bg-muted rounded-full">
                  <Box className="h-5 w-5 text-muted-foreground" />
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4 flex items-center justify-between">
                <div className="space-y-1 min-w-0 flex-1 mr-2">
                  <p className="text-xs font-medium text-muted-foreground">{t("leaderboard.bestModel")}</p>
                  <p className="text-lg font-bold text-foreground truncate" title={topModel?.displayName ?? "—"}>
                    {topModel ? topModel.displayName : "—"}
                  </p>
                  <p className="text-[11px] text-primary font-medium">
                    {topModel?.primaryScore != null ? `${topModel.primaryScore.toFixed(1)}% (${topModel.primaryMetricName})` : "—"}
                  </p>
                </div>
                <div className="p-3 bg-amber-500/10 rounded-full shrink-0">
                  <Trophy className="h-5 w-5 text-amber-500" />
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4 flex items-center justify-between">
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">{t("leaderboard.avgAccuracy")}</p>
                  <p className="text-2xl font-bold text-foreground">
                    {avgScore !== null ? `${avgScore.toFixed(1)}%` : "—"}
                  </p>
                  <p className="text-[11px] text-muted-foreground">{t("leaderboard.avgScoreDesc")}</p>
                </div>
                <div className="p-3 bg-emerald-500/10 rounded-full">
                  <TrendingUp className="h-5 w-5 text-emerald-500" />
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4 flex items-center justify-between">
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">{t("leaderboard.evalCount")}</p>
                  <p className="text-2xl font-bold text-foreground">
                    {evaluatedEntries.length} / {entries.length}
                  </p>
                  <p className="text-[11px] text-muted-foreground">{t("leaderboard.evalCountDesc")}</p>
                </div>
                <div className="p-3 bg-sky-500/10 rounded-full">
                  <ClipboardList className="h-5 w-5 text-sky-500" />
                </div>
              </CardContent>
            </Card>
          </div>
        </FadeIn>

        {/* Filter and Search Controls */}
        <FadeIn delay={0.1}>
          <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center justify-between">
            <div className="flex flex-1 flex-col sm:flex-row gap-3 items-stretch sm:items-center">
              <div className="relative flex-1 sm:max-w-xs">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                <Input
                  placeholder={t("leaderboard.searchPlaceholder")}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-9 pr-8"
                  aria-label={t("leaderboard.searchPlaceholder")}
                />
                {search && (
                  <button
                    type="button"
                    onClick={() => setSearch("")}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>

              <Select value={taskFilter} onValueChange={setTaskFilter}>
                <SelectTrigger className="w-full sm:w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("leaderboard.allTasks")}</SelectItem>
                  <SelectItem value="classification">{getTaskTypeLabel("classification")}</SelectItem>
                  <SelectItem value="tool_calling">{getTaskTypeLabel("tool_calling")}</SelectItem>
                  <SelectItem value="qa">{getTaskTypeLabel("qa")}</SelectItem>
                </SelectContent>
              </Select>

              <Select value={evalFilter} onValueChange={(v: "all" | "evaluated") => setEvalFilter(v)}>
                <SelectTrigger className="w-full sm:w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("leaderboard.totalModels")}</SelectItem>
                  <SelectItem value="evaluated">{t("leaderboard.evaluatedOnly")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center gap-2">
              <Select value={sortBy} onValueChange={(v: SortKey) => setSortBy(v)}>
                <SelectTrigger className="w-full sm:w-44 text-xs">
                  <span className="text-muted-foreground mr-1">{t("leaderboard.sortBy")}:</span>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="primaryScore">{t("leaderboard.primaryScore")}</SelectItem>
                  <SelectItem value="secondaryMetric">{t("leaderboard.secondaryMetric")}</SelectItem>
                  <SelectItem value="judge">{t("leaderboard.judge")}</SelectItem>
                  <SelectItem value="size">{t("leaderboard.size")}</SelectItem>
                  <SelectItem value="name">{t("leaderboard.model")}</SelectItem>
                  <SelectItem value="created">Date</SelectItem>
                </SelectContent>
              </Select>
              <Button
                variant="outline"
                size="icon"
                className="h-9 w-9 shrink-0"
                onClick={() => setSortAsc(!sortAsc)}
                title="Toggle sort direction"
              >
                <ArrowUpDown className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </FadeIn>

        {/* Main Content Area */}
        <FadeIn delay={0.15}>
          {isLoading ? (
            <Card>
              <CardContent className="p-8 text-center space-y-3">
                <div className="flex justify-center">
                  <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                </div>
                <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
              </CardContent>
            </Card>
          ) : modelsError ? (
            <Card>
              <CardContent className="p-8 text-center text-destructive">
                <p className="text-sm">{t("common.error")}</p>
              </CardContent>
            </Card>
          ) : entries.length === 0 ? (
            <EngineEmptyState
              icon={Trophy}
              title={t("leaderboard.emptyTitle")}
              hint={t("leaderboard.emptyDesc")}
              action={
                <Button asChild size="sm">
                  <Link to="/projects/new">{t("leaderboard.startTraining")}</Link>
                </Button>
              }
            />
          ) : rankedEntries.length === 0 ? (
            <Card className="border-dashed">
              <CardContent className="py-12 text-center space-y-3">
                <Search className="h-8 w-8 mx-auto text-muted-foreground" />
                <h3 className="font-semibold text-foreground">{t("leaderboard.noMatchingTitle")}</h3>
                <p className="text-xs text-muted-foreground max-w-sm mx-auto">
                  {t("leaderboard.noMatchingDesc")}
                </p>
                <Button variant="outline" size="sm" onClick={handleClearFilters}>
                  {t("leaderboard.clearFilters")}
                </Button>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardHeader className="py-4 px-6 border-b border-border">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base font-semibold">{t("leaderboard.rankings")}</CardTitle>
                  <span className="text-xs text-muted-foreground">
                    {rankedEntries.length} {t("leaderboard.model")}
                  </span>
                </div>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border bg-muted/30 text-xs text-muted-foreground">
                        <th className="py-3 px-4 text-left font-medium w-16">{t("leaderboard.rank")}</th>
                        <th
                          className="py-3 px-4 text-left font-medium cursor-pointer hover:text-foreground"
                          onClick={() => handleSort("name")}
                        >
                          <div className="inline-flex items-center gap-1">
                            {t("leaderboard.model")}
                            <ArrowUpDown className="h-3 w-3" />
                          </div>
                        </th>
                        <th className="py-3 px-4 text-left font-medium">{t("leaderboard.project")}</th>
                        <th className="py-3 px-4 text-left font-medium">{t("leaderboard.base")}</th>
                        <th className="py-3 px-4 text-left font-medium">{t("leaderboard.task")}</th>
                        <th
                          className="py-3 px-4 text-right font-medium cursor-pointer hover:text-foreground"
                          onClick={() => handleSort("primaryScore")}
                        >
                          <div className="inline-flex items-center gap-1 justify-end">
                            {t("leaderboard.primaryScore")}
                            <ArrowUpDown className="h-3 w-3" />
                          </div>
                        </th>
                        <th
                          className="py-3 px-4 text-right font-medium cursor-pointer hover:text-foreground"
                          onClick={() => handleSort("secondaryScore")}
                        >
                          <div className="inline-flex items-center gap-1 justify-end">
                            {t("leaderboard.secondaryMetric")}
                            <ArrowUpDown className="h-3 w-3" />
                          </div>
                        </th>
                        <th
                          className="py-3 px-4 text-right font-medium cursor-pointer hover:text-foreground"
                          onClick={() => handleSort("judge")}
                        >
                          <div className="inline-flex items-center gap-1 justify-end">
                            {t("leaderboard.judge")}
                            <ArrowUpDown className="h-3 w-3" />
                          </div>
                        </th>
                        <th
                          className="py-3 px-4 text-right font-medium cursor-pointer hover:text-foreground"
                          onClick={() => handleSort("size")}
                        >
                          <div className="inline-flex items-center gap-1 justify-end">
                            {t("leaderboard.size")}
                            <ArrowUpDown className="h-3 w-3" />
                          </div>
                        </th>
                        <th className="py-3 px-4 text-center font-medium">{t("leaderboard.evalCount")}</th>
                        <th className="py-3 px-4 text-right font-medium">{t("leaderboard.actions")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/60">
                      {rankedEntries.map((m) => (
                        <tr key={m.id} className="hover:bg-muted/40 transition-colors">
                          {/* Rank column */}
                          <td className="py-3 px-4">
                            <div className="flex items-center justify-center">
                              {m.rank === 1 ? (
                                <div
                                  className="flex h-7 w-7 items-center justify-center rounded-full bg-amber-500/15 text-amber-500 font-bold"
                                  title="#1"
                                >
                                  <Trophy className="h-4 w-4" />
                                </div>
                              ) : m.rank === 2 ? (
                                <div
                                  className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-400/15 text-slate-400 font-bold"
                                  title="#2"
                                >
                                  <Medal className="h-4 w-4" />
                                </div>
                              ) : m.rank === 3 ? (
                                <div
                                  className="flex h-7 w-7 items-center justify-center rounded-full bg-amber-700/15 text-amber-700 font-bold"
                                  title="#3"
                                >
                                  <Medal className="h-4 w-4" />
                                </div>
                              ) : m.rank ? (
                                <span className="font-mono text-xs font-semibold text-muted-foreground">
                                  #{m.rank}
                                </span>
                              ) : (
                                <span className="text-muted-foreground text-xs">—</span>
                              )}
                            </div>
                          </td>

                          {/* Model name + deployable badge */}
                          <td className="py-3 px-4">
                            <div className="flex flex-col">
                              <div className="flex items-center gap-1.5">
                                <Link
                                  to={`/models/${m.id}`}
                                  className="font-medium text-xs font-mono hover:text-primary transition-colors truncate max-w-[200px]"
                                  title={m.displayName}
                                >
                                  {m.displayName}
                                </Link>
                                {m.isDeployable && (
                                  <Badge variant="outline" className="text-[9px] px-1 py-0 border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                                    {t("leaderboard.deployable")}
                                  </Badge>
                                )}
                              </div>
                              <span className="text-[11px] text-muted-foreground font-mono truncate max-w-[180px]">
                                {shortId(m.id)}
                              </span>
                            </div>
                          </td>

                          {/* Project name */}
                          <td className="py-3 px-4 text-xs text-muted-foreground">
                            {m.projectId ? (
                              <Link
                                to={`/projects/${m.projectId}`}
                                className="hover:underline hover:text-foreground"
                              >
                                {m.projectName}
                              </Link>
                            ) : (
                              <span>{m.projectName}</span>
                            )}
                          </td>

                          {/* Base Model badge */}
                          <td className="py-3 px-4">
                            <Badge variant="outline" className="text-[10px] font-normal truncate max-w-[120px]">
                              {m.baseModelLabel}
                            </Badge>
                          </td>

                          {/* Task Type badge */}
                          <td className="py-3 px-4">
                            <TaskTypeBadge taskType={m.taskType} label={getTaskTypeLabel(m.taskType)} />
                          </td>

                          {/* Primary Score */}
                          <td className="py-3 px-4 text-right">
                            {m.primaryScore !== null ? (
                              <div className="flex flex-col items-end gap-1">
                                <span className="font-mono font-semibold text-xs text-foreground">
                                  {m.primaryScore.toFixed(1)}%
                                </span>
                                <div className="w-20">
                                  <Progress value={Math.min(100, Math.max(0, m.primaryScore))} className="h-1.5" />
                                </div>
                                <span className="text-[10px] text-muted-foreground">{m.primaryMetricName}</span>
                              </div>
                            ) : (
                              <Badge variant="secondary" className="text-[10px] font-normal text-muted-foreground">
                                {t("leaderboard.notEvaluated")}
                              </Badge>
                            )}
                          </td>

                          {/* Secondary Metric */}
                          <td className="py-3 px-4 text-right">
                            {m.secondaryScore !== null ? (
                              <div className="flex flex-col items-end">
                                <span className="font-mono text-xs text-foreground">
                                  {m.secondaryScore.toFixed(1)}%
                                </span>
                                <span className="text-[10px] text-muted-foreground">{m.secondaryMetricName}</span>
                              </div>
                            ) : (
                              <span className="text-muted-foreground text-xs">—</span>
                            )}
                          </td>

                          {/* LLM Judge */}
                          <td className="py-3 px-4 text-right">
                            {m.llmJudgeScore !== null ? (
                              <div className="inline-flex items-center gap-1 font-mono text-xs font-medium text-amber-500">
                                <Star className="h-3 w-3 fill-amber-500" />
                                {m.llmJudgeScore.toFixed(1)}
                                <span className="text-[10px] text-muted-foreground font-normal">/ 5.0</span>
                              </div>
                            ) : (
                              <span className="text-muted-foreground text-xs">—</span>
                            )}
                          </td>

                          {/* Size */}
                          <td className="py-3 px-4 text-right font-mono text-xs text-muted-foreground">
                            {m.sizeMb !== null ? formatBytes(m.sizeMb * 1024 * 1024) : "—"}
                          </td>

                          {/* Evaluations count */}
                          <td className="py-3 px-4 text-center">
                            {m.evaluationCount > 0 ? (
                              <Badge variant="outline" className="text-[10px] font-normal">
                                {t("leaderboard.evalRuns").replace("{count}", String(m.evaluationCount))}
                              </Badge>
                            ) : (
                              <span className="text-muted-foreground text-xs">—</span>
                            )}
                          </td>

                          {/* Actions */}
                          <td className="py-3 px-4 text-right">
                            <div className="flex items-center justify-end gap-1">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-muted-foreground hover:text-foreground"
                                asChild
                                title={t("leaderboard.viewDetails")}
                              >
                                <Link to={`/models/${m.id}`}>
                                  <Eye className="h-3.5 w-3.5" />
                                </Link>
                              </Button>

                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-muted-foreground hover:text-foreground"
                                asChild
                                title={t("leaderboard.compareAction")}
                              >
                                <Link to="/models/compare">
                                  <GitCompare className="h-3.5 w-3.5" />
                                </Link>
                              </Button>

                              {m.ollamaTag && (
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-7 w-7 text-primary hover:text-primary hover:bg-primary/10"
                                  asChild
                                  title={t("leaderboard.playgroundAction")}
                                >
                                  <Link to={`/playground?model=${encodeURIComponent(m.ollamaTag)}`}>
                                    <MessageSquare className="h-3.5 w-3.5" />
                                  </Link>
                                </Button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}
        </FadeIn>
      </div>
    </PageTransition>
  );
}
