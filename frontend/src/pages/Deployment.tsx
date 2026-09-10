import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageTransition, FadeIn } from "@/components/motion";
import {
  Rocket,
  Copy,
  Check,
  Download,
  Terminal,
  MessageSquare,
  Server,
  Activity,
  HardDrive,
  Code2,
  ExternalLink,
  Zap,
} from "lucide-react";
import { useModels, useTrainings, useModelDownloadUrl } from "@/hooks/queries";
import { buildTrainingNameMap, modelDisplayName } from "@/components/model/modelNaming";
import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { useToast } from "@/hooks/use-toast";
import type { ModelArtifact } from "@/api/types";

export default function Deployment() {
  const { data: modelsPage, isLoading } = useModels(undefined, { limit: 100 });
  const { data: trainingsPage } = useTrainings({ limit: 100 });
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const { toast } = useToast();
  const downloadMutation = useModelDownloadUrl();

  const models = modelsPage?.items ?? [];
  const trainingNames = useMemo(() => buildTrainingNameMap(trainingsPage?.items), [trainingsPage]);

  // Models with GGUF or Ollama tag are considered ready for deployment
  const deployedModels = useMemo(() => {
    return models.filter((m) => m.ollama_model_tag || m.gguf_uri);
  }, [models]);

  const selectedModel = useMemo(() => {
    if (selectedModelId) {
      return models.find((m) => m.id === selectedModelId) ?? deployedModels[0] ?? models[0];
    }
    return deployedModels[0] ?? models[0] ?? null;
  }, [selectedModelId, models, deployedModels]);

  const handleCopy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    toast({ title: "Copied to clipboard", description: text });
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleDownload = async (model: ModelArtifact) => {
    try {
      const res = await downloadMutation.mutateAsync({ id: model.id, format: "gguf" });
      const firstFile = res.files?.[0];
      if (firstFile?.url) {
        window.open(firstFile.url, "_blank");
        toast({ title: "Download started", description: firstFile.name });
      } else {
        toast({ title: "Download link ready", description: "No file URL returned from storage." });
      }
    } catch (e) {
      toast({
        title: "Download failed",
        description: e instanceof Error ? e.message : "Could not retrieve download URL",
        variant: "destructive",
      });
    }
  };

  const activeTag = selectedModel?.ollama_model_tag ?? (selectedModel ? `smt-model-${selectedModel.id.slice(0, 8)}` : "my-model");

  const curlSnippet = `curl -X POST http://localhost:8000/api/v1/inference/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -d '{
    "model": "${activeTag}",
    "messages": [
      {"role": "user", "content": "Hello, how can you help me today?"}
    ],
    "temperature": 0.7
  }'`;

  const pythonSnippet = `import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/api/v1/inference",
    api_key="YOUR_API_KEY"  # Or local bearer token
)

response = client.chat.completions.create(
    model="${activeTag}",
    messages=[
        {"role": "user", "content": "Hello, how can you help me today?"}
    ],
    temperature=0.7,
)

print(response.choices[0].message.content)`;

  const tsSnippet = `import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:8000/api/v1/inference",
  apiKey: "YOUR_API_KEY",
});

async function main() {
  const completion = await client.chat.completions.create({
    model: "${activeTag}",
    messages: [{ role: "user", content: "Hello, how can you help me today?" }],
    temperature: 0.7,
  });

  console.log(completion.choices[0].message.content);
}

main();`;

  const ollamaCliSnippet = `ollama run ${activeTag}`;

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                <Rocket className="h-6 w-6 text-primary" /> Model Deployment & Serving
              </h1>
              <p className="text-sm text-muted-foreground">
                Manage exported models, local Ollama serving, and OpenAI-compatible inference endpoints.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" asChild>
                <Link to="/models">
                  View All Models
                </Link>
              </Button>
            </div>
          </div>
        </FadeIn>

        {/* Top Summary Metrics */}
        <FadeIn delay={0.05}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Card>
              <CardContent className="p-4 flex items-center gap-3">
                <div className="p-2.5 rounded-lg bg-primary/10 text-primary">
                  <Server className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-foreground">{deployedModels.length}</p>
                  <p className="text-xs text-muted-foreground">Ollama Serving Models</p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4 flex items-center gap-3">
                <div className="p-2.5 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                  <Activity className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-foreground">{models.length}</p>
                  <p className="text-xs text-muted-foreground">Total Artifacts</p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4 flex items-center gap-3">
                <div className="p-2.5 rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
                  <HardDrive className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-foreground">
                    {models.filter((m) => m.gguf_uri).length}
                  </p>
                  <p className="text-xs text-muted-foreground">GGUF Exports</p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4 flex items-center gap-3">
                <div className="p-2.5 rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400">
                  <Zap className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-foreground">OpenAI</p>
                  <p className="text-xs text-muted-foreground">API Compatible</p>
                </div>
              </CardContent>
            </Card>
          </div>
        </FadeIn>

        {!isLoading && models.length === 0 ? (
          <EngineEmptyState
            icon={Rocket}
            title="No trained models available for deployment"
            hint="Train a model and export it to GGUF format to enable local Ollama serving and API endpoints."
            action={
              <Button asChild size="sm">
                <Link to="/projects/new">Create New Project</Link>
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Left: Deployed Models List */}
            <FadeIn delay={0.1} className="lg:col-span-1 space-y-4">
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-sm flex items-center justify-between">
                    <span>Exported Models</span>
                    <Badge variant="outline" className="text-[10px] font-mono">
                      {models.length} available
                    </Badge>
                  </CardTitle>
                  <CardDescription className="text-xs">
                    Select a model to view deployment details and client snippets.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-2.5 max-h-[580px] overflow-y-auto pr-1">
                  {models.map((m) => {
                    const isSelected = (selectedModel?.id ?? "") === m.id;
                    const hasOllama = Boolean(m.ollama_model_tag);
                    const hasGguf = Boolean(m.gguf_uri);

                    return (
                      <div
                        key={m.id}
                        onClick={() => setSelectedModelId(m.id)}
                        className={`p-3 rounded-lg border cursor-pointer transition-all space-y-1.5 ${
                          isSelected
                            ? "border-primary bg-primary/5 ring-1 ring-primary/30"
                            : "border-border hover:bg-muted/30"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-1">
                          <span className="font-semibold text-xs text-foreground truncate max-w-[190px]" title={m.name}>
                            {modelDisplayName(m, trainingNames)}
                          </span>
                          {hasOllama ? (
                            <Badge className="text-[9px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30">
                              Ollama Active
                            </Badge>
                          ) : hasGguf ? (
                            <Badge variant="secondary" className="text-[9px]">
                              GGUF Ready
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="text-[9px]">
                              LoRA
                            </Badge>
                          )}
                        </div>

                        <p className="text-[11px] text-muted-foreground font-mono truncate">
                          Base: {m.base_model.split("/").pop()}
                        </p>

                        {m.ollama_model_tag && (
                          <div className="flex items-center gap-1 text-[10px] text-primary font-mono bg-primary/5 px-2 py-0.5 rounded">
                            <Terminal className="h-3 w-3 shrink-0" />
                            <span className="truncate">{m.ollama_model_tag}</span>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </CardContent>
              </Card>
            </FadeIn>

            {/* Right: Selected Model Deployment Hub & Code Snippets */}
            <FadeIn delay={0.15} className="lg:col-span-2 space-y-5">
              {selectedModel ? (
                <>
                  <Card>
                    <CardHeader className="pb-4">
                      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                        <div>
                          <CardTitle className="text-base flex items-center gap-2">
                            {modelDisplayName(selectedModel, trainingNames)}
                          </CardTitle>
                          <CardDescription className="text-xs mt-0.5">
                            ID: <span className="font-mono">{selectedModel.id}</span> · Base: {selectedModel.base_model}
                          </CardDescription>
                        </div>
                        <div className="flex items-center gap-2">
                          {selectedModel.ollama_model_tag && (
                            <Button size="sm" variant="default" className="text-xs gap-1.5" asChild>
                              <Link to={`/playground?model=${encodeURIComponent(selectedModel.ollama_model_tag)}`}>
                                <MessageSquare className="h-3.5 w-3.5" /> Test in Playground
                              </Link>
                            </Button>
                          )}
                          {selectedModel.gguf_uri && (
                            <Button
                              size="sm"
                              variant="outline"
                              className="text-xs gap-1.5"
                              onClick={() => handleDownload(selectedModel)}
                            >
                              <Download className="h-3.5 w-3.5" /> Download GGUF
                            </Button>
                          )}
                        </div>
                      </div>
                    </CardHeader>

                    <CardContent className="space-y-4">
                      {/* CLI Command */}
                      {selectedModel.ollama_model_tag ? (
                        <div className="rounded-lg border p-3 bg-muted/40 space-y-1.5">
                          <div className="flex items-center justify-between text-xs font-semibold text-foreground">
                            <span className="flex items-center gap-1.5">
                              <Terminal className="h-3.5 w-3.5 text-primary" /> Run Locally with Ollama CLI
                            </span>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 text-xs gap-1 px-2"
                              onClick={() => handleCopy(ollamaCliSnippet, "ollama-cli")}
                            >
                              {copiedKey === "ollama-cli" ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                              Copy
                            </Button>
                          </div>
                          <pre className="text-xs font-mono bg-background p-2 rounded border overflow-x-auto text-primary">
                            {ollamaCliSnippet}
                          </pre>
                        </div>
                      ) : (
                        <div className="rounded-lg border p-3.5 bg-muted/30 flex items-center justify-between">
                          <div>
                            <p className="text-xs font-medium text-foreground">Model not yet exported to GGUF / Ollama</p>
                            <p className="text-[11px] text-muted-foreground mt-0.5">
                              Export this model to GGUF from the Model Detail page to enable local Ollama serving.
                            </p>
                          </div>
                          <Button size="sm" variant="outline" asChild className="text-xs gap-1 shrink-0">
                            <Link to={`/models/${selectedModel.id}`}>
                              Export Model <ExternalLink className="h-3 w-3" />
                            </Link>
                          </Button>
                        </div>
                      )}

                      {/* API Endpoints & Code Snippets Tabs */}
                      <div className="space-y-2 pt-2">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
                            <Code2 className="h-4 w-4 text-primary" /> Integration Snippets (OpenAI Compatible)
                          </span>
                        </div>

                        <Tabs defaultValue="curl" className="w-full">
                          <div className="flex items-center justify-between gap-2 border-b pb-1">
                            <TabsList className="h-8">
                              <TabsTrigger value="curl" className="text-xs py-1">cURL</TabsTrigger>
                              <TabsTrigger value="python" className="text-xs py-1">Python</TabsTrigger>
                              <TabsTrigger value="ts" className="text-xs py-1">TypeScript</TabsTrigger>
                            </TabsList>
                          </div>

                          <TabsContent value="curl" className="mt-2 relative">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="absolute top-2 right-2 h-7 text-xs gap-1 bg-background/80 backdrop-blur"
                              onClick={() => handleCopy(curlSnippet, "curl")}
                            >
                              {copiedKey === "curl" ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                              Copy
                            </Button>
                            <pre className="p-3.5 rounded-lg bg-muted text-xs font-mono overflow-x-auto text-foreground/90 border">
                              {curlSnippet}
                            </pre>
                          </TabsContent>

                          <TabsContent value="python" className="mt-2 relative">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="absolute top-2 right-2 h-7 text-xs gap-1 bg-background/80 backdrop-blur"
                              onClick={() => handleCopy(pythonSnippet, "python")}
                            >
                              {copiedKey === "python" ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                              Copy
                            </Button>
                            <pre className="p-3.5 rounded-lg bg-muted text-xs font-mono overflow-x-auto text-foreground/90 border">
                              {pythonSnippet}
                            </pre>
                          </TabsContent>

                          <TabsContent value="ts" className="mt-2 relative">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="absolute top-2 right-2 h-7 text-xs gap-1 bg-background/80 backdrop-blur"
                              onClick={() => handleCopy(tsSnippet, "ts")}
                            >
                              {copiedKey === "ts" ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
                              Copy
                            </Button>
                            <pre className="p-3.5 rounded-lg bg-muted text-xs font-mono overflow-x-auto text-foreground/90 border">
                              {tsSnippet}
                            </pre>
                          </TabsContent>
                        </Tabs>
                      </div>
                    </CardContent>
                  </Card>
                </>
              ) : null}
            </FadeIn>
          </div>
        )}
      </div>
    </PageTransition>
  );
}
