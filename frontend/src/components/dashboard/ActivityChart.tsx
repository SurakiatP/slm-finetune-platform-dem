import { useMemo } from "react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useModels, useTrainings } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";
import { formatDate } from "@/lib/format";

export function ActivityChart() {
  const { t, language } = useLanguage();
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
        date: formatDate(d, { month: "short", day: "numeric" }),
        trainings: trainings.filter((tr) => tr.created_at.slice(0, 10) === key).length,
        models: models.filter((m) => m.created_at.slice(0, 10) === key).length,
      });
    }
    return days;
    // `language` re-runs the date labels when the UI language changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trainingsPage, modelsPage, language]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{t("dashboardPage.trainingActivity")}</CardTitle>
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
              {/* Both series are usually small integers and often equal, so
                  each gets its own colour, point markers and a legend —
                  otherwise one line hides the other (UAT DEF-004). */}
              <Legend verticalAlign="top" align="right" height={28} iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
              <Area type="monotone" dataKey="trainings" name={t("dashboardPage.seriesTrainings")} stroke="hsl(var(--primary))" fill="url(#colorTrainings)" strokeWidth={2} dot={{ r: 3, fill: "hsl(var(--primary))" }} activeDot={{ r: 5 }} />
              <Area type="monotone" dataKey="models" name={t("dashboardPage.seriesModels")} stroke="hsl(32,95%,48%)" fill="transparent" strokeWidth={2} strokeDasharray="5 4" dot={{ r: 4, fill: "hsl(var(--card))", stroke: "hsl(32,95%,48%)", strokeWidth: 2 }} activeDot={{ r: 6 }} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
