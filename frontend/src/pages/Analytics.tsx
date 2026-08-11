import { useState, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PageTransition, FadeIn } from "@/components/motion";
import { BarChart3, Activity, AlertTriangle, ArrowUpRight, Clock } from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Legend,
} from "recharts";
import { useLanguage } from "@/i18n/LanguageContext";
import { useCallEvents } from "@/hooks/useUserData";
import { summarize, bucketByTime, endpointStats } from "@/lib/analyticsApi";

const timeRanges = ["24h", "7d", "30d", "90d"] as const;

export default function Analytics() {
  const [range, setRange] = useState<string>("7d");
  const { t } = useLanguage();
  const { events, loading } = useCallEvents(range);

  const summary = useMemo(() => summarize(events), [events]);
  const buckets = useMemo(() => bucketByTime(events, range), [events, range]);
  const endpoints = useMemo(() => endpointStats(events), [events]);

  const statCards = [
    { label: t("analytics.totalCalls"), value: summary.totalCalls.toLocaleString(), icon: BarChart3, change: "+12.3%" },
    { label: t("analytics.avgLatency"), value: `${summary.avgLatency}ms`, icon: Clock, change: "-0.8ms" },
    { label: t("analytics.errorRate"), value: `${summary.errorRate}%`, icon: AlertTriangle, change: "-0.3%" },
    { label: t("analytics.uptime"), value: `${summary.uptime}%`, icon: Activity, change: "+0.1%" },
  ];

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground">{t("analytics.title")}</h1>
              <p className="text-sm text-muted-foreground">{t("analytics.subtitle")}</p>
            </div>
            <div className="flex gap-1">
              {timeRanges.map((r) => (
                <Button key={r} variant={range === r ? "default" : "outline"} size="sm" onClick={() => setRange(r)} className="text-xs">
                  {r}
                </Button>
              ))}
            </div>
          </div>
        </FadeIn>

        {!loading && events.length === 0 && (
          <Card>
            <CardContent className="p-8 text-center">
              <Activity className="h-10 w-10 mx-auto text-muted-foreground mb-3" />
              <p className="text-sm text-muted-foreground">No API call events recorded in this range yet.</p>
            </CardContent>
          </Card>
        )}

        <FadeIn delay={0.1}>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {statCards.map((s) => (
              <Card key={s.label}>
                <CardContent className="p-4">
                  <div className="flex items-center justify-between mb-2">
                    <s.icon className="h-4 w-4 text-muted-foreground" />
                    <Badge variant="outline" className="text-[10px] text-primary">
                      <ArrowUpRight className="h-3 w-3 mr-0.5" />{s.change}
                    </Badge>
                  </div>
                  <p className="text-2xl font-bold text-foreground">{s.value}</p>
                  <p className="text-xs text-muted-foreground">{s.label}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </FadeIn>

        <FadeIn delay={0.2}>
          <Card>
            <CardHeader><CardTitle className="text-lg">{t("analytics.apiCallsOverTime")}</CardTitle></CardHeader>
            <CardContent>
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={buckets.calls}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                    <XAxis dataKey="time" className="text-xs" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                    <YAxis tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                    <Tooltip contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 }} />
                    <Area type="monotone" dataKey="calls" stroke="hsl(var(--primary))" fill="hsl(var(--primary)/0.15)" strokeWidth={2} />
                    <Area type="monotone" dataKey="errors" stroke="hsl(var(--destructive))" fill="hsl(var(--destructive)/0.1)" strokeWidth={1.5} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        </FadeIn>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <FadeIn delay={0.3}>
            <Card>
              <CardHeader><CardTitle className="text-lg">{t("analytics.latency")}</CardTitle></CardHeader>
              <CardContent>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={buckets.latency}>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                      <XAxis dataKey="time" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                      <YAxis tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                      <Tooltip contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 }} />
                      <Legend />
                      <Area type="monotone" dataKey="p50" stroke="hsl(var(--primary))" fill="hsl(var(--primary)/0.1)" strokeWidth={2} />
                      <Area type="monotone" dataKey="p95" stroke="hsl(var(--warning))" fill="hsl(var(--warning)/0.1)" strokeWidth={1.5} />
                      <Area type="monotone" dataKey="p99" stroke="hsl(var(--destructive))" fill="hsl(var(--destructive)/0.1)" strokeWidth={1.5} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          </FadeIn>

          <FadeIn delay={0.3}>
            <Card>
              <CardHeader><CardTitle className="text-lg">{t("analytics.errorByEndpoint")}</CardTitle></CardHeader>
              <CardContent>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={endpoints} layout="vertical">
                      <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                      <XAxis type="number" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} unit="%" />
                      <YAxis dataKey="endpoint" type="category" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 10 }} width={140} />
                      <Tooltip contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 }} />
                      <Bar dataKey="errorRate" fill="hsl(var(--destructive))" radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          </FadeIn>
        </div>

        <FadeIn delay={0.4}>
          <Card>
            <CardHeader><CardTitle className="text-lg">{t("analytics.topEndpoints")}</CardTitle></CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left py-2 text-muted-foreground font-medium">{t("analytics.endpoint")}</th>
                      <th className="text-right py-2 text-muted-foreground font-medium">{t("analytics.calls")}</th>
                      <th className="text-right py-2 text-muted-foreground font-medium">{t("analytics.avgLatency")}</th>
                      <th className="text-right py-2 text-muted-foreground font-medium">{t("analytics.errorRate")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {endpoints.map((ep) => (
                      <tr key={ep.endpoint} className="border-b border-border/50">
                        <td className="py-2.5 font-mono text-xs">{ep.endpoint}</td>
                        <td className="py-2.5 text-right">{ep.calls.toLocaleString()}</td>
                        <td className="py-2.5 text-right">{ep.avgLatency}ms</td>
                        <td className="py-2.5 text-right">
                          <Badge variant={ep.errorRate > 1.5 ? "destructive" : "outline"} className="text-[10px]">{ep.errorRate}%</Badge>
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
