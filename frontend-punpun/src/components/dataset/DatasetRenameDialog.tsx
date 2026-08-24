import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { ApiError } from "@/api/client";
import type { Dataset } from "@/api/types";
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
import { useUpdateDataset } from "@/hooks/queries";
import { useToast } from "@/hooks/use-toast";
import { useLanguage } from "@/i18n/LanguageContext";

interface DatasetRenameDialogProps {
  dataset: Dataset;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Rename a dataset via PATCH /datasets/{id}. Mirrors EditProjectDialog's
 *  prefill/validate/submit shape: required non-empty name, inline ErrorDetail
 *  on failure, toast on success. */
export function DatasetRenameDialog({ dataset, open, onOpenChange }: DatasetRenameDialogProps) {
  const { t } = useLanguage();
  const { toast } = useToast();
  const [name, setName] = useState(dataset.name);
  const [nameError, setNameError] = useState<string | null>(null);

  const updateDataset = useUpdateDataset();

  // Re-prefill whenever the dialog is (re)opened for a given dataset, so
  // stale edits from a previous open don't linger.
  useEffect(() => {
    if (open) {
      setName(dataset.name);
      setNameError(null);
      updateDataset.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, dataset]);

  const handleOpenChange = (next: boolean) => {
    if (!next) updateDataset.reset();
    onOpenChange(next);
  };

  const submit = () => {
    setNameError(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNameError(t("trainCreate.nameRequired"));
      return;
    }

    updateDataset.mutate(
      { id: dataset.id, body: { name: trimmedName } },
      {
        onSuccess: () => {
          toast({ title: t("dataset.renameSaved"), description: trimmedName });
          handleOpenChange(false);
        },
      },
    );
  };

  const apiError = updateDataset.error instanceof ApiError ? updateDataset.error : null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("dataset.rename")}</DialogTitle>
          <DialogDescription>{t("dataset.renameTitle")}</DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="rename-dataset-name">
              {t("dataset.renameLabel")} <span className="text-destructive">*</span>
            </Label>
            <Input
              id="rename-dataset-name"
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

          {apiError && <ErrorDetail error={{ detail: apiError.message, code: apiError.code }} />}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={updateDataset.isPending}>
              {updateDataset.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t("common.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
