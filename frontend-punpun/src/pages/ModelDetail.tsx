import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import { PageTransition } from "@/components/motion";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ArrowLeft, FlaskConical, MessageSquare } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { queryKeys, useModel } from "@/hooks/queries";
import { jobRefetchInterval, useJobProgress } from "@/hooks/useJobProgress";
import { QueueBadge } from "@/components/engine/QueueBadge";
import { ExportPanel } from "@/components/model/ExportPanel";
import { DownloadMenu } from "@/components/model/DownloadMenu";
import { formatDateTime } from "@/lib/format";

export default function ModelDetail() {
  const { id } = useParams<{ id: string }>();
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  // Poll the artifact only while an export job is in flight; the cadence
  // lives in a ref so the lazily-evaluated refetchInterval callback can
  // read the latest value without re-subscribing the query.
  const exportPollRef = useRef<number | false>(false);

  const { data: model, isLoading } = useModel(id ?? "", {
    refetchInterval: () => exportPollRef.current,
  });

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

  return (
    <PageTransition>
      <div className="space-y-6 max-w-5xl">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/models"><ArrowLeft className="h-4 w-4" /></Link>
          </Button>
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl font-bold font-mono text-foreground truncate">{model.name}</h1>
              <QueueBadge queueState={model.queue_state} queuePosition={model.queue_position} />
            </div>
            <p className="text-sm text-muted-foreground mt-0.5 truncate">
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
          </div>
        </div>

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
              <span className="font-mono text-xs text-foreground">{model.training_job_id.slice(0, 8)}…</span>
            </div>
            {model.ollama_model_tag && (
              <div className="flex justify-between gap-4">
                <span className="text-muted-foreground">Ollama tag</span>
                <span className="font-mono text-xs text-foreground break-all text-right">{model.ollama_model_tag}</span>
              </div>
            )}
          </CardContent>
        </Card>

        <ExportPanel model={model} exportInFlight={Boolean(exportInFlight)} progress={progress} />
      </div>
    </PageTransition>
  );
}
