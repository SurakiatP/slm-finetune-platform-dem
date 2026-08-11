import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Trophy, TrendingUp, TrendingDown, Beaker, ArrowRight } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { useModels } from "@/hooks/useUserData";
import { useLanguage } from "@/i18n/LanguageContext";
import { useToast } from "@/hooks/use-toast";

const liveData = Array.from({ length: 12 }).map((_, i) => ({
  time: `${i * 5}m`,
  variantA: 142 + Math.round(Math.sin(i / 2) * 8 + Math.random() * 6),
  variantB: 128 + Math.round(Math.cos(i / 2) * 7 + Math.random() * 6),
}));

const errorData = Array.from({ length: 12 }).map((_, i) => ({
  time: `${i * 5}m`,
  variantA: 1.2 + Math.random() * 0.4,
  variantB: 0.6 + Math.random() * 0.3,
}));

export function ABTestingPanel() {
  const { models } = useModels();
  const [variantA, setVariantA] = useState<string>("");
  const [variantB, setVariantB] = useState<string>("");
  const [trafficSplit, setTrafficSplit] = useState(70);
  const [running, setRunning] = useState(true);
  const { t } = useLanguage();
  const { toast } = useToast();

  if (models.length > 0 && !variantA) setVariantA(models[0].id);
  if (models.length > 1 && !variantB) setVariantB(models[1].id);

  const getName = (id: string) => models.find((m) => m.id === id)?.name || id;

  const winner = "B";
  const handlePromote = (variant: "A" | "B") => {
    toast({
      title: t("ab.promoted"),
      description: `${variant === "A" ? getName(variantA) : getName(variantB)} → 100% traffic`,
    });
    setRunning(false);
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="text-sm flex items-center gap-2">
                <Beaker className="h-4 w-4 text-primary" /> {t("ab.title")}
              </CardTitle>
              <CardDescription className="text-xs">{t("ab.subtitle")}</CardDescription>
            </div>
            <Badge variant={running ? "default" : "outline"} className="text-[10px] shrink-0">
              {running ? t("ab.running") : t("ab.stopped")}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                {t("ab.variantA")} ({trafficSplit}%)
              </label>
              <Select value={variantA} onValueChange={setVariantA}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                {t("ab.variantB")} ({100 - trafficSplit}%)
              </label>
              <Select value={variantB} onValueChange={setVariantB}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-muted-foreground">{t("ab.trafficSplit")}</span>
              <span className="font-mono font-medium">{trafficSplit}% / {100 - trafficSplit}%</span>
            </div>
            <Slider
              value={[trafficSplit]}
              onValueChange={([v]) => setTrafficSplit(v)}
              min={10}
              max={90}
              step={5}
            />
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {[
          {
            id: "A",
            name: getName(variantA),
            traffic: trafficSplit,
            requests: 1842,
            latency: 142,
            errorRate: 1.4,
            winner: false,
          },
          {
            id: "B",
            name: getName(variantB),
            traffic: 100 - trafficSplit,
            requests: 789,
            latency: 128,
            errorRate: 0.7,
            winner: true,
          },
        ].map((v) => (
          <Card key={v.id} className={v.winner ? "border-primary/40 bg-primary/5" : ""}>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm flex items-center gap-2">
                  {t("ab.variant")} {v.id}
                  {v.winner && <Trophy className="h-3.5 w-3.5 text-yellow-500" />}
                </CardTitle>
                <Badge variant="outline" className="text-[10px]">{v.traffic}%</Badge>
              </div>
              <CardDescription className="text-xs font-mono">{v.name}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="p-2 rounded bg-accent">
                  <p className="text-sm font-bold">{v.requests.toLocaleString()}</p>
                  <p className="text-[9px] text-muted-foreground">{t("ab.requests")}</p>
                </div>
                <div className="p-2 rounded bg-accent">
                  <p className="text-sm font-bold flex items-center justify-center gap-1">
                    {v.latency}ms
                    {v.id === "B" && <TrendingDown className="h-3 w-3 text-emerald-500" />}
                  </p>
                  <p className="text-[9px] text-muted-foreground">{t("ab.latency")}</p>
                </div>
                <div className="p-2 rounded bg-accent">
                  <p className="text-sm font-bold flex items-center justify-center gap-1">
                    {v.errorRate}%
                    {v.id === "B" && <TrendingDown className="h-3 w-3 text-emerald-500" />}
                  </p>
                  <p className="text-[9px] text-muted-foreground">{t("ab.errorRate")}</p>
                </div>
              </div>
              <Button
                size="sm"
                variant={v.winner ? "default" : "outline"}
                className="w-full gap-1.5 text-xs h-7"
                onClick={() => handlePromote(v.id as "A" | "B")}
                disabled={!running}
              >
                <ArrowRight className="h-3 w-3" /> {t("ab.promoteToProduction")}
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>

      {winner === "B" && (
        <Card className="border-primary/30 bg-primary/5">
          <CardContent className="p-4 flex items-start gap-3">
            <TrendingUp className="h-5 w-5 text-primary shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-foreground">{t("ab.recommendation")}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {t("ab.recommendationDesc").replace("{name}", getName(variantB))}
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle className="text-sm">{t("ab.latencyChart")}</CardTitle></CardHeader>
          <CardContent>
            <div className="h-[200px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={liveData}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="time" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line type="monotone" dataKey="variantA" name={`A: ${getName(variantA)}`} stroke="hsl(var(--muted-foreground))" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="variantB" name={`B: ${getName(variantB)}`} stroke="hsl(var(--primary))" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm">{t("ab.errorChart")}</CardTitle></CardHeader>
          <CardContent>
            <div className="h-[200px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={errorData}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="time" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line type="monotone" dataKey="variantA" name={`A: ${getName(variantA)}`} stroke="hsl(var(--muted-foreground))" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="variantB" name={`B: ${getName(variantB)}`} stroke="hsl(var(--primary))" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
