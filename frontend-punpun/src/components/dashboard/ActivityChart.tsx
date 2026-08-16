import { useMemo } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useModels, useProjects } from "@/hooks/queries";

export function ActivityChart() {
  const { data: projectsPage, isLoading: projectsLoading } = useProjects({ limit: 100 });
  const { data: modelsPage, isLoading: modelsLoading } = useModels(undefined, { limit: 100 });
  const loading = projectsLoading || modelsLoading;

  const data = useMemo(() => {
    const projects = projectsPage?.items ?? [];
    const models = modelsPage?.items ?? [];
    const days: { date: string; projects: number; models: number }[] = [];
    const today = new Date();
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      days.push({
        date: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
        projects: projects.filter((p) => p.created_at.slice(0, 10) === key).length,
        models: models.filter((m) => m.created_at.slice(0, 10) === key).length,
      });
    }
    return days;
  }, [projectsPage, modelsPage]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Activity (7 days)</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-[220px] w-full rounded-lg" />
        ) : (
          <div className="h-[220px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data}>
                <defs>
                  <linearGradient id="colorProjects" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                <XAxis dataKey="date" className="text-xs" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 12 }} />
                <YAxis
                  className="text-xs"
                  allowDecimals={false}
                  tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 12 }}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "hsl(var(--card))",
                    border: "1px solid hsl(var(--border))",
                    borderRadius: "8px",
                    fontSize: 12,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="projects"
                  name="Projects created"
                  stroke="hsl(var(--primary))"
                  fill="url(#colorProjects)"
                  strokeWidth={2}
                />
                <Area
                  type="monotone"
                  dataKey="models"
                  name="Models trained"
                  stroke="hsl(142,71%,45%)"
                  fill="transparent"
                  strokeWidth={2}
                  strokeDasharray="4 4"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
