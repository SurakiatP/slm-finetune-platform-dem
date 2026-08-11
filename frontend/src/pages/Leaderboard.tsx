import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PageTransition, FadeIn } from "@/components/motion";
import { Trophy, Medal, ArrowUpDown, Rocket, Eye } from "lucide-react";
import { taskTypeLabels, baseModelLabels } from "@/data/mockData";
import { useLanguage } from "@/i18n/LanguageContext";
import { useModels } from "@/hooks/useUserData";
import { useProjects } from "@/hooks/useProjects";

type SortKey = "accuracy" | "f1Score" | "latencyMs" | "fileSize";

export default function Leaderboard() {
  const [filterTask, setFilterTask] = useState("all");
  const [sortBy, setSortBy] = useState<SortKey>("accuracy");
  const [sortAsc, setSortAsc] = useState(false);
  const { t } = useLanguage();
  const { models } = useModels();
  const { projects } = useProjects();

  const modelsWithMetrics = models.map((m) => {
    const project = projects.find((p) => p.id === m.projectId);
    const metrics = {
      accuracy: m.accuracy,
      f1Score: m.f1Score,
      precision: m.precision,
      recall: m.recall,
      latencyMs: m.latencyMs,
    };
    return { ...m, project, metrics };
  });

  const filtered = modelsWithMetrics.filter(
    (m) => filterTask === "all" || m.taskType === filterTask
  );

  const sorted = [...filtered].sort((a, b) => {
    let va: number, vb: number;
    if (sortBy === "fileSize") {
      va = parseFloat(a.fileSize);
      vb = parseFloat(b.fileSize);
    } else {
      va = a.metrics![sortBy as keyof typeof a.metrics] as number;
      vb = b.metrics![sortBy as keyof typeof b.metrics] as number;
    }
    return sortAsc ? va - vb : vb - va;
  });

  const handleSort = (key: SortKey) => {
    if (sortBy === key) setSortAsc(!sortAsc);
    else { setSortBy(key); setSortAsc(false); }
  };

  const medalColors = ["text-yellow-500", "text-gray-400", "text-amber-600"];
  const avgAccuracy = filtered.length ? (filtered.reduce((s, m) => s + (m.metrics?.accuracy || 0), 0) / filtered.length).toFixed(1) : "0";

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                <Trophy className="h-6 w-6 text-yellow-500" /> {t("leaderboard.title")}
              </h1>
              <p className="text-sm text-muted-foreground">{t("leaderboard.subtitle")}</p>
            </div>
            <Select value={filterTask} onValueChange={setFilterTask}>
              <SelectTrigger className="w-full sm:w-48"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("projects.allTasks")}</SelectItem>
                {Object.entries(taskTypeLabels).map(([k, v]) => (
                  <SelectItem key={k} value={k}>{v}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          <div className="grid grid-cols-3 gap-4">
            <Card>
              <CardContent className="p-4 text-center">
                <p className="text-2xl font-bold text-foreground">{sorted.length}</p>
                <p className="text-xs text-muted-foreground">{t("leaderboard.totalModels")}</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-4 text-center">
                <p className="text-2xl font-bold text-foreground">{avgAccuracy}%</p>
                <p className="text-xs text-muted-foreground">{t("leaderboard.avgAccuracy")}</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-4 text-center">
                <p className="text-2xl font-bold text-foreground">{sorted[0]?.name || "—"}</p>
                <p className="text-xs text-muted-foreground">{t("leaderboard.bestModel")}</p>
              </CardContent>
            </Card>
          </div>
        </FadeIn>

        <FadeIn delay={0.15}>
          <Card>
            <CardHeader><CardTitle className="text-sm">{t("leaderboard.rankings")}</CardTitle></CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left py-2 text-muted-foreground font-medium w-12">#</th>
                      <th className="text-left py-2 text-muted-foreground font-medium">{t("leaderboard.model")}</th>
                      <th className="text-left py-2 text-muted-foreground font-medium">{t("leaderboard.project")}</th>
                      <th className="text-left py-2 text-muted-foreground font-medium">{t("leaderboard.base")}</th>
                      <th className="text-left py-2 text-muted-foreground font-medium">{t("leaderboard.task")}</th>
                      {(["accuracy", "f1Score", "latencyMs"] as SortKey[]).map((k) => (
                        <th key={k} className="text-right py-2 text-muted-foreground font-medium cursor-pointer hover:text-foreground" onClick={() => handleSort(k)}>
                          <span className="inline-flex items-center gap-1">
                            {k === "accuracy" ? "Accuracy" : k === "f1Score" ? "F1" : "Latency"}
                            <ArrowUpDown className="h-3 w-3" />
                          </span>
                        </th>
                      ))}
                      <th className="text-right py-2 text-muted-foreground font-medium">{t("leaderboard.actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((m, i) => (
                      <tr key={m.id} className="border-b border-border/50 hover:bg-muted/30">
                        <td className="py-3">
                          {i < 3 ? <Medal className={`h-4 w-4 ${medalColors[i]}`} /> : <span className="text-muted-foreground text-xs">{i + 1}</span>}
                        </td>
                        <td className="py-3 font-medium font-mono text-xs">{m.name}</td>
                        <td className="py-3 text-xs text-muted-foreground">{m.project?.name}</td>
                        <td className="py-3"><Badge variant="outline" className="text-[10px]">{baseModelLabels[m.baseModel]}</Badge></td>
                        <td className="py-3"><Badge variant="secondary" className="text-[10px]">{taskTypeLabels[m.taskType]}</Badge></td>
                        <td className="py-3 text-right font-medium">{m.metrics?.accuracy}%</td>
                        <td className="py-3 text-right">{m.metrics?.f1Score}%</td>
                        <td className="py-3 text-right">{m.metrics?.latencyMs}ms</td>
                        <td className="py-3 text-right">
                          <div className="flex justify-end gap-1">
                            <Button variant="ghost" size="icon" className="h-7 w-7" asChild>
                              <Link to={`/models/${m.id}`}><Eye className="h-3.5 w-3.5" /></Link>
                            </Button>
                            <Button variant="ghost" size="icon" className="h-7 w-7">
                              <Rocket className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </FadeIn>
      </div>
    </PageTransition>
  );
}
