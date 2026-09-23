import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { PageTransition, FadeIn } from "@/components/motion";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ArrowLeft, Database, CheckCircle2, AlertTriangle, Tag, Activity, FileText, Loader2, Sparkles } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, PieChart, Pie } from "recharts";
import { DatasetList } from "@/components/dataset/DatasetList";
import { useLanguage } from "@/i18n/LanguageContext";
import { useDatasetInsights, useDatasets, useProject } from "@/hooks/queries";
import type { Dataset } from "@/api/types";

const READINESS_BANDS = (score: number, t: (k: string) => string) => {
  if (score >= 85) return { label: t("insights.readyToTrain"), color: "text-emerald-500", icon: CheckCircle2 };
  if (score >= 65) return { label: t("insights.canTrainWithCaveats"), color: "text-yellow-500", icon: AlertTriangle };
  return { label: t("insights.fixBeforeTraining"), color: "text-destructive", icon: AlertTriangle };
};

const PIE_COLORS = ["hsl(var(--primary))", "hsl(var(--primary)/0.85)", "hsl(var(--primary)/0.7)", "hsl(var(--primary)/0.55)", "hsl(var(--primary)/0.4)", "hsl(var(--destructive)/0.7)"];
const EMPTY_DATASETS: Dataset[] = [];

