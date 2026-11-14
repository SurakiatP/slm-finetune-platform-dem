import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tag, HelpCircle, Zap, ListChecks, Loader2, RefreshCw, type LucideIcon } from "lucide-react";
import { toErrorDetail, type ProjectFormData } from "@/pages/NewProject";
import type { TaskType } from "@/api/types";
import { useTaskTypes } from "@/hooks/queries";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { useLanguage } from "@/i18n/LanguageContext";

const taskIcons: Record<TaskType, LucideIcon> = {
  classification: Tag,
  qa: HelpCircle,
  tool_calling: Zap,
};

export function TaskSelectionStep({
  formData,
  updateForm,
}: {
  formData: ProjectFormData;
  updateForm: (p: Partial<ProjectFormData>) => void;
}) {
  const { data: taskTypes, isLoading, isError, error, refetch } = useTaskTypes();
  const { t } = useLanguage();
  // Server-provided descriptions are English; prefer a translation when one exists.
  const describe = (taskType: string, fallback: string) => {
    const value = t(`newProject.task.desc.${taskType}`);
    return value.startsWith("newProject.task.desc.") ? fallback : value;
  };

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-semibold text-foreground">{t("newProject.task.title")}</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {t("newProject.task.hint")}
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" role="status">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("newProject.task.loading")}
        </div>
      ) : isError ? (
        <div className="space-y-3">
          <ErrorDetail error={toErrorDetail(error)} />
          <Button type="button" variant="outline" size="sm" className="gap-2" onClick={() => void refetch()}>
            <RefreshCw className="h-3.5 w-3.5" /> {t("common.tryAgain")}
          </Button>
        </div>
      ) : !taskTypes || taskTypes.length === 0 ? (
        <p className="py-8 text-sm text-muted-foreground">{t("newProject.task.empty")}</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {taskTypes.map((task) => {
            const selected = formData.taskType === task.task_type;
            const Icon = taskIcons[task.task_type] ?? ListChecks;
            return (
              <button
                key={task.task_type}
                type="button"
                onClick={() => updateForm({ taskType: task.task_type })}
                className={`text-left p-4 rounded-lg border-2 transition-all ${
                  selected
                    ? "border-primary bg-accent"
                    : "border-border hover:border-primary/40 bg-background"
                }`}
              >
                <div className="flex items-center gap-2.5 mb-2">
                  <div className={`p-1.5 rounded-md ${selected ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground"}`}>
                    <Icon className="h-4 w-4" />
                  </div>
                  <span className="font-semibold text-sm text-foreground">{task.display_name}</span>
                  {selected && <Badge className="ml-auto text-[10px]">{t("newProject.task.selected")}</Badge>}
                </div>
                <p className="text-xs text-muted-foreground mb-2">{describe(task.task_type, task.description)}</p>
                <div className="bg-secondary/50 rounded-md px-2.5 py-1.5">
                  <p className="text-[10px] font-mono text-muted-foreground line-clamp-2">
                    {JSON.stringify(task.example)}
                  </p>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
