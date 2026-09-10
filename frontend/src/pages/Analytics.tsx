import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PageTransition, FadeIn } from "@/components/motion";
import {
  BarChart3,
  TrendingUp,
  Coins,
  Cpu,
  Clock,
  CheckCircle2,
  AlertTriangle,
  FolderKanban,
  Activity,
  Layers,
} from "lucide-react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
  Cell,
  PieChart,
  Pie,
} from "recharts";
import { useUsageSummary, useTrainings, useProjects, useModels } from "@/hooks/queries";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { formatNumber, formatUsd } from "@/lib/format";

const STAGE_COLORS: Record<string, string> = {
  sdg: "hsl(var(--primary))",
  training: "hsl(217, 91%, 60%)",
  inference: "hsl(142, 71%, 45%)",
  judge: "hsl(38, 92%, 50%)",
  eval: "hsl(280, 80%, 60%)",
  other: "hsl(var(--muted-foreground))",
};

const CHART_COLORS = [
  "hsl(var(--primary))",
  "hsl(217, 91%, 60%)",
  "hsl(142, 71%, 45%)",
  "hsl(38, 92%, 50%)",
  "hsl(280, 80%, 60%)",
  "hsl(340, 75%, 55%)",
];

export default function Analytics() {
  const { data: usage, isLoading: usageLoading } = useUsageSummary();
  const { data: trainingsPage, isLoading: trainingsLoading } = useTrainings({ limit: 200 });
  const { data: projectsPage } = useProjects({ limit: 100 });
  const { data: modelsPage } = useModels(undefined, { limit: 100 });

  const loading = usageLoading || trainingsLoading;

  const trainings = trainingsPage?.items ?? [];
  const projects = projectsPage?.items ?? [];
  const models = modelsPage?.items ?? [];

  // Training statistics
  const trainingStats = useMemo(() => {
    const completed = trainings.filter((t) => t.status === "completed").length;
    const failed = trainings.filter((t) => t.status === "failed").length;
    const running = trainings.filter((t) => t.status === "running").length;
    const totalFinished = completed + failed;
    const successRate = totalFinished > 0 ? Math.round((completed / totalFinished) * 100) : 100;

    // Average duration of completed trainings
    const durations = trainings
      .filter((t) => t.status === "completed" && t.started_at && t.ended_at)
      .map((t) => (new Date(t.ended_at!).getTime() - new Date(t.started_at!).getTime()) / 60000);

    const avgDuration = durations.length > 0
      ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length)
      : 0;

    return { completed, failed, running, successRate, avgDuration, total: trainings.length };
  }, [trainings]);

  // Aggregated token usage per model
  const tokensByModel = useMemo(() => {
    if (!usage?.items) return [];

    const map = new Map<string, { model: string; promptTokens: number; completionTokens: number; totalTokens: number }>();

    for (const item of usage.items) {
      const existing = map.get(item.model) ?? {
        model: item.model.split("/").pop() ?? item.model,
        promptTokens: 0,
        completionTokens: 0,
        totalTokens: 0,
      };
      existing.promptTokens += item.promptTokens;
      existing.completionTokens += item.completionTokens;
      existing.totalTokens += item.promptTokens + item.completionTokens;
      map.set(item.model, existing);
    }

    return Array.from(map.values())
      .sort((a, b) => b.totalTokens - a.totalTokens)
      .slice(0, 6);
  }, [usage]);

  // Aggregated cost per stage
  const costByStage = useMemo(() => {
    if (!usage?.items) return [];

    const map = new Map<string, number>();

    for (const item of usage.items) {
      const cost = item.cost_usd ? parseFloat(item.cost_usd) : 0;
      const stage = item.stage.toLowerCase();
      map.set(stage, (map.get(stage) ?? 0) + cost);
    }

    return Array.from(map.entries()).map(([stage, cost]) => ({
      name: stage.toUpperCase(),
      stage,
      value: Math.round(cost * 1000) / 1000,
    }));
  }, [usage]);

  const totalTokens = (usage?.prompt_tokens ?? 0) + (usage?.completion_tokens ?? 0);

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                <BarChart3 className="h-6 w-6 text-primary" /> Analytics & Engine Performance
              </h1>
              <p className="text-sm text-muted-foreground">
                Track OpenRouter token consumption, pipeline execution costs, and training success rates.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" asChild>
                <Link to="/usage">
                  Detailed Usage View
                </Link>
              </Button>
            </div>
          </div>
        </FadeIn>

        {/* Overview Stat Cards */}
        <FadeIn delay={0.05}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Card>
              <CardContent className="p-4 flex items-center gap-3">
                <div className="p-2.5 rounded-lg bg-primary/10 text-primary">
                  <Coins className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-foreground">{formatUsd(usage?.cost_usd)}</p>
                  <p className="text-xs text-muted-foreground">Total USD Cost</p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4 flex items-center gap-3">
                <div className="p-2.5 rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
                  <Activity className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-foreground">{formatNumber(totalTokens)}</p>
                  <p className="text-xs text-muted-foreground">Tokens Consumed</p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4 flex items-center gap-3">
                <div className="p-2.5 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                  <CheckCircle2 className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-foreground">{trainingStats.successRate}%</p>
                  <p className="text-xs text-muted-foreground">Training Success Rate</p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4 flex items-center gap-3">
                <div className="p-2.5 rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
                  <Clock className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-foreground">
                    {trainingStats.avgDuration > 0 ? `${trainingStats.avgDuration}m` : "< 1m"}
                  </p>
                  <p className="text-xs text-muted-foreground">Avg Training Time</p>
                </div>
              </CardContent>
            </Card>
          </div>
        </FadeIn>

        {/* Charts Section */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Token Usage by Model */}
          <FadeIn delay={0.1}>
            <Card className="h-full">
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <Cpu className="h-4 w-4 text-primary" /> Token Usage by Model
                </CardTitle>
                <CardDescription className="text-xs">
                  Prompt vs Completion token breakdown across top models.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {tokensByModel.length === 0 ? (
                  <div className="h-64 flex flex-col items-center justify-center text-muted-foreground text-xs">
                    <Activity className="h-8 w-8 mb-2 opacity-50" />
                    No model token activity recorded yet.
                  </div>
                ) : (
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={tokensByModel} margin={{ top: 10, right: 10, left: -10, bottom: 20 }}>
                        <CartesianGrid strokeDasharray="3 3" className="stroke-border" vertical={false} />
                        <XAxis
                          dataKey="model"
                          tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 10 }}
                          angle={-15}
                          textAnchor="end"
                          interval={0}
                        />
                        <YAxis tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 10 }} />
                        <Tooltip
                          contentStyle={{
                            background: "hsl(var(--card))",
                            border: "1px solid hsl(var(--border))",
                            borderRadius: 8,
                            fontSize: 12,
                          }}
                        />
                        <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
                        <Bar dataKey="promptTokens" name="Prompt Tokens" fill="hsl(var(--primary))" stackId="a" radius={[0, 0, 0, 0]} />
                        <Bar dataKey="completionTokens" name="Completion Tokens" fill="hsl(217, 91%, 60%)" stackId="a" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>
          </FadeIn>

          {/* Cost by Stage */}
          <FadeIn delay={0.15}>
            <Card className="h-full">
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <TrendingUp className="h-4 w-4 text-primary" /> Cost Breakdown by Stage
                </CardTitle>
                <CardDescription className="text-xs">
                  Proportion of compute and generation budget across pipeline stages.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {costByStage.length === 0 ? (
                  <div className="h-64 flex flex-col items-center justify-center text-muted-foreground text-xs">
                    <Coins className="h-8 w-8 mb-2 opacity-50" />
                    No stage cost breakdown available yet.
                  </div>
                ) : (
                  <div className="h-64 flex items-center">
                    <div className="w-1/2 h-full">
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie
                            data={costByStage}
                            dataKey="value"
                            nameKey="name"
                            cx="50%"
                            cy="50%"
                            innerRadius={50}
                            outerRadius={80}
                            paddingAngle={4}
                          >
                            {costByStage.map((entry, index) => (
                              <Cell
                                key={`cell-${index}`}
                                fill={STAGE_COLORS[entry.stage] ?? CHART_COLORS[index % CHART_COLORS.length]}
                              />
                            ))}
                          </Pie>
                          <Tooltip
                            formatter={(val: number) => [`$${val.toFixed(4)}`, "Cost"]}
                            contentStyle={{
                              background: "hsl(var(--card))",
                              border: "1px solid hsl(var(--border))",
                              borderRadius: 8,
                              fontSize: 12,
                            }}
                          />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>

                    <div className="w-1/2 space-y-2.5 pr-2">
                      {costByStage.map((entry, index) => (
                        <div key={entry.name} className="flex items-center justify-between text-xs">
                          <div className="flex items-center gap-2">
                            <div
                              className="h-3 w-3 rounded-full shrink-0"
                              style={{
                                background:
                                  STAGE_COLORS[entry.stage] ?? CHART_COLORS[index % CHART_COLORS.length],
                              }}
                            />
                            <span className="font-medium text-foreground">{entry.name}</span>
                          </div>
                          <span className="font-mono text-muted-foreground">${entry.value.toFixed(3)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </FadeIn>
        </div>

        {/* Training Jobs & Pipeline Execution Summary */}
        <FadeIn delay={0.2}>
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="text-base flex items-center gap-2">
                    <Layers className="h-4 w-4 text-primary" /> Training Operations
                  </CardTitle>
                  <CardDescription className="text-xs">
                    Overview of model training runs across all projects.
                  </CardDescription>
                </div>
                <Badge variant="outline" className="text-xs">
                  {trainings.length} total runs
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              {trainings.length === 0 ? (
                <div className="py-8 text-center text-xs text-muted-foreground">
                  No training runs recorded yet. Start training a model to view operational telemetry.
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
                    <div className="border rounded-lg p-3">
                      <p className="text-xs text-muted-foreground">Completed</p>
                      <p className="text-xl font-bold text-emerald-600 dark:text-emerald-400 mt-1">
                        {trainingStats.completed}
                      </p>
                    </div>
                    <div className="border rounded-lg p-3">
                      <p className="text-xs text-muted-foreground">Running</p>
                      <p className="text-xl font-bold text-primary mt-1">{trainingStats.running}</p>
                    </div>
                    <div className="border rounded-lg p-3">
                      <p className="text-xs text-muted-foreground">Failed</p>
                      <p className="text-xl font-bold text-destructive mt-1">{trainingStats.failed}</p>
                    </div>
                    <div className="border rounded-lg p-3">
                      <p className="text-xs text-muted-foreground">Active Projects</p>
                      <p className="text-xl font-bold text-foreground mt-1">{projects.length}</p>
                    </div>
                  </div>

                  <div className="pt-2">
                    <p className="text-xs font-semibold text-foreground mb-2">Recent Runs</p>
                    <div className="space-y-2">
                      {trainings.slice(0, 5).map((t) => (
                        <div
                          key={t.id}
                          className="flex items-center justify-between p-2.5 rounded-lg border bg-muted/20 text-xs"
                        >
                          <div className="flex items-center gap-2.5 min-w-0">
                            <Badge
                              variant={
                                t.status === "completed"
                                  ? "default"
                                  : t.status === "failed"
                                  ? "destructive"
                                  : "secondary"
                              }
                              className="text-[10px] capitalize"
                            >
                              {t.status}
                            </Badge>
                            <span className="font-semibold truncate text-foreground">
                              {t.training_name || `Training ${t.id.slice(0, 8)}`}
                            </span>
                            <span className="text-muted-foreground font-mono truncate hidden sm:inline">
                              ({t.base_model.split("/").pop()})
                            </span>
                          </div>

                          <div className="flex items-center gap-3 shrink-0 text-muted-foreground">
                            <span className="font-mono text-[11px]">
                              {t.started_at ? new Date(t.started_at).toLocaleDateString() : "—"}
                            </span>
                            <Button variant="ghost" size="sm" asChild className="h-6 text-xs px-2">
                              <Link to={`/projects/${t.project_id}/training`}>
                                Monitor
                              </Link>
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </FadeIn>
      </div>
    </PageTransition>
  );
}
