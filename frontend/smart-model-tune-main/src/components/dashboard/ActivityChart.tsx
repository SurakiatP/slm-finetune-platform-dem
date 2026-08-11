import { useMemo } from "react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useProjects } from "@/hooks/useProjects";

export function ActivityChart() {
  const { projects } = useProjects();

  const data = useMemo(() => {
    const days: { date: string; jobs: number; credits: number }[] = [];
    const today = new Date();
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      const dayProjects = projects.filter((p) => p.updatedAt.slice(0, 10) === key);
      days.push({
        date: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
        jobs: dayProjects.length,
        credits: dayProjects.reduce((s, p) => s + p.creditsCost, 0),
      });
    }
    return days;
  }, [projects]);

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
                <linearGradient id="colorCredits" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
              <XAxis dataKey="date" className="text-xs" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 12 }} />
              <YAxis className="text-xs" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 12 }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: "hsl(var(--card))",
                  border: "1px solid hsl(var(--border))",
                  borderRadius: "8px",
                  fontSize: 12,
                }}
              />
              <Area type="monotone" dataKey="credits" stroke="hsl(var(--primary))" fill="url(#colorCredits)" strokeWidth={2} />
              <Area type="monotone" dataKey="jobs" stroke="hsl(142,71%,45%)" fill="transparent" strokeWidth={2} strokeDasharray="4 4" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
