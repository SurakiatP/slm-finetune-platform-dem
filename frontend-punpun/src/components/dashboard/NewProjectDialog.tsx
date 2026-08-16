import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Loader2, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { useCreateProject, useTaskTypes } from "@/hooks/queries";
import { useToast } from "@/hooks/use-toast";
import { useLanguage } from "@/i18n/LanguageContext";
import type { TaskType } from "@/api/types";

/** Quick-create dialog: name + description + task type, then straight into
 *  the project detail page. Task type is permanent once training starts
 *  (ADR-005 — only 3 supported task types), so it's the one required field
 *  besides name. For the seed-data-first flow, the full wizard at
 *  /projects/new (untouched, still on the stub data layer) stays linked at
 *  the bottom rather than being replaced. */
export function NewProjectDialog() {
  const { t } = useLanguage();
  const { toast } = useToast();
  const navigate = useNavigate();
  const createProject = useCreateProject();
  const { data: taskTypes } = useTaskTypes();

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [taskType, setTaskType] = useState<TaskType | null>(null);

  const reset = () => {
    setName("");
    setDescription("");
    setTaskType(null);
  };

  const canSubmit = name.trim().length > 0 && taskType !== null && !createProject.isPending;

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!taskType || !name.trim()) return;

    createProject.mutate(
      { name: name.trim(), description: description.trim() || null, task_type: taskType },
      {
        onSuccess: (project) => {
          toast({ title: t("newProject.launched"), description: project.name });
          setOpen(false);
          reset();
          navigate(`/projects/${project.id}`);
        },
        onError: (error) => {
          toast({
            title: t("newProject.launchFailed"),
            description: error instanceof Error ? error.message : String(error),
            variant: "destructive",
          });
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button className="gap-2" aria-label={t("command.newProject")}>
          <Plus className="h-4 w-4" /> {t("command.newProject")}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New Project</DialogTitle>
          <DialogDescription>Task type is permanent — create a new project to switch tasks.</DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="new-project-name">Name</Label>
            <Input
              id="new-project-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              placeholder="support-ticket-router"
              autoFocus
              required
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="new-project-description">Description</Label>
            <Textarea
              id="new-project-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={2000}
              placeholder="What should the fine-tuned model do?"
              rows={3}
            />
          </div>

          <div className="space-y-1.5">
            <Label>Task Type</Label>
            {taskTypes ? (
              <RadioGroup value={taskType ?? undefined} onValueChange={(v) => setTaskType(v as TaskType)}>
                {taskTypes.map((info) => (
                  <label
                    key={info.task_type}
                    htmlFor={`new-project-task-${info.task_type}`}
                    className={`flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-colors ${
                      taskType === info.task_type
                        ? "border-primary bg-accent/40"
                        : "border-border hover:border-muted-foreground/40"
                    }`}
                  >
                    <RadioGroupItem
                      value={info.task_type}
                      id={`new-project-task-${info.task_type}`}
                      className="mt-0.5"
                    />
                    <div className="min-w-0">
                      <p className="text-xs font-medium text-foreground">{info.display_name}</p>
                      <p className="text-[11px] text-muted-foreground line-clamp-2">{info.description}</p>
                    </div>
                  </label>
                ))}
              </RadioGroup>
            ) : (
              <p className="text-xs text-muted-foreground">{t("common.loading")}</p>
            )}
          </div>

          <DialogFooter className="items-center pt-1 sm:justify-between">
            <Link
              to="/projects/new"
              className="text-[11px] text-muted-foreground hover:text-primary hover:underline"
              onClick={() => setOpen(false)}
            >
              Need seed data? Use the full setup wizard
            </Link>
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={!canSubmit} className="gap-2">
                {createProject.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Create Project
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