export default function DatasetInsights() {
  const { id } = useParams<{ id: string }>();
  const { data: project, isLoading } = useProject(id ?? "");
  const { t } = useLanguage();
  const [selectedDataset, setSelectedDataset] = useState<string>();
  const { data: datasetsPage, isLoading: datasetsLoading } = useDatasets(project?.id, { limit: 100 });
  const datasets = datasetsPage?.items ?? EMPTY_DATASETS;
  const { data: report, isLoading: insightsLoading } = useDatasetInsights(selectedDataset);

  useEffect(() => {
    if (!selectedDataset || !datasets.some((dataset) => dataset.id === selectedDataset)) {
      setSelectedDataset(datasets[0]?.id);
    }
  }, [datasets, selectedDataset]);

  const band = report ? READINESS_BANDS(report.overall_quality_score, t) : null;
  const BandIcon = band?.icon;

  // Coverage = how many class samples meet a healthy minimum (assume 30 per class as min target for fine-tune)
  const minPerClass = 30;
  const classesAboveMin = report?.label_distribution.filter((c) => c.count >= minPerClass).length ?? 0;
  const coveragePct = report?.label_distribution.length
    ? Math.round((classesAboveMin / report.label_distribution.length) * 100)
    : 0;

  const labelCoverage = (report?.label_distribution ?? []).map((c) => ({
    ...c,
    healthy: c.count >= minPerClass,
  }));
  const datasetMeta = datasets.find((dataset) => dataset.id === selectedDataset);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">{t("projectDetail.notFound")}</p>
        <Button variant="link" asChild><Link to="/projects">{t("projectDetail.backToProjects")}</Link></Button>
      </div>
    );
  }

  return (
    <PageTransition>
      <div className="space-y-6 max-w-6xl">
        <FadeIn>
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="icon" asChild>
              <Link to={`/projects/${id}`}><ArrowLeft className="h-4 w-4" /></Link>
            </Button>
            <div className="flex-1">
              <h1 className="text-xl font-bold text-foreground flex items-center gap-2">
                <Database className="h-5 w-5 text-primary" />
                {t("insights.title")}
              </h1>
              <p className="text-sm text-muted-foreground">{project.name}</p>
            </div>
          </div>
        </FadeIn>

        <Tabs defaultValue="datasets">
          <TabsList>
            <TabsTrigger value="datasets">
              <Database className="h-3.5 w-3.5 mr-1.5" />
              Datasets
            </TabsTrigger>
            <TabsTrigger value="quality">
              <Sparkles className="h-3.5 w-3.5 mr-1.5" />
              {t("insights.title")}
            </TabsTrigger>
          </TabsList>

          {/* Real Engine datasets for this project (seed / SDG / merged). */}
          <TabsContent value="datasets" className="mt-4">
            <DatasetList projectId={project.id} />
          </TabsContent>

          <TabsContent value="quality" className="mt-4 space-y-6">
            {datasetsLoading ? (
              <div className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
            ) : datasets.length === 0 ? (
              <p className="py-12 text-center text-sm text-muted-foreground">Upload or generate a dataset to view its quality insights.</p>
            ) : insightsLoading || !report || !band || !BandIcon ? (
              <div className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
            ) : (
              <>
                <FadeIn>
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-xs text-muted-foreground">
                      Quality analysis is calculated from the selected dataset in the Engine.
                      {report.scan_truncated ? ` Sampled ${report.scanned_rows.toLocaleString()} rows.` : ""}
                    </p>
                    <Select value={selectedDataset} onValueChange={setSelectedDataset}>
                      <SelectTrigger className="w-56"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {datasets.map((dataset) => (
                          <SelectItem key={dataset.id} value={dataset.id}>{dataset.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </FadeIn>

            {/* Readiness Banner */}
            <FadeIn delay={0.05}>
              <Alert className={`${band.color === "text-emerald-500" ? "border-emerald-500/30 bg-emerald-500/5" : band.color === "text-yellow-500" ? "border-yellow-500/30 bg-yellow-500/5" : "border-destructive/30 bg-destructive/5"}`}>
                <BandIcon className={`h-4 w-4 ${band.color}`} />
                <AlertDescription>
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <p className="text-sm font-semibold text-foreground">{band.label}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                      {t("insights.readinessDesc").replace("{score}", String(report.overall_quality_score))}
                      </p>
                    </div>
                    <Badge variant="outline" className="text-xs shrink-0">
                      {t("insights.qualityScore")}: <span className={`ml-1 font-bold ${band.color}`}>{report.overall_quality_score}/100</span>
                    </Badge>
                  </div>
                </AlertDescription>
              </Alert>
            </FadeIn>

            {/* Stats Cards */}
            <FadeIn delay={0.1}>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {[
                  { label: t("insights.totalRows"), value: report.row_count.toLocaleString(), icon: FileText },
                  { label: t("insights.uniqueLabels"), value: report.label_distribution.length || "—", icon: Tag },
                  { label: t("insights.coverage"), value: `${coveragePct}%`, icon: Activity },
                  { label: t("insights.duplicates"), value: report.duplicate_rows, icon: AlertTriangle },
                ].map((s) => (
                  <Card key={s.label}>
                    <CardContent className="p-4 flex items-center gap-3">
                      <div className="p-2 rounded-lg bg-accent">
                        <s.icon className="h-4 w-4 text-primary" />
                      </div>
                      <div>
                        <p className="text-xl font-bold text-foreground">{s.value}</p>
                        <p className="text-xs text-muted-foreground">{s.label}</p>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </FadeIn>

            {/* Label Coverage */}
            {report.label_distribution.length > 0 && (
              <FadeIn delay={0.15}>
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm flex items-center gap-2">
                      <Tag className="h-4 w-4 text-primary" /> {t("insights.labelCoverage")}
                    </CardTitle>
                    <CardDescription className="text-xs">
                      {t("insights.labelCoverageDesc").replace("{n}", String(minPerClass))}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                      <div className="space-y-2">
                        {labelCoverage.map((c) => (
                          <div key={c.label} className="space-y-1">
                            <div className="flex justify-between items-center text-xs">
                              <span className="font-mono text-foreground flex items-center gap-1.5">
                                {c.healthy ? (
                                  <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                                ) : (
                                  <AlertTriangle className="h-3 w-3 text-yellow-500" />
                                )}
                                {c.label}
                              </span>
                              <span className="text-muted-foreground">
                                {c.count} ({c.percent}%)
                              </span>
                            </div>
                            <Progress value={Math.min(100, (c.count / minPerClass) * 100)} className="h-1.5" />
                          </div>
                        ))}
                      </div>
                      <div className="h-[240px]">
                        <ResponsiveContainer width="100%" height="100%">
                          <PieChart>
                            <Pie
                              data={report.label_distribution}
                              dataKey="count"
                              nameKey="label"
                              cx="50%"
                              cy="50%"
                              outerRadius={90}
                              label={(entry) => entry.label}
                              labelLine={false}
                            >
                              {report.label_distribution.map((_, i) => (
                                <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                              ))}
                            </Pie>
                            <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12 }} />
                          </PieChart>
                        </ResponsiveContainer>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </FadeIn>
            )}

            {/* Length distribution */}
            <FadeIn delay={0.2}>
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">{t("insights.textLength")}</CardTitle>
                  <CardDescription className="text-xs">{t("insights.textLengthDesc")}</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="h-[220px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={report.length_distribution}>
                        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                        <XAxis dataKey="bucket" tick={{ fontSize: 10 }} />
                        <YAxis tick={{ fontSize: 10 }} />
                        <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12 }} />
                        <Bar dataKey="count" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </CardContent>
              </Card>
            </FadeIn>

            {/* Issues / Action items */}
            <FadeIn delay={0.25}>
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">{t("insights.actionItems")}</CardTitle>
                  <CardDescription className="text-xs">{t("insights.actionItemsDesc")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {report.issues.length === 0 ? (
                    <Alert className="border-emerald-500/30 bg-emerald-500/5">
                      <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                      <AlertDescription className="text-sm">{t("insights.noIssues")}</AlertDescription>
                    </Alert>
                  ) : (
                    report.issues.map((iss) => (
                      <div key={iss.id} className="p-3 rounded-lg border border-border space-y-1">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-sm font-medium text-foreground">{iss.title}</p>
                          <Badge variant="outline" className="text-[9px] shrink-0">{iss.severity}</Badge>
                        </div>
                        <p className="text-xs text-muted-foreground">{iss.description}</p>
                        <p className="text-xs text-foreground flex items-start gap-1.5">
                          <CheckCircle2 className="h-3.5 w-3.5 text-primary shrink-0 mt-0.5" />
                          {iss.suggestion}
                        </p>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>
            </FadeIn>

            <FadeIn delay={0.3}>
              <div className="flex justify-between items-center pt-2">
                <p className="text-xs text-muted-foreground">
                  {datasetMeta?.name} · {datasetMeta?.num_samples.toLocaleString()} rows
                </p>
                <Button asChild>
                  <Link to={`/projects/${id}/training`}>{t("insights.startTraining")} →</Link>
                </Button>
              </div>
            </FadeIn>
              </>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </PageTransition>
  );
}
