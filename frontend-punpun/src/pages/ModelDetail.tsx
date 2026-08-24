import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useParams, Link, useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { PageTransition } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ArrowLeft, CheckCircle2, Copy, ExternalLink, FlaskConical, MessageSquare, Trash2 } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { queryKeys, useDeleteModel, useEvaluations, useModel, useTraining } from "@/hooks/queries";
import { useToast } from "@/hooks/use-toast";
import { jobRefetchInterval, useJobProgress } from "@/hooks/useJobProgress";
import { ConfirmDialog } from "@/components/engine/ConfirmDialog";
import { QueueBadge } from "@/components/engine/QueueBadge";
import { AutoPipelineStatus } from "@/components/model/AutoPipelineStatus";
import { ExportPanel } from "@/components/model/ExportPanel";
import { DownloadMenu } from "@/components/model/DownloadMenu";
import { formatDateTime, formatNumber } from "@/lib/format";
import { metricMeta, scalarMetrics } from "@/lib/metrics";

const codeExamples = {
  python: `import requests

url = "http://localhost:8000/api/v1/inference/chat/completions"
headers = {
    "Content-Type": "application/json"
}
payload = {
    "model": "MODEL_NAME",
    "messages": [
        {"role": "user", "content": "Your input here"}
    ],
    "max_tokens": 512,
    "temperature": 0.7
}

response = requests.post(url, json=payload, headers=headers)
print(response.json())`,
  curl: `curl -X POST http://localhost:8000/api/v1/inference/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "MODEL_NAME",
    "messages": [
      {"role": "user", "content": "Your input here"}
    ],
    "max_tokens": 512,
    "temperature": 0.7
  }'`,
  javascript: `const response = await fetch("http://localhost:8000/api/v1/inference/chat/completions", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: "MODEL_NAME",
    messages: [
      { role: "user", content: "Your input here" }
    ],
    max_tokens: 512,
    temperature: 0.7,
  }),
});
const data = await response.json();
console.log(data);`,
};

const INFERENCE_URL = "http://localhost:8000/api/v1/inference/chat/completions";

