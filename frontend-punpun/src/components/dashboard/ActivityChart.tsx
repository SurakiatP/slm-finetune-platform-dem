import { useMemo } from "react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useModels, useTrainings } from "@/hooks/queries";

export function ActivityChart() {
  const { data: trainingsPage } = useTrainings({ limit: 100 });
  const { data: modelsPage } = useModels(undefined, { limit: 100 });

  const data = useMemo(() => {
    const trainings = trainingsPage?.items ?? [];
    const models = modelsPage?.items ?? [];
    // Original plotted mock `credits` + `jobs`; the Engine's equivalent
    // activity signal is training jobs launched and model artifacts produced.
    const days: { date: string; trainings: number; models: number }[] = [];
    const today = new Date();
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      days.push({
        date: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
        trainings: trainings.filter((tr) => tr.created_at.slice(0, 10) === key).length,
        models: models.filter((m) => m.created_at.slice(0, 10) === key).length,
      });
    }
    return days;
  }, [trainingsPage, modelsPage]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Training Activity (7 days)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[220px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data}>
              <defs>
                <linearGradient id="colorTrainings" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
              <XAxis dataKey="date" className="text-xs" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 12 }} />
              <YAxis className="text-xs" allowDecimals={false} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 12 }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: "hsl(var(--card))",
                  border: "1px solid hsl(var(--border))",
                  borderRadius: "8px",
                  fontSize: 12,
                }}
              />
              <Area type="monotone" dataKey="trainings" stroke="hsl(var(--primary))" fill="url(#colorTrainings)" strokeWidth={2} />
              <Area type="monotone" dataKey="models" stroke="hsl(142,71%,45%)" fill="transparent" strokeWidth={2} strokeDasharray="4 4" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
