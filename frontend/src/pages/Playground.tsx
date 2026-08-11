import { useState } from "react";
import { PageTransition } from "@/components/motion";
import { Badge } from "@/components/ui/badge";

import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Columns2, MessageSquare } from "lucide-react";
import { ChatPanel } from "@/components/playground/ChatPanel";
import { useModels } from "@/hooks/useUserData";
import { useLanguage } from "@/i18n/LanguageContext";

export default function Playground() {
  const { models } = useModels();
  const [modelA, setModelA] = useState<string>("");
  const [modelB, setModelB] = useState<string>("");
  const [abMode, setAbMode] = useState(false);
  const { t } = useLanguage();

  if (models.length > 0 && !modelA) setModelA(models[0].id);
  if (models.length > 1 && !modelB) setModelB(models[1].id);

  const getModelName = (id: string) => models.find((m) => m.id === id)?.name || id;

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
          <div className={`grid gap-4 ${abMode ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1"}`}>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">{abMode ? "Model A" : t("playground.model")}</Label>
              <Select value={modelA} onValueChange={setModelA}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm">{m.name}</span>
                        <Badge variant="outline" className="text-[9px]">{m.taskType}</Badge>
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
                          <span className="font-mono text-sm">{m.name}</span>
                          <Badge variant="outline" className="text-[9px]">{m.taskType}</Badge>
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
        <ChatPanel key={`a-${modelA}`} modelName={getModelName(modelA)} className="h-full" />
        {abMode && <ChatPanel key={`b-${modelB}`} modelName={getModelName(modelB)} className="h-full" />}
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
