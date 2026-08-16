import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MessageSquare, PackageOpen } from "lucide-react";
import type { ModelArtifact } from "@/api/types";
import { StatusBadge } from "@/components/engine/StatusBadge";
import { formatDateTime } from "@/lib/format";
import { useLanguage } from "@/i18n/LanguageContext";

export function ModelCard({ model }: { model: ModelArtifact }) {
  const { t } = useLanguage();
  const formats: string[] = [];
  if (model.gguf_uri) formats.push("GGUF");
  if (model.lora_adapter_uri) formats.push("LoRA");
  if (model.safetensors_uri) formats.push("SafeTensors");

  return (
    <Link to={`/models/${model.id}`}>
      <Card className="hover:shadow-md transition-shadow cursor-pointer h-full">
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <CardTitle className="text-sm font-semibold font-mono truncate">{model.name}</CardTitle>
              <p className="text-[10px] text-muted-foreground mt-0.5 truncate">
                {model.base_model.replace(/^unsloth\//, "")}
              </p>
            </div>
            {model.export_status && <StatusBadge status={model.export_status} />}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-2 text-center">
            <div>
              <p className="text-sm font-bold text-foreground">
                {model.size_mb !== null ? `${model.size_mb.toFixed(0)} MB` : "—"}
              </p>
              <p className="text-[10px] text-muted-foreground">Size</p>
            </div>
            <div>
              <p className="text-sm font-bold text-foreground">{formatDateTime(model.created_at)}</p>
              <p className="text-[10px] text-muted-foreground">Created</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {formats.length > 0 ? (
              formats.map((f) => (
                <Badge key={f} variant="outline" className="text-[10px]">
                  {f}
                </Badge>
              ))
            ) : (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">
                Not exported
              </Badge>
            )}
          </div>
          <div className="flex gap-2 pt-1" onClick={(e) => e.preventDefault()}>
            <Button variant="outline" size="sm" className="flex-1 text-xs h-8" asChild>
              <Link to={`/models/${model.id}`}>
                <PackageOpen className="h-3 w-3 mr-1" /> {t("model.export")}
              </Link>
            </Button>
            {model.ollama_model_tag && (
              <Button variant="outline" size="sm" className="h-8 px-2" asChild>
                <Link to={`/playground?model=${encodeURIComponent(model.ollama_model_tag)}`}>
                  <MessageSquare className="h-3 w-3" />
                </Link>
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
