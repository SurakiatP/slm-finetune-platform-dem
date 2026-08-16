import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { AlertOctagon } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { ErrorDetail } from "@/components/engine/ErrorDetail";
import type { JobStatus } from "@/api/types";

interface DiagnosticPanelProps {
  status: JobStatus;
  /** Verbatim `Training.error_message` from the Engine — includes CUDA-OOM
   *  guidance text when applicable. Rendered as-is via ErrorDetail, never
   *  reworded or summarized. */
  errorMessage: string | null;
}

/** Failure panel for a training run. Renders the backend's own error text —
 *  no client-side heuristic diagnosis or fabricated root-cause guessing. */
export function DiagnosticPanel({ status, errorMessage }: DiagnosticPanelProps) {
  const { t } = useLanguage();

  if (status !== "failed") return null;

  return (
    <Card className="border-destructive/30 bg-destructive/5">
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2 text-destructive">
          <AlertOctagon className="h-4 w-4" />
          {t("diagnostic.title")}
        </CardTitle>
        <CardDescription className="text-xs">Error reported by the training worker</CardDescription>
      </CardHeader>
      <CardContent>
        <ErrorDetail error={{ detail: errorMessage }} />
      </CardContent>
    </Card>
  );
}
