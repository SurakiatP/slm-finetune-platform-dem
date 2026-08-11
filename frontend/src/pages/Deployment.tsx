import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { PageTransition, FadeIn } from "@/components/motion";
import { Rocket, Copy, Power, PowerOff, Activity, Clock, AlertTriangle, Shield, Beaker } from "lucide-react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { mockUsageTimeline } from "@/data/deploymentMockData";
import { ABTestingPanel } from "@/components/deployment/ABTestingPanel";
import { useToast } from "@/hooks/use-toast";
import { useLanguage } from "@/i18n/LanguageContext";
import { useEndpoints } from "@/hooks/useUserData";
import { setEndpointStatus, type DeployedEndpoint } from "@/lib/deploymentsApi";

export default function Deployment() {
  const { endpoints, setEndpoints } = useEndpoints();
  const [selected, setSelected] = useState<DeployedEndpoint | null>(null);
  const { toast } = useToast();
  const { t } = useLanguage();

  // Auto-select first endpoint when loaded
  if (!selected && endpoints.length > 0) {
    setSelected(endpoints[0]);
  }

  const handleToggle = async (id: string) => {
    const current = endpoints.find((e) => e.id === id);
    if (!current) return;
    const newStatus = current.status === "active" ? "inactive" : "active";
    await setEndpointStatus(id, newStatus);
    setEndpoints((prev) => prev.map((ep) => (ep.id === id ? { ...ep, status: newStatus } : ep)));
    if (selected?.id === id) setSelected({ ...current, status: newStatus });
    toast({ title: t("deploy.statusUpdated") });
  };

  const handleCopy = (url: string) => {
    navigator.clipboard.writeText(url);
    toast({ title: t("deploy.copied") });
  };

  const activeCount = endpoints.filter((e) => e.status === "active").length;
  const totalRequests = endpoints.reduce((s, e) => s + e.requestsPerMin, 0);
  const avgLatency = endpoints.filter((e) => e.status === "active").reduce((s, e) => s + e.avgLatencyMs, 0) / (activeCount || 1);

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div>
            <h1 className="text-2xl font-bold text-foreground">{t("deploy.title")}</h1>
            <p className="text-sm text-muted-foreground">{t("deploy.subtitle")}</p>
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { label: t("deploy.activeEndpoints"), value: activeCount, icon: Rocket },
              { label: t("deploy.requestsPerMin"), value: totalRequests, icon: Activity },
              { label: t("deploy.avgLatency"), value: `${Math.round(avgLatency)}ms`, icon: Clock },
              { label: t("deploy.errorRate"), value: `${(endpoints.filter((e) => e.status === "active").reduce((s, e) => s + e.errorRate, 0) / (activeCount || 1)).toFixed(1)}%`, icon: AlertTriangle },
            ].map((s) => (
              <Card key={s.label}>
                <CardContent className="p-4 flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-accent"><s.icon className="h-4 w-4 text-primary" /></div>
                  <div>
                    <p className="text-lg font-bold text-foreground">{s.value}</p>
                    <p className="text-xs text-muted-foreground">{s.label}</p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </FadeIn>

        <Tabs defaultValue="endpoints" className="space-y-4">
          <TabsList>
            <TabsTrigger value="endpoints" className="text-xs gap-1.5"><Rocket className="h-3.5 w-3.5" /> {t("deploy.endpointsTab")}</TabsTrigger>
            <TabsTrigger value="ab" className="text-xs gap-1.5"><Beaker className="h-3.5 w-3.5" /> {t("ab.tabTitle")}</TabsTrigger>
          </TabsList>

          <TabsContent value="endpoints">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <FadeIn delay={0.15} className="lg:col-span-1">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t("deploy.endpoints")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {endpoints.map((ep) => (
                  <div
                    key={ep.id}
                    onClick={() => setSelected(ep)}
                    className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                      selected?.id === ep.id ? "border-primary bg-accent/50" : "border-border hover:bg-muted/30"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-foreground">{ep.modelName}</span>
                      <Badge variant={ep.status === "active" ? "default" : ep.status === "scaling" ? "secondary" : "outline"} className="text-[10px]">
                        {ep.status}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">{ep.projectName}</p>
                    {ep.status === "active" && (
                      <p className="text-[10px] text-muted-foreground mt-1">{ep.requestsPerMin} req/min · {ep.avgLatencyMs}ms</p>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>
          </FadeIn>

          <FadeIn delay={0.2} className="lg:col-span-2 space-y-4">
            {selected && (
              <>
                <Card>
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-sm">{selected.modelName}</CardTitle>
                      <div className="flex gap-2">
                        <Button
                          variant={selected.status === "active" ? "destructive" : "default"}
                          size="sm"
                          onClick={() => handleToggle(selected.id)}
                        >
                          {selected.status === "active" ? (
                            <><PowerOff className="h-3.5 w-3.5 mr-1.5" />{t("deploy.undeploy")}</>
                          ) : (
                            <><Power className="h-3.5 w-3.5 mr-1.5" />{t("deploy.deploy")}</>
                          )}
                        </Button>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div>
                      <Label className="text-xs text-muted-foreground">{t("deploy.endpointUrl")}</Label>
                      <div className="flex items-center gap-2 mt-1">
                        <code className="text-xs font-mono bg-muted p-2 rounded flex-1 truncate">{selected.endpointUrl}</code>
                        <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={() => handleCopy(selected.endpointUrl)}>
                          <Copy className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                    {selected.status === "active" && (
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-center">
                        {[
                          { label: t("deploy.requestsPerMin"), value: selected.requestsPerMin },
                          { label: t("deploy.avgLatency"), value: `${selected.avgLatencyMs}ms` },
                          { label: t("deploy.errorRate"), value: `${selected.errorRate}%` },
                          { label: t("deploy.uptime"), value: `${selected.uptime}%` },
                        ].map((m) => (
                          <div key={m.label} className="p-2 rounded-lg bg-accent">
                            <p className="text-lg font-bold text-foreground">{m.value}</p>
                            <p className="text-[10px] text-muted-foreground">{m.label}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>

                {selected.status === "active" && (
                  <>
                    <Card>
                      <CardHeader><CardTitle className="text-sm">{t("deploy.usageChart")}</CardTitle></CardHeader>
                      <CardContent>
                        <div className="h-[200px]">
                          <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={mockUsageTimeline}>
                              <defs>
                                <linearGradient id="reqGrad" x1="0" y1="0" x2="0" y2="1">
                                  <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.2} />
                                  <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                                </linearGradient>
                              </defs>
                              <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                              <XAxis dataKey="time" tick={{ fontSize: 10 }} />
                              <YAxis tick={{ fontSize: 10 }} />
                              <Tooltip contentStyle={{ borderRadius: "8px", fontSize: 12 }} />
                              <Area type="monotone" dataKey="requests" stroke="hsl(var(--primary))" fill="url(#reqGrad)" strokeWidth={2} />
                            </AreaChart>
                          </ResponsiveContainer>
                        </div>
                      </CardContent>
                    </Card>

                    <Card>
                      <CardHeader>
                        <CardTitle className="text-sm flex items-center gap-2">
                          <Shield className="h-4 w-4" /> {t("deploy.rateLimiting")}
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div className="space-y-2">
                          <Label className="text-xs">{t("deploy.rateLimit")}: {selected.rateLimitPerMin} req/min</Label>
                          <Slider value={[selected.rateLimitPerMin]} min={100} max={5000} step={100} />
                        </div>
                        <div className="space-y-2">
                          <Label className="text-xs">{t("deploy.burstLimit")}: {selected.burstLimit}</Label>
                          <Slider value={[selected.burstLimit]} min={10} max={200} step={5} />
                        </div>
                      </CardContent>
                    </Card>
                  </>
                )}
              </>
            )}
          </FadeIn>
        </div>
          </TabsContent>

          <TabsContent value="ab">
            <ABTestingPanel />
          </TabsContent>
        </Tabs>
      </div>
    </PageTransition>
  );
}

