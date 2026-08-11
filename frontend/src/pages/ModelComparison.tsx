import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PageTransition, FadeIn } from "@/components/motion";
import { GitCompare, Trophy, Rocket } from "lucide-react";
import { useModels } from "@/hooks/useUserData";
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
} from "recharts";
import { useLanguage } from "@/i18n/LanguageContext";

const COLORS = ["hsl(var(--primary))", "hsl(var(--warning))", "hsl(var(--destructive))"];

export default function ModelComparison() {
  const { models } = useModels();
  const [selected, setSelected] = useState<string[]>([]);
  const { t } = useLanguage();

  if (models.length >= 2 && selected.length === 0) {
    setSelected([models[0].id, models[1].id]);
  }

  const modelMetrics: Record<string, { accuracy: number; f1: number; precision: number; recall: number; latency: number; size: string }> = {};
  models.forEach((m) => {
    modelMetrics[m.id] = {
      accuracy: m.accuracy,
      f1: m.f1Score,
      precision: m.precision,
      recall: m.recall,
      latency: m.latencyMs / 1000,
      size: m.fileSize,
    };
  });


  const handleSelect = (index: number, value: string) => {
    const next = [...selected];
    next[index] = value;
    setSelected(next);
  };

  const addSlot = () => {
    if (selected.length < 3) {
      const unused = models.find((m) => !selected.includes(m.id));
      if (unused) setSelected([...selected, unused.id]);
    }
  };

  const removeSlot = (index: number) => {
    if (selected.length > 2) setSelected(selected.filter((_, i) => i !== index));
  };

  const radarData = ["accuracy", "f1", "precision", "recall"].map((metric) => {
    const entry: Record<string, string | number> = { metric: metric.charAt(0).toUpperCase() + metric.slice(1) };
    selected.forEach((id, i) => {
      const m = modelMetrics[id];
      if (m) entry[`model${i}`] = m[metric as keyof typeof m] as number;
    });
    return entry;
  });

  const latencyData = selected.map((id, i) => ({
    name: models.find((m) => m.id === id)?.name || id,
    latency: modelMetrics[id]?.latency || 0,
    fill: COLORS[i],
  }));

  const getBest = (metric: string) => {
    let bestId = selected[0];
    let bestVal = -Infinity;
    selected.forEach((id) => {
      const val = modelMetrics[id]?.[metric as keyof (typeof modelMetrics)["model-1"]] as number;
      if (metric === "latency" ? val < bestVal || bestVal === -Infinity : val > bestVal) {
        bestVal = val;
        bestId = id;
      }
    });
    return bestId;
  };

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                <GitCompare className="h-6 w-6 text-primary" /> {t("compare.title")}
              </h1>
              <p className="text-sm text-muted-foreground">{t("compare.subtitle")}</p>
            </div>
            {selected.length < 3 && <Button variant="outline" size="sm" onClick={addSlot}>{t("compare.addModel")}</Button>}
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {selected.map((id, i) => (
              <div key={i} className="space-y-2">
                <div className="flex items-center gap-2">
                  <div className="h-3 w-3 rounded-full" style={{ background: COLORS[i] }} />
                  <Select value={id} onValueChange={(v) => handleSelect(i, v)}>
                    <SelectTrigger className="flex-1"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {models.map((m) => (
                        <SelectItem key={m.id} value={m.id}>
                          <span className="font-mono text-sm">{m.name}</span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selected.length > 2 && (
                    <Button variant="ghost" size="sm" onClick={() => removeSlot(i)} className="text-xs">✕</Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </FadeIn>

        <FadeIn delay={0.15}>
          <Card>
            <CardHeader><CardTitle className="text-lg">{t("compare.metricsComparison")}</CardTitle></CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left py-2 text-muted-foreground font-medium">{t("compare.metric")}</th>
                      {selected.map((id, i) => (
                        <th key={id} className="text-right py-2 text-muted-foreground font-medium">
                          <div className="flex items-center justify-end gap-1.5">
                            <div className="h-2.5 w-2.5 rounded-full" style={{ background: COLORS[i] }} />
                            {models.find((m) => m.id === id)?.name}
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(["accuracy", "f1", "precision", "recall", "latency"] as const).map((metric) => {
                      const best = getBest(metric);
                      return (
                        <tr key={metric} className="border-b border-border/50">
                          <td className="py-2.5 capitalize">{metric === "f1" ? "F1 Score" : metric}</td>
                          {selected.map((id) => {
                            const val = modelMetrics[id]?.[metric];
                            return (
                              <td key={id} className="py-2.5 text-right">
                                <span className="font-medium">{val}{metric === "latency" ? "ms" : "%"}</span>
                                {id === best && <Trophy className="inline ml-1.5 h-3.5 w-3.5 text-warning" />}
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                    <tr className="border-b border-border/50">
                      <td className="py-2.5">{t("compare.modelSize")}</td>
                      {selected.map((id) => (
                        <td key={id} className="py-2.5 text-right">{modelMetrics[id]?.size}</td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </FadeIn>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <FadeIn delay={0.2}>
            <Card>
              <CardHeader><CardTitle className="text-lg">{t("compare.radarOverview")}</CardTitle></CardHeader>
              <CardContent>
                <div className="h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <RadarChart data={radarData}>
                      <PolarGrid stroke="hsl(var(--border))" />
                      <PolarAngleAxis dataKey="metric" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                      <PolarRadiusAxis domain={[80, 100]} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 10 }} />
                      {selected.map((_, i) => (
                        <Radar key={i} name={`Model ${i + 1}`} dataKey={`model${i}`} stroke={COLORS[i]} fill={COLORS[i]} fillOpacity={0.15} strokeWidth={2} />
                      ))}
                    </RadarChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          </FadeIn>

          <FadeIn delay={0.2}>
            <Card>
              <CardHeader><CardTitle className="text-lg">{t("compare.latencyComparison")}</CardTitle></CardHeader>
              <CardContent>
                <div className="h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={latencyData}>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                      <XAxis dataKey="name" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                      <YAxis tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} unit="ms" />
                      <Tooltip contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 }} />
                      <Bar dataKey="latency" radius={[4, 4, 0, 0]}>
                        {latencyData.map((entry, index) => (
                          <rect key={index} fill={entry.fill} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          </FadeIn>
        </div>

        <FadeIn delay={0.3}>
          <div className="flex justify-end">
            <Button size="lg">
              <Rocket className="h-4 w-4 mr-2" /> {t("compare.deployBest")}
            </Button>
          </div>
        </FadeIn>
      </div>
    </PageTransition>
  );
}
