import { AlertTriangle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface ApiErrorLike {
  detail?: string | null;
  code?: string | null;
}

/** Renders an ApiError-ish payload verbatim in a destructive alert box —
 *  used for 422 VRAM-guard responses and CUDA-OOM failure messages where the
 *  backend's `detail` string is the whole point and shouldn't be reworded or
 *  truncated. `code` (when present) is shown as a small mono subtitle. */
export function ErrorDetail({ error }: { error: ApiErrorLike }) {
  return (
    <Alert variant="destructive">
      <AlertTriangle className="h-4 w-4" aria-hidden />
      <AlertTitle>{error.code ? <span className="font-mono text-xs">{error.code}</span> : "Error"}</AlertTitle>
      <AlertDescription className="whitespace-pre-wrap break-words">
        {error.detail ?? "An unknown error occurred."}
      </AlertDescription>
    </Alert>
  );
}
