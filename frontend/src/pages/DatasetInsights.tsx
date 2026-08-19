import { useMemo, useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { PageTransition, FadeIn } from "@/components/motion";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ArrowLeft, Database, CheckCircle2, AlertTriangle, Tag, Activity, FileText, Trash2, Loader2 } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, PieChart, Pie } from "recharts";
import { mockDatasets } from "@/data/datasetMockData";
import { computeQualityReport } from "@/lib/qualityCalculator";
import { useLanguage } from "@/i18n/LanguageContext";
import { useProject } from "@/hooks/useProjects";
import {
  engineGetDatasetDownloadUrl,
  enginePreviewDataset,
  engineCancelDataset,
  engineListDatasets,
  engineDeleteDataset,
  type EngineDataset,
} from "@/lib/engineApi";
import { getEngineMeta } from "@/lib/engineStore";
import { useToast } from "@/hooks/use-toast";


const READINESS_BANDS = (score: number, t: (k: string) => string) => {
  if (score >= 85) return { label: t("insights.readyToTrain"), color: "text-emerald-500", icon: CheckCircle2 };
  if (score >= 65) return { label: t("insights.canTrainWithCaveats"), color: "text-yellow-500", icon: AlertTriangle };
  return { label: t("insights.fixBeforeTraining"), color: "text-destructive", icon: AlertTriangle };
};

const PIE_COLORS = ["hsl(var(--primary))", "hsl(var(--primary)/0.85)", "hsl(var(--primary)/0.7)", "hsl(var(--primary)/0.55)", "hsl(var(--primary)/0.4)", "hsl(var(--destructive)/0.7)"];

