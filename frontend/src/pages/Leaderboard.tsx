import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PageTransition, FadeIn } from "@/components/motion";
import {
  Trophy,
  Medal,
  Award,
  ArrowUpDown,
  Search,
  ExternalLink,
  MessageSquare,
  GitCompare,
  Box,
  Scale,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { useEvaluations, useModels, useDatasets, useTrainings } from "@/hooks/queries";
import { buildTrainingNameMap, modelDisplayName } from "@/components/model/modelNaming";
import { shortId } from "@/lib/format";
import type { TaskType } from "@/api/types";

type SortKey = "score" | "accuracy" | "judge" | "date";

interface LeaderboardEntry {
  rank: number;
  evaluationId: string;
  modelId: string;
  modelName: string;
  baseModel: string;
  datasetId: string;
  datasetName: string;
  taskType: TaskType | "unknown";
  accuracy: number | null;
  f1: number | null;
  judgeScore: number | null;
  overallScore: number;
  createdAt: string;
  ollamaTag: string | null;
}

export default function Leaderboard() {
  const [filterTask, setFilterTask] = useState<string>("all");
  const [sortBy, setSortBy] = useState<SortKey>("score");
  const [sortAsc, setSortAsc] = useState(false);
  const [search, setSearch] = useState("");

  const { data: evaluationsPage, isLoading: evalLoading } = useEvaluations({
    status: "completed",
    limit: 200,
  });
  const { data: modelsPage, isLoading: modelsLoading } = useModels(undefined, { limit: 200 });
  const { data: datasetsPage } = useDatasets(undefined, { limit: 200 });
  const { data: trainingsPage } = useTrainings({ limit: 200 });

  const loading = evalLoading || modelsLoading;

  const trainingNames = useMemo(() => buildTrainingNameMap(trainingsPage?.items), [trainingsPage]);

  const modelMap = useMemo(() => {
    const map = new Map<string, (typeof modelsPage.items)[0]>();
    for (const m of modelsPage?.items ?? []) {
      map.set(m.id, m);
    }
    return map;
  }, [modelsPage]);

  const datasetMap = useMemo(() => {
    const map = new Map<string, (typeof datasetsPage.items)[0]>();
    for (const d of datasetsPage?.items ?? []) {
      map.set(d.id, d);
    }
    return map;
  }, [datasetsPage]);

  // Aggregate evaluations into leaderboard entries
  const entries = useMemo<LeaderboardEntry[]>(() => {
    if (!evaluationsPage?.items) return [];

    const list: LeaderboardEntry[] = [];

    for (const ev of evaluationsPage.items) {
      const model = modelMap.get(ev.model_artifact_id);
      const dataset = datasetMap.get(ev.dataset_id);

      const metrics = (ev.metrics_json as Record<string, unknown>) ?? {};
      const accuracyRaw = typeof metrics.accuracy === "number" ? metrics.accuracy : null;
      const f1Raw = typeof metrics.f1 === "number" ? metrics.f1 : (typeof metrics.f1_score === "number" ? metrics.f1_score : null);
      const judgeRaw = ev.llm_judge_score;

      // Normalize overall score out of 100
      let overall = 0;
      if (judgeRaw != null && accuracyRaw != null) {
        overall = ((judgeRaw / 5) * 50) + (accuracyRaw * 50);
      } else if (judgeRaw != null) {
        overall = (judgeRaw / 5) * 100;
      } else if (accuracyRaw != null) {
        overall = accuracyRaw <= 1 ? accuracyRaw * 100 : accuracyRaw;
      } else if (f1Raw != null) {
        overall = f1Raw <= 1 ? f1Raw * 100 : f1Raw;
      }

      list.push({
        rank: 0,
        evaluationId: ev.id,
        modelId: ev.model_artifact_id,
        modelName: model ? modelDisplayName(model, trainingNames) : shortId(ev.model_artifact_id),
        baseModel: model?.base_model ?? "Unknown",
        datasetId: ev.dataset_id,
        datasetName: dataset?.name ?? shortId(ev.dataset_id),
        taskType: dataset?.task_type ?? "unknown",
        accuracy: accuracyRaw != null ? (accuracyRaw <= 1 ? accuracyRaw * 100 : accuracyRaw) : null,
        f1: f1Raw != null ? (f1Raw <= 1 ? f1Raw * 100 : f1Raw) : null,
        judgeScore: judgeRaw,
        overallScore: Math.round(overall * 10) / 10,
        createdAt: ev.created_at,
        ollamaTag: model?.ollama_model_tag ?? null,
      });
    }

    return list;
  }, [evaluationsPage, modelMap, datasetMap, trainingNames]);

  const filtered = useMemo(() => {
    return entries.filter((e) => {
      const matchTask = filterTask === "all" || e.taskType === filterTask;
      const matchSearch =
        !search ||
        e.modelName.toLowerCase().includes(search.toLowerCase()) ||
        e.baseModel.toLowerCase().includes(search.toLowerCase()) ||
        e.datasetName.toLowerCase().includes(search.toLowerCase());
      return matchTask && matchSearch;
    });
  }, [entries, filterTask, search]);

  const sorted = useMemo(() => {
    const list = [...filtered].sort((a, b) => {
      let va = 0;
      let vb = 0;
      switch (sortBy) {
        case "accuracy":
          va = a.accuracy ?? 0;
          vb = b.accuracy ?? 0;
          break;
        case "judge":
          va = a.judgeScore ?? 0;
          vb = b.judgeScore ?? 0;
          break;
        case "date":
          va = new Date(a.createdAt).getTime();
          vb = new Date(b.createdAt).getTime();
          break;
        case "score":
        default:
          va = a.overallScore;
          vb = b.overallScore;
          break;
      }
      return sortAsc ? va - vb : vb - va;
    });

    // Assign rank 1-based
    return list.map((item, idx) => ({ ...item, rank: idx + 1 }));
  }, [filtered, sortBy, sortAsc]);

  const handleSort = (key: SortKey) => {
    if (sortBy === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortBy(key);
      setSortAsc(false);
    }
  };

  const avgOverall = sorted.length
    ? (sorted.reduce((s, m) => s + m.overallScore, 0) / sorted.length).toFixed(1)
    : "0.0";
  const bestModel = sorted[0];

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                <Trophy className="h-6 w-6 text-amber-500" /> Model Leaderboard
              </h1>
              <p className="text-sm text-muted-foreground">
                Rankings of fine-tuned models based on standardized benchmark and LLM judge evaluations.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" asChild>
                <Link to="/models/compare" className="gap-1.5">
                  <GitCompare className="h-3.5 w-3.5" /> Compare Models
                </Link>
              </Button>
            </div>
          </div>
        </FadeIn>

        {!loading && entries.length === 0 ? (
          <EngineEmptyState
            icon={Scale}
            title="No completed evaluations found"
            hint="Run evaluations on your fine-tuned models to populate the leaderboard with benchmark results and rankings."
            action={
              <Button asChild size="sm">
                <Link to="/models">Choose a model to evaluate</Link>
              </Button>
            }
          />
        ) : (
          <>
            {/* Top Stats Cards */}
            <FadeIn delay={0.05}>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <Card>
                  <CardContent className="p-4 flex items-center gap-3">
                    <div className="p-2.5 rounded-lg bg-primary/10 text-primary">
                      <Box className="h-5 w-5" />
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Evaluated Models</p>
                      <p className="text-2xl font-bold text-foreground">{sorted.length}</p>
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardContent className="p-4 flex items-center gap-3">
                    <div className="p-2.5 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                      <Award className="h-5 w-5" />
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Average Score</p>
                      <p className="text-2xl font-bold text-foreground">{avgOverall}%</p>
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardContent className="p-4 flex items-center gap-3">
                    <div className="p-2.5 rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
                      <Trophy className="h-5 w-5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs text-muted-foreground">Top Performer</p>
                      <p className="text-lg font-bold text-foreground truncate" title={bestModel?.modelName}>
                        {bestModel?.modelName || "—"}
                      </p>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </FadeIn>

            {/* Filters and Controls */}
            <FadeIn delay={0.1}>
              <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center justify-between">
                <div className="relative flex-1 max-w-sm">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="Search by model or dataset..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="pl-9"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <Select value={filterTask} onValueChange={setFilterTask}>
                    <SelectTrigger className="w-[170px]">
                      <SelectValue placeholder="All Tasks" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All Tasks</SelectItem>
                      <SelectItem value="classification">Classification</SelectItem>
                      <SelectItem value="qa">QA</SelectItem>
                      <SelectItem value="tool_calling">Tool Calling</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </FadeIn>

            {/* Leaderboard Table */}
            <FadeIn delay={0.15}>
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base flex items-center gap-2">
                    <Trophy className="h-4 w-4 text-amber-500" /> Rankings
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-14 text-center">#</TableHead>
                        <TableHead>Model</TableHead>
                        <TableHead>Task</TableHead>
                        <TableHead>Evaluation Dataset</TableHead>
                        <TableHead
                          className="cursor-pointer select-none text-right"
                          onClick={() => handleSort("accuracy")}
                        >
                          <div className="flex items-center justify-end gap-1">
                            Accuracy <ArrowUpDown className="h-3 w-3" />
                          </div>
                        </TableHead>
                        <TableHead
                          className="cursor-pointer select-none text-right"
                          onClick={() => handleSort("judge")}
                        >
                          <div className="flex items-center justify-end gap-1">
                            LLM Judge <ArrowUpDown className="h-3 w-3" />
                          </div>
                        </TableHead>
                        <TableHead
                          className="cursor-pointer select-none text-right font-semibold"
                          onClick={() => handleSort("score")}
                        >
                          <div className="flex items-center justify-end gap-1">
                            Overall Score <ArrowUpDown className="h-3 w-3" />
                          </div>
                        </TableHead>
                        <TableHead className="w-28 text-right">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {sorted.map((item) => (
                        <TableRow key={item.evaluationId} className="hover:bg-muted/40">
                          <TableCell className="text-center font-bold">
                            {item.rank === 1 ? (
                              <span className="inline-flex items-center justify-center h-6 w-6 rounded-full bg-amber-500/20 text-amber-600 dark:text-amber-400">
                                <Medal className="h-4 w-4" />
                              </span>
                            ) : item.rank === 2 ? (
                              <span className="inline-flex items-center justify-center h-6 w-6 rounded-full bg-slate-400/20 text-slate-600 dark:text-slate-300">
                                <Medal className="h-4 w-4" />
                              </span>
                            ) : item.rank === 3 ? (
                              <span className="inline-flex items-center justify-center h-6 w-6 rounded-full bg-amber-700/20 text-amber-700 dark:text-amber-500">
                                <Medal className="h-4 w-4" />
                              </span>
                            ) : (
                              <span className="text-xs text-muted-foreground">{item.rank}</span>
                            )}
                          </TableCell>

                          <TableCell>
                            <div className="space-y-0.5">
                              <Link
                                to={`/models/${item.modelId}`}
                                className="font-semibold text-foreground hover:text-primary transition-colors flex items-center gap-1"
                              >
                                {item.modelName}
                                <ExternalLink className="h-3 w-3 text-muted-foreground opacity-60" />
                              </Link>
                              <p className="text-[11px] text-muted-foreground font-mono">
                                Base: {item.baseModel.split("/").pop()}
                              </p>
                            </div>
                          </TableCell>

                          <TableCell>
                            <Badge variant="outline" className="text-[10px] uppercase">
                              {item.taskType}
                            </Badge>
                          </TableCell>

                          <TableCell className="text-xs text-muted-foreground">
                            {item.datasetName}
                          </TableCell>

                          <TableCell className="text-right font-mono text-xs">
                            {item.accuracy != null ? `${item.accuracy.toFixed(1)}%` : "—"}
                          </TableCell>

                          <TableCell className="text-right font-mono text-xs">
                            {item.judgeScore != null ? (
                              <span className="font-semibold text-primary">
                                {item.judgeScore.toFixed(2)} / 5
                              </span>
                            ) : (
                              "—"
                            )}
                          </TableCell>

                          <TableCell className="text-right font-bold text-sm">
                            <span className="text-primary">{item.overallScore.toFixed(1)}%</span>
                          </TableCell>

                          <TableCell className="text-right">
                            <div className="flex items-center justify-end gap-1">
                              {item.ollamaTag && (
                                <Button variant="ghost" size="icon" className="h-7 w-7" asChild title="Test in Playground">
                                  <Link to={`/playground?model=${encodeURIComponent(item.ollamaTag)}`}>
                                    <MessageSquare className="h-3.5 w-3.5" />
                                  </Link>
                                </Button>
                              )}
                              <Button variant="ghost" size="sm" className="text-xs h-7 px-2" asChild>
                                <Link to={`/models/${item.modelId}`}>
                                  View
                                </Link>
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </FadeIn>
          </>
        )}
      </div>
    </PageTransition>
  );
}
