import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageTransition } from "@/components/motion";
import { Badge } from "@/components/ui/badge";

import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Columns2, MessageSquare, Send, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ChatPanel, type ChatPanelHandle } from "@/components/playground/ChatPanel";
import { buildTrainingNameMap } from "@/components/model/modelNaming";
import { useLanguage } from "@/i18n/LanguageContext";
import { useInferenceModels, useModels, useTrainings } from "@/hooks/queries";
import { displayModelTag } from "@/lib/format";

/** Ollama's stock models report `owned_by: "library"`; everything else is a
 *  user's fine-tuned export (owned_by carries the owner namespace). */
const isBaseModel = (ownedBy: string) => ownedBy === "library";

export default function Playground() {
  const [searchParams] = useSearchParams();
  const { data, isLoading: loadingModels, error: modelsErrorObj } = useInferenceModels();
  // Fine-tuned models first, so the default selection is one of the user's own.
  const models = useMemo(
    () => [...(data?.data ?? [])].sort((a, b) => Number(isBaseModel(a.owned_by)) - Number(isBaseModel(b.owned_by))),
    [data],
  );
  const [modelA, setModelA] = useState<string>(searchParams.get("model") ?? "");
  const [modelB, setModelB] = useState<string>("");
  const [abMode, setAbMode] = useState(false);
  const { t } = useLanguage();

  // A/B mode: one shared input sends the same prompt to both panels.
  const panelA = useRef<ChatPanelHandle>(null);
  const panelB = useRef<ChatPanelHandle>(null);
  const [sharedInput, setSharedInput] = useState("");
  const [loadingA, setLoadingA] = useState(false);
  const [loadingB, setLoadingB] = useState(false);
  const onLoadingA = useCallback((v: boolean) => setLoadingA(v), []);
  const onLoadingB = useCallback((v: boolean) => setLoadingB(v), []);
  const sharedBusy = loadingA || loadingB;
  const sendShared = () => {
    const text = sharedInput.trim();
    if (!text || sharedBusy) return;
    panelA.current?.send(text);
    panelB.current?.send(text);
    setSharedInput("");
  };
  const stopShared = () => {
    panelA.current?.stop();
    panelB.current?.stop();
  };

  const modelsError = modelsErrorObj instanceof Error ? modelsErrorObj.message : null;

  useEffect(() => {
    if (models.length === 0) return;
    setModelA((current) => current || models[0]?.id || "");
    setModelB((current) => current || models[1]?.id || "");
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

  const getModelName = (id: string) => tagToTrainingName[id] ?? displayModelTag(id);

  const renderOption = (m: (typeof models)[number]) => (
    <SelectItem key={m.id} value={m.id}>
      <div className="flex items-center gap-2">
        <span className="flex flex-col">
          <span className="text-sm">{getModelName(m.id)}</span>
          {tagToTrainingName[m.id] && (
            <span className="font-mono text-[10px] text-muted-foreground">{displayModelTag(m.id)}</span>
          )}
        </span>
        <Badge variant="outline" className="text-[9px]">
          {isBaseModel(m.owned_by) ? t("playground.baseModelBadge") : t("playground.fineTunedBadge")}
        </Badge>
      </div>
    </SelectItem>
  );

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
            <p className="mb-4 text-sm text-muted-foreground">{t("playground.noModels")}</p>
          )}
          <div className={`grid gap-4 ${abMode ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1"}`}>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">{abMode ? t("playground.modelA") : t("playground.model")}</Label>
              <Select value={modelA} onValueChange={setModelA}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {models.map(renderOption)}
                </SelectContent>
              </Select>
            </div>
            {abMode && (
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">{t("playground.modelB")}</Label>
                <Select value={modelB} onValueChange={setModelB}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {models.map(renderOption)}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <div className={`grid gap-4 ${abMode ? "grid-cols-1 lg:grid-cols-2" : "grid-cols-1"}`}>
        {modelA && (
          <ChatPanel
            key={`a-${modelA}`}
            ref={panelA}
            modelId={modelA}
            displayName={abMode ? `A · ${getModelName(modelA)}` : getModelName(modelA)}
            className="h-full"
            hideInput={abMode}
            onLoadingChange={onLoadingA}
          />
        )}
        {abMode && modelB && (
          <ChatPanel
            key={`b-${modelB}`}
            ref={panelB}
            modelId={modelB}
            displayName={`B · ${getModelName(modelB)}`}
            className="h-full"
            hideInput
            onLoadingChange={onLoadingB}
          />
        )}
      </div>

      {abMode && (modelA || modelB) && (
        <div className="flex gap-2 rounded-lg border border-border bg-background p-3">
          <Input
            placeholder={t("playground.sharedPlaceholder")}
            aria-label={t("playground.sharedPlaceholder")}
            value={sharedInput}
            onChange={(e) => setSharedInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && sendShared()}
            className="border-0 shadow-none focus-visible:ring-0 bg-transparent"
            disabled={sharedBusy}
          />
          <Button aria-label={t("playground.sendBoth")} size="icon" onClick={sendShared} disabled={!sharedInput.trim() || sharedBusy} className="shrink-0">
            <Send className="h-4 w-4" />
          </Button>
          {sharedBusy && (
            <Button type="button" variant="outline" onClick={stopShared}>
              <Square className="mr-1 h-4 w-4" />{t("playground.stop")}
            </Button>
          )}
        </div>
      )}

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
