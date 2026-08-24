import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { ApiError } from "@/api/client";
import type { Project } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useUpdateProject } from "@/hooks/queries";
import { useToast } from "@/hooks/use-toast";
import { useLanguage } from "@/i18n/LanguageContext";

interface EditProjectDialogProps {
  project: Project;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function EditProjectDialog({ project, open, onOpenChange }: EditProjectDialogProps) {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? "");
  const [nameError, setNameError] = useState<string | null>(null);

  const updateProject = useUpdateProject();

  // Re-prefill whenever the dialog is (re)opened for a given project, so
  // stale edits from a previous open don't linger.
  useEffect(() => {
    if (open) {
      setName(project.name);
      setDescription(project.description ?? "");
      setNameError(null);
      updateProject.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, project]);

  const handleOpenChange = (next: boolean) => {
    if (!next) updateProject.reset();
    onOpenChange(next);
  };

  const submit = () => {
    setNameError(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNameError(t("trainCreate.nameRequired"));
      return;
    }

    updateProject.mutate(
      { id: project.id, body: { name: trimmedName, description: description.trim() || null } },
      {
        onSuccess: () => {
          toast({ title: t("project.editSaved"), description: trimmedName });
          handleOpenChange(false);
        },
      },
    );
  };

  const apiError = updateProject.error instanceof ApiError ? updateProject.error : null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("project.edit")}</DialogTitle>
          <DialogDescription>{t("project.editTitle")}</DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="edit-project-name">
              {t("project.editNameLabel")} <span className="text-destructive">*</span>
            </Label>
            <Input
              id="edit-project-name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (nameError) setNameError(null);
              }}
              aria-invalid={!!nameError}
              required
            />
            {nameError && (
              <p role="alert" className="text-xs text-destructive">
                {nameError}
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="edit-project-description">{t("project.editDescriptionLabel")}</Label>
            <Textarea
              id="edit-project-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={4}
            />
          </div>

          {apiError && <ErrorDetail error={{ detail: apiError.message, code: apiError.code }} />}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={updateProject.isPending}>
              {updateProject.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t("common.confirm")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
