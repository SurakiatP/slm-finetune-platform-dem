import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageTransition } from "@/components/motion";
import { Badge } from "@/components/ui/badge";

import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Columns2, MessageSquare } from "lucide-react";
import { ChatPanel } from "@/components/playground/ChatPanel";
import { buildTrainingNameMap } from "@/components/model/modelNaming";
import { useLanguage } from "@/i18n/LanguageContext";
import { useInferenceModels, useModels, useTrainings } from "@/hooks/queries";

export default function Playground() {
  const [searchParams] = useSearchParams();
  const { data, isLoading: loadingModels, error: modelsErrorObj } = useInferenceModels();
  const models = data?.data ?? [];
  const [modelA, setModelA] = useState<string>(searchParams.get("model") ?? "");
  const [modelB, setModelB] = useState<string>("");
  const [abMode, setAbMode] = useState(false);
  const { t } = useLanguage();

  const modelsError = modelsErrorObj instanceof Error ? modelsErrorObj.message : null;

  useEffect(() => {
    if (models.length === 0) return;
    setModelA((current) => current || models[0]?.id || "");
    setModelB((current) => current || models[1]?.id || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [models]);

  // The Engine's inference model list is a flat OpenAI-style id (the Ollama
  // tag) with no training linkage of its own — join it to the fine-tuned
  // artifact's owning training client-side via `ollama_model_tag`, one list
  // query each, rather than resolving per-descriptor.
  const { data: artifactsPage } = useModels(undefined, { limit: 200 });
  const { data: trainingsPage } = useTrainings({ limit: 200 });
  const trainingNames = useMemo(() => buildTrainingNameMap(trainingsPage?.items), [trainingsPage]);
  const tagToTrainingName = useMemo(() => {
    const map: Record<string, string> = {};
    for (const artifact of artifactsPage?.items ?? []) {
      const trainingName = trainingNames[artifact.training_job_id];
      if (trainingName && artifact.ollama_model_tag) map[artifact.ollama_model_tag] = trainingName;
    }
    return map;
  }, [artifactsPage, trainingNames]);

  const getModelName = (id: string) => tagToTrainingName[id] ?? models.find((model) => model.id === id)?.id ?? id;

  return (
    <PageTransition>
    <div className="space-y-5 max-w-7xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">{t("playground.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("playground.subtitle")}</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <MessageSquare className="h-4 w-4 text-muted-foreground" />
            <Label htmlFor="ab-toggle" className="text-sm text-muted-foreground cursor-pointer">{t("playground.single")}</Label>
            <Switch id="ab-toggle" checked={abMode} onCheckedChange={setAbMode} />
            <Label htmlFor="ab-toggle" className="text-sm text-muted-foreground cursor-pointer flex items-center gap-1">
              <Columns2 className="h-4 w-4" /> A/B
            </Label>
          </div>
        </div>
      </div>

      <Card>
        <CardContent className="p-4">
          {modelsError && <p role="alert" className="mb-4 text-sm text-destructive">{modelsError}</p>}
          {!loadingModels && models.length === 0 && !modelsError && (
            <p className="mb-4 text-sm text-muted-foreground">No inference models are registered in the Engine.</p>
          )}
          <div className={`grid gap-4 ${abMode ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1"}`}>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">{abMode ? "Model A" : t("playground.model")}</Label>
              <Select value={modelA} onValueChange={setModelA}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      <div className="flex items-center gap-2">
                        {tagToTrainingName[m.id] ? (
                          <span className="flex flex-col">
                            <span className="text-sm">{tagToTrainingName[m.id]}</span>
                            <span className="font-mono text-[10px] text-muted-foreground">{m.id}</span>
                          </span>
                        ) : (
                          <span className="font-mono text-sm">{m.id}</span>
                        )}
                        <Badge variant="outline" className="text-[9px]">{m.owned_by}</Badge>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {abMode && (
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Model B</Label>
                <Select value={modelB} onValueChange={setModelB}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {models.map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        <div className="flex items-center gap-2">
                          {tagToTrainingName[m.id] ? (
                            <span className="flex flex-col">
                              <span className="text-sm">{tagToTrainingName[m.id]}</span>
                              <span className="font-mono text-[10px] text-muted-foreground">{m.id}</span>
                            </span>
                          ) : (
                            <span className="font-mono text-sm">{m.id}</span>
                          )}
                          <Badge variant="outline" className="text-[9px]">{m.owned_by}</Badge>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <div className={`grid gap-4 ${abMode ? "grid-cols-1 lg:grid-cols-2" : "grid-cols-1"}`}>
        {modelA && <ChatPanel key={`a-${modelA}`} modelId={modelA} displayName={getModelName(modelA)} className="h-full" />}
        {abMode && modelB && <ChatPanel key={`b-${modelB}`} modelId={modelB} displayName={getModelName(modelB)} className="h-full" />}
      </div>

      {abMode && (
        <Card className="bg-accent/50 border-primary/20">
          <CardContent className="p-4 flex items-start gap-3">
            <Columns2 className="h-5 w-5 text-primary shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-foreground">{t("playground.abMode")}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{t("playground.abDesc")}</p>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
    </PageTransition>
  );
}