export default function DatasetInsights() {
  const { id } = useParams<{ id: string }>();
  const { project } = useProject(id);
  const { t } = useLanguage();
  const { toast } = useToast();

  const [realDatasets, setRealDatasets] = useState<EngineDataset[]>([]);
  const [selectedDataset, setSelectedDataset] = useState(mockDatasets[0].id);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const engineMeta = id ? getEngineMeta(id) : null;

  useEffect(() => {
    if (!engineMeta?.engineProjectId) return;
    let active = true;
    engineListDatasets(engineMeta.engineProjectId)
      .then((res) => {
        if (active && res.items.length > 0) {
          setRealDatasets(res.items);
          setSelectedDataset(res.items[0].id);
        }
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [engineMeta?.engineProjectId]);

  const report = useMemo(() => computeQualityReport(selectedDataset), [selectedDataset]);
  const activeDatasetObj = realDatasets.find((d) => d.id === selectedDataset);
  const datasetMeta = activeDatasetObj
    ? {
        name: activeDatasetObj.name || `Dataset ${activeDatasetObj.id.slice(0, 8)}`,
        format: activeDatasetObj.source.toUpperCase(),
        fileSize: activeDatasetObj.size_bytes ? `${Math.round(activeDatasetObj.size_bytes / 1024)} KB` : `${activeDatasetObj.num_samples} rows`,
      }
    : (mockDatasets.find((d) => d.id === selectedDataset) || mockDatasets[0]);

  const handleDeleteDataset = async () => {
    setIsDeleting(true);
    try {
      await engineDeleteDataset(selectedDataset);
      toast({ title: "Dataset deleted" });
      setRealDatasets((prev) => prev.filter((d) => d.id !== selectedDataset));
      const remaining = realDatasets.filter((d) => d.id !== selectedDataset);
      if (remaining.length > 0) setSelectedDataset(remaining[0].id);
      else setSelectedDataset(mockDatasets[0].id);
      setDeleteOpen(false);
    } catch (err) {
      toast({ title: "Failed to delete dataset", description: (err as Error).message, variant: "destructive" });
    } finally {
      setIsDeleting(false);
    }
  };

  const band = READINESS_BANDS(report.overallScore, t);
  const BandIcon = band.icon;

  // Coverage = how many class samples meet a healthy minimum (assume 30 per class as min target for fine-tune)
  const minPerClass = 30;
  const classesAboveMin = report.classDistribution.filter((c) => c.count >= minPerClass).length;
  const coveragePct = report.classDistribution.length
    ? Math.round((classesAboveMin / report.classDistribution.length) * 100)
    : 0;

  const labelCoverage = report.classDistribution.map((c) => ({
    ...c,
    healthy: c.count >= minPerClass,
  }));

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
            <Select value={selectedDataset} onValueChange={setSelectedDataset}>
              <SelectTrigger className="w-64"><SelectValue /></SelectTrigger>
              <SelectContent>
                {realDatasets.length > 0
                  ? realDatasets.map((d) => (
                      <SelectItem key={d.id} value={d.id}>
                        {d.name || `${d.source.toUpperCase()} (${d.num_samples} rows)`}
                      </SelectItem>
                    ))
                  : mockDatasets.map((d) => (
                      <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>
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
                    {t("insights.readinessDesc").replace("{score}", String(report.overallScore))}
                  </p>
                </div>
                <Badge variant="outline" className="text-xs shrink-0">
                  {t("insights.qualityScore")}: <span className={`ml-1 font-bold ${band.color}`}>{report.overallScore}/100</span>
                </Badge>
              </div>
            </AlertDescription>
          </Alert>
        </FadeIn>

        {/* Stats Cards */}
        <FadeIn delay={0.1}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { label: t("insights.totalRows"), value: report.totalRows.toLocaleString(), icon: FileText },
              { label: t("insights.uniqueLabels"), value: report.classDistribution.length || "—", icon: Tag },
              { label: t("insights.coverage"), value: `${coveragePct}%`, icon: Activity },
              { label: t("insights.duplicates"), value: report.duplicateRows, icon: AlertTriangle },
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
        {report.classDistribution.length > 0 && (
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
                          data={report.classDistribution}
                          dataKey="count"
                          nameKey="label"
                          cx="50%"
                          cy="50%"
                          outerRadius={90}
                          label={(entry) => entry.label}
                          labelLine={false}
                        >
                          {report.classDistribution.map((_, i) => (
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
                  <BarChart data={report.lengthDistribution}>
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
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-sm">Dataset Operations</CardTitle>
                <CardDescription className="text-xs">{datasetMeta.name} · {datasetMeta.format} · {datasetMeta.fileSize}</CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={async () => {
                  try {
                    const res = await engineGetDatasetDownloadUrl(selectedDataset);
                    window.open(res.download_url, "_blank");
                  } catch (err) {
                    alert("Download link not available: " + (err instanceof Error ? err.message : String(err)));
                  }
                }}>
                  Download JSONL
                </Button>
                <Button variant="outline" size="sm" onClick={async () => {
                  try {
                    const preview = await enginePreviewDataset(selectedDataset, 5);
                    alert(`Dataset Preview (${preview.total_rows} total rows):\n` + JSON.stringify(preview.rows, null, 2));
                  } catch (err) {
                    alert("Preview error: " + (err instanceof Error ? err.message : String(err)));
                  }
                }}>
                  Preview Data
                </Button>
                <Button variant="outline" size="sm" className="text-destructive hover:text-destructive" onClick={() => setDeleteOpen(true)}>
                  <Trash2 className="h-4 w-4 mr-1.5" />
                  Delete Dataset
                </Button>
                <Button variant="destructive" size="sm" onClick={async () => {
                  if (!confirm("Are you sure you want to cancel synthetic generation for this dataset?")) return;
                  try {
                    await engineCancelDataset(selectedDataset);
                    alert("Dataset generation cancelled.");
                  } catch (err) {
                    alert("Cancel error: " + (err instanceof Error ? err.message : String(err)));
                  }
                }}>
                  Cancel SDG Job
                </Button>
              </div>
            </CardHeader>
          </Card>

          {/* Delete Dataset Alert Dialog */}
          <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete Dataset</AlertDialogTitle>
                <AlertDialogDescription>
                  Are you sure you want to delete dataset <strong>{datasetMeta.name}</strong> from the FineTune Engine? This action cannot be undone.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  onClick={(e) => {
                    e.preventDefault();
                    void handleDeleteDataset();
                  }}
                  disabled={isDeleting}
                >
                  {isDeleting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  Delete Dataset
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>


          <div className="flex justify-between items-center pt-4">
            <p className="text-xs text-muted-foreground">
              {datasetMeta.name} · {datasetMeta.format} · {datasetMeta.fileSize}
            </p>
            <Button asChild>
              <Link to={`/projects/${id}/training`}>{t("insights.startTraining")} →</Link>
            </Button>
          </div>
        </FadeIn>
      </div>
    </PageTransition>
  );
}
