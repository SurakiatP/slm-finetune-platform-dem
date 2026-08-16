import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface EngineEmptyStateProps {
  icon: LucideIcon;
  title: string;
  hint?: string;
  action?: ReactNode;
  className?: string;
}

/** Icon + title + hint placeholder for empty lists (datasets, jobs, runs).
 *  Ported from frontend/src/components/ui/EmptyState.tsx onto the app's
 *  border/muted-foreground theme tokens. */
export function EngineEmptyState({ icon: Icon, title, hint, action, className }: EngineEmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-6 py-14 text-center",
        className,
      )}
    >
      <Icon className="h-8 w-8 text-muted-foreground/50" aria-hidden />
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {hint && <p className="max-w-sm text-xs text-muted-foreground">{hint}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