export default function ModelDetail() {
  const { id } = useParams<{ id: string }>();
  const { t } = useLanguage();
  const { toast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const deleteMutation = useDeleteModel();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [codeTab, setCodeTab] = useState<"python" | "curl" | "javascript">("python");
  const [copied, setCopied] = useState(false);
  // Poll the artifact only while an export job is in flight; the cadence
  // lives in a ref so the lazily-evaluated refetchInterval callback can
  // read the latest value without re-subscribing the query.
  const exportPollRef = useRef<number | false>(false);

  const { data: model, isLoading } = useModel(id ?? "", {
    refetchInterval: () => exportPollRef.current,
  });

  // Single targeted fetch of the owning training — only one model is shown
  // per page here, so this is the natural join (no N+1 across a list).
  const { data: training } = useTraining(model?.training_job_id ?? "");

  const { data: evalsPage } = useEvaluations(
    { model_artifact_id: id ?? "", limit: 20 },
    { enabled: Boolean(id) },
  );

  const exportInFlight = model?.export_status === "pending" || model?.export_status === "running";
  const progress = useJobProgress(exportInFlight ? model?.export_celery_task_id ?? null : null, {
    onTerminal: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.model(id ?? "") });
      void queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });
  const exportTerminal = progress.completed !== null || progress.failed !== null;

  useEffect(() => {
    exportPollRef.current = exportInFlight ? jobRefetchInterval(exportTerminal, progress.socketOpen) : false;
  }, [exportInFlight, exportTerminal, progress.socketOpen]);

  if (isLoading || !id) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">{t("common.loading")}</p>
      </div>
    );
  }

  if (!model) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">{t("modelDetail.notFound")}</p>
        <Button variant="link" asChild><Link to="/models">{t("modelDetail.backToModels")}</Link></Button>
      </div>
    );
  }

  // The original read fixed accuracy/f1/precision/recall off a mock row; the
  // Engine reports whatever the evaluation stage produced for this artifact.
  // A completed run with either scalar metrics or a judge score counts —
  // an LLM-judge-only evaluation has no metrics_json at all.
  const latestEval = (evalsPage?.items ?? [])
    .filter((e) => e.status === "completed" && (e.metrics_json || e.llm_judge_score !== null))
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
  const baseMetrics = latestEval ? scalarMetrics(latestEval.metrics_json ?? {}) : {};
  const metrics =
    latestEval && latestEval.llm_judge_score !== null
      ? { ...baseMetrics, llm_judge_score: latestEval.llm_judge_score }
      : baseMetrics;
  const metricEntries = Object.entries(metrics);

  const displayName = training?.training_name ?? model.name;

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text.replace(/MODEL_NAME/g, model.ollama_model_tag ?? model.name));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDelete = () => {
    deleteMutation.mutate(model.id, {
      onSuccess: () => {
        toast({ title: t("model.deleted"), description: displayName });
        setDeleteOpen(false);
        navigate("/models");
      },
      onError: (err: unknown) => {
        // A GGUF export still references this artifact — the server refuses
        // rather than orphaning the exported file. Stay on the page and
        // surface its own reason instead of a generic error toast.
        if (err instanceof ApiError && err.status === 409) {
          toast({
            title: t("model.deleteBlockedExport"),
            description: err.message,
            variant: "destructive",
          });
        } else {
          toast({
            title: t("common.error"),
            description: err instanceof Error ? err.message : String(err),
            variant: "destructive",
          });
        }
        setDeleteOpen(false);
      },
    });
  };

  return (
    <PageTransition>
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" asChild>
          <Link to="/models"><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold font-mono text-foreground" title={model.id}>{displayName}</h1>
            <QueueBadge queueState={model.queue_state} queuePosition={model.queue_position} />
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">
            {model.base_model.replace(/^unsloth\//, "")}
          </p>
        </div>
        <div className="flex gap-2">
          <DownloadMenu model={model} />
          {model.ollama_model_tag && (
            <Button variant="outline" size="sm" className="gap-2" asChild>
              <Link to={`/playground?model=${encodeURIComponent(model.ollama_model_tag)}`}>
                <MessageSquare className="h-3.5 w-3.5" /> {t("modelDetail.test")}
              </Link>
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="gap-2 text-destructive hover:text-destructive"
            onClick={() => setDeleteOpen(true)}
          >
            <Trash2 className="h-3.5 w-3.5" /> {t("model.delete")}
          </Button>
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">{t("modelDetail.overview")}</TabsTrigger>
          <TabsTrigger value="export">{t("modelDetail.export")}</TabsTrigger>
          <TabsTrigger value="api">{t("modelDetail.apiEndpoint")}</TabsTrigger>
          <TabsTrigger value="versions">{t("modelDetail.versions")}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4 mt-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">{t("modelDetail.modelInfo")}</CardTitle></CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="flex justify-between gap-4">
                  <span className="text-muted-foreground">Model ID</span>
                  <span className="font-medium text-foreground text-right break-all">{model.id}</span>
                </div>
                <div className="flex justify-between gap-4">
                  <span className="text-muted-foreground">{t("projectDetail.baseModel")}</span>
                  <span className="font-medium text-foreground text-right break-all">{model.base_model}</span>
                </div>
                <div className="flex justify-between gap-4">
                  <span className="text-muted-foreground">{t("dataset.fileSize")}</span>
                  <span className="font-medium text-foreground">
                    {model.size_mb !== null ? `${model.size_mb.toFixed(0)} MB` : "—"}
                  </span>
                </div>
                <div className="flex justify-between gap-4">
                  <span className="text-muted-foreground">{t("projectDetail.created")}</span>
                  <span className="font-medium text-foreground">{formatDateTime(model.created_at)}</span>
                </div>
                <div className="flex justify-between gap-4">
                  <span className="text-muted-foreground flex items-center gap-1">
                    <FlaskConical className="h-3 w-3" /> Training job
                  </span>
                  <span className="text-right">
                    {training?.training_name && (
                      <span className="block text-xs font-medium text-foreground">{training.training_name}</span>
                    )}
                    <span className="font-mono text-xs text-muted-foreground">{model.training_job_id.slice(0, 8)}…</span>
                  </span>
                </div>
                {model.ollama_model_tag && (
                  <div className="flex justify-between gap-4">
                    <span className="text-muted-foreground">Ollama tag</span>
                    <span className="font-mono text-xs text-foreground break-all text-right">{model.ollama_model_tag}</span>
                  </div>
                )}
              </CardContent>
            </Card>

            {metricEntries.length > 0 ? (
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-sm">{t("modelDetail.performanceMetrics")}</CardTitle></CardHeader>
                <CardContent className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    {metricEntries.map(([label, value]) => {
                      const meta = metricMeta(label, value);
                      return (
                        <div key={label} className="text-center p-3 rounded-lg bg-accent">
                          <p className="text-lg font-bold text-foreground">
                            {meta.kind === "score5" ? `${formatNumber(value, 2)}/5` : formatNumber(value)}
                          </p>
                          <p className="text-[10px] text-muted-foreground">{label}</p>
                        </div>
                      );
                    })}
                  </div>
                  {latestEval?.llm_judge_model && (
                    <p className="text-xs text-muted-foreground">
                      {t("modelNaming.judgeModel")}: <span className="font-mono text-foreground">{latestEval.llm_judge_model}</span>
                    </p>
                  )}
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardContent className="p-6 text-center text-muted-foreground text-sm">
                  {t("modelDetail.metricsNotAvailable")}
                </CardContent>
              </Card>
            )}
          </div>

          {training?.auto_pipeline && <AutoPipelineStatus pipeline={training.auto_pipeline} />}
        </TabsContent>

        <TabsContent value="export" className="space-y-4 mt-4">
          <ExportPanel model={model} exportInFlight={Boolean(exportInFlight)} progress={progress} />
        </TabsContent>

        <TabsContent value="api" className="space-y-4 mt-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">API Endpoint (OpenAI-Compatible)</CardTitle>
                <Badge variant="outline" className="text-[10px] gap-1">
                  <ExternalLink className="h-2.5 w-2.5" /> REST API
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-2 bg-secondary/50 rounded-md px-3 py-2">
                <code className="text-xs font-mono text-foreground flex-1 break-all">
                  POST {INFERENCE_URL}
                </code>
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => handleCopy(INFERENCE_URL)}>
                  {copied ? <CheckCircle2 className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
                </Button>
              </div>

              <div>
                <div className="flex gap-1 mb-2">
                  {(["python", "curl", "javascript"] as const).map((lang) => (
                    <Button
                      key={lang}
                      variant={codeTab === lang ? "default" : "ghost"}
                      size="sm"
                      className="text-xs h-7 capitalize"
                      onClick={() => setCodeTab(lang)}
                    >
                      {lang}
                    </Button>
                  ))}
                </div>
                <div className="relative">
                  <pre className="bg-foreground/[0.03] border border-border rounded-lg p-4 overflow-x-auto text-[11px] font-mono text-foreground leading-relaxed">
                    {codeExamples[codeTab].replace(/MODEL_NAME/g, model.ollama_model_tag ?? model.name)}
                  </pre>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="absolute top-2 right-2 h-7 text-[10px] gap-1"
                    onClick={() => handleCopy(codeExamples[codeTab])}
                  >
                    {copied ? <CheckCircle2 className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
                    Copy
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="versions" className="space-y-4 mt-4">
          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-sm">{t("modelDetail.modelVersions")}</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              {[
                {
                  version: "v1.0",
                  date: model.created_at,
                  metric: metricEntries[0],
                  status: "current",
                  note: "Initial release",
                },
              ].map((v) => (
                <div key={v.version} className="flex items-center justify-between p-3 rounded-lg border border-border">
                  <div className="flex items-center gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold font-mono text-foreground">{v.version}</span>
                        {v.status === "current" && <Badge className="text-[9px]">Current</Badge>}
                      </div>
                      <p className="text-[10px] text-muted-foreground mt-0.5">
                        {new Date(v.date).toLocaleDateString()} · {v.note}
                      </p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-bold text-foreground">
                      {v.metric ? formatNumber(v.metric[1]) : "—"}
                    </p>
                    <p className="text-[10px] text-muted-foreground">{v.metric ? v.metric[0] : "no metrics"}</p>
                  </div>
                </div>
              ))}
              <p className="text-xs text-muted-foreground text-center pt-2">
                {t("modelDetail.newVersionHint")}
              </p>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>

    <ConfirmDialog
      open={deleteOpen}
      onOpenChange={setDeleteOpen}
      onConfirm={handleDelete}
      title={t("model.delete")}
      description={t("model.deleteConfirm")}
      confirmLabel={t("model.delete")}
      destructive
      loading={deleteMutation.isPending}
    />
    </PageTransition>
  );
}
