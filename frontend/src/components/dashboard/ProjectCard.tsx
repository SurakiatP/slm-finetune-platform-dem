import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/engine/ConfirmDialog";
import { QueueBadge } from "@/components/engine/QueueBadge";
import { queryKeys } from "@/hooks/queries";
import { useToast } from "@/hooks/use-toast";
import { useLanguage } from "@/i18n/LanguageContext";
import { useTaskTypeLabel } from "@/lib/labels";
import { deleteProjectCascade } from "@/lib/projectDelete";
import type { Project } from "@/api/types";

export function ProjectCard({ project }: { project: Project }) {
  const { t } = useLanguage();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const taskTypeLabel = useTaskTypeLabel();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deleteProjectCascade(project.id);
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      toast({ title: t("project.delete"), description: project.name });
      setConfirmOpen(false);
    } catch (error) {
      toast({
        title: t("common.error"),
        description: error instanceof Error ? error.message : String(error),
        variant: "destructive",
      });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      <Link to={`/projects/${project.id}`}>
        <Card className="hover:shadow-md transition-shadow cursor-pointer h-full group">
          <CardHeader className="pb-3">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-start gap-1.5 flex-1 min-w-0">
                <CardTitle className="text-sm font-semibold leading-tight">{project.name}</CardTitle>
              </div>
              <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.preventDefault()}>
                <QueueBadge queueState={project.queue_state} queuePosition={project.queue_position} />
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                  aria-label={t("project.delete")}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setConfirmOpen(true);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground line-clamp-2">{project.description}</p>
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="outline" className="text-[10px]">{taskTypeLabel(project.task_type)}</Badge>
            </div>
            <p className="text-[10px] text-muted-foreground">
              {new Date(project.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
            </p>
          </CardContent>
        </Card>
      </Link>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        onConfirm={handleDelete}
        title={t("project.delete")}
        description={
          <>
            {t("project.deleteConfirm")} <strong>{project.name}</strong>. {t("project.deleteCancelsJobs")}{" "}
            {t("pipelineHub.deleteDatasetsNote")}
          </>
        }
        confirmLabel={t("project.delete")}
        destructive
        loading={deleting}
      />
    </>
  );
}
