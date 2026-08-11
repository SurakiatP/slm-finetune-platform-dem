import { useParams, Link } from "react-router-dom";
import { PageTransition } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ArrowLeft, Download, Rocket, MessageSquare, Copy, ExternalLink, CheckCircle2 } from "lucide-react";
import { taskTypeLabels, baseModelLabels } from "@/data/mockData";
import { useState, useEffect } from "react";
import { getModel, type TrainedModelExt } from "@/lib/modelsApi";
import { useLanguage } from "@/i18n/LanguageContext";

const exportFormats = [
  { format: "SafeTensors", size: "1.2 GB", description: "Default format, best compatibility with HuggingFace" },
  { format: "GGUF", size: "0.8 GB", description: "Optimized for llama.cpp and local inference" },
  { format: "ONNX", size: "1.0 GB", description: "Cross-platform deployment with ONNX Runtime" },
];

const codeExamples = {
  python: `import requests

url = "https://api.slmstudio.dev/v1/inference"
headers = {
    "Authorization": "Bearer YOUR_API_KEY",
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
  curl: `curl -X POST https://api.slmstudio.dev/v1/inference \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "MODEL_NAME",
    "messages": [
      {"role": "user", "content": "Your input here"}
    ],
    "max_tokens": 512,
    "temperature": 0.7
  }'`,
  javascript: `const response = await fetch("https://api.slmstudio.dev/v1/inference", {
  method: "POST",
  headers: {
    "Authorization": "Bearer YOUR_API_KEY",
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

export default function ModelDetail() {
  const { id } = useParams<{ id: string }>();
  const [model, setModel] = useState<TrainedModelExt | null>(null);
  const [loading, setLoading] = useState(true);
  const [codeTab, setCodeTab] = useState<"python" | "curl" | "javascript">("python");
  const [copied, setCopied] = useState(false);
  const { t } = useLanguage();

  useEffect(() => {
    if (!id) return;
    getModel(id).then(setModel).finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return <div className="text-center py-20"><p className="text-muted-foreground">Loading...</p></div>;
  }

  if (!model) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">{t("modelDetail.notFound")}</p>
        <Button variant="link" asChild><Link to="/models">{t("modelDetail.backToModels")}</Link></Button>
      </div>
    );
  }

  const metrics = {
    accuracy: model.accuracy,
    f1Score: model.f1Score,
    precision: model.precision,
    recall: model.recall,
    rouge1: model.f1Score,
    latencyMs: model.latencyMs,
  };

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text.replace("MODEL_NAME", model.name));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const statusColor: Record<string, "default" | "secondary" | "outline"> = {
    deployed: "default",
    deploying: "secondary",
    ready: "outline",
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
            <h1 className="text-xl font-bold font-mono text-foreground">{model.name}</h1>
            <Badge variant={statusColor[model.status]}>{model.status}</Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-0.5">
            {baseModelLabels[model.baseModel]} · {taskTypeLabels[model.taskType]}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" className="gap-2" asChild>
            <Link to="/playground">
              <MessageSquare className="h-3.5 w-3.5" /> {t("modelDetail.test")}
            </Link>
          </Button>
          <Button size="sm" className="gap-2">
            <Rocket className="h-3.5 w-3.5" />
            {model.status === "deployed" ? t("modelDetail.deployed") : t("modelDetail.deploy")}
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
                {[
                  ["Model ID", model.id],
                  [t("projectDetail.baseModel"), baseModelLabels[model.baseModel]],
                  [t("projectDetail.taskType"), taskTypeLabels[model.taskType]],
                  [t("dataset.fileSize"), model.fileSize],
                  [t("dataset.format"), model.format],
                  [t("projectDetail.created"), new Date(model.createdAt).toLocaleString()],
                ].map(([label, value]) => (
                  <div key={String(label)} className="flex justify-between">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="font-medium text-foreground">{value}</span>
                  </div>
                ))}
              </CardContent>
            </Card>

            {metrics ? (
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-sm">{t("modelDetail.performanceMetrics")}</CardTitle></CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 gap-3">
                    {[
                      ["Accuracy", `${metrics.accuracy}%`],
                      ["F1 Score", `${metrics.f1Score}%`],
                      ["Precision", `${metrics.precision}%`],
                      ["Recall", `${metrics.recall}%`],
                      ...(metrics.rouge1 > 0 ? [["ROUGE-1", `${metrics.rouge1}%`]] : []),
                      ["Latency", `${metrics.latencyMs}ms`],
                    ].map(([label, value]) => (
                      <div key={String(label)} className="text-center p-3 rounded-lg bg-accent">
                        <p className="text-lg font-bold text-foreground">{value}</p>
                        <p className="text-[10px] text-muted-foreground">{label}</p>
                      </div>
                    ))}
                  </div>
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
        </TabsContent>

        <TabsContent value="export" className="space-y-4 mt-4">
          <div className="space-y-3">
            {exportFormats.map((fmt) => (
              <Card key={fmt.format}>
                <CardContent className="p-4 flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <div className="p-2.5 rounded-lg bg-accent">
                      <Download className="h-4 w-4 text-accent-foreground" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-foreground">{fmt.format}</p>
                      <p className="text-xs text-muted-foreground">{fmt.description}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-muted-foreground">{fmt.size}</span>
                    <Button variant="outline" size="sm" className="gap-2">
                      <Download className="h-3.5 w-3.5" /> {t("modelDetail.download")}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
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
                <code className="text-xs font-mono text-foreground flex-1">
                  POST https://api.slmstudio.dev/v1/inference
                </code>
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => handleCopy("https://api.slmstudio.dev/v1/inference")}>
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
                    {codeExamples[codeTab].replace(/MODEL_NAME/g, model.name)}
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
                { version: "v1.0", date: model.createdAt, accuracy: model.accuracy, status: "current", note: "Initial release" },
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
                    <p className="text-sm font-bold text-foreground">{v.accuracy}%</p>
                    <p className="text-[10px] text-muted-foreground">accuracy</p>
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
    </PageTransition>
  );
}
