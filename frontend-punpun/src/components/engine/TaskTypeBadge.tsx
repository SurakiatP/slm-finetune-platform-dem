import { MessageCircleQuestion, Tags, Wrench, type LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { TaskType } from "@/api/types";

interface TaskConfig {
  icon: LucideIcon;
  className: string;
}

const taskConfig: Record<TaskType, TaskConfig> = {
  classification: {
    icon: Tags,
    className: "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  },
  tool_calling: {
    icon: Wrench,
    className: "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  },
  qa: {
    icon: MessageCircleQuestion,
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
};

interface TaskTypeBadgeProps {
  /** Known backend task type — picks up its icon/tone from taskConfig. */
  taskType?: TaskType;
  /** Explicit label override (or the only thing rendered when `taskType`
   *  isn't one of the three known values). Kept dumb on purpose: an
   *  unrecognized/undefined taskType still renders something sensible. */
  label?: string;
}

export function TaskTypeBadge({ taskType, label }: TaskTypeBadgeProps) {
  const config = taskType ? taskConfig[taskType] : undefined;
  const Icon = config?.icon;
  const text = label ?? taskType ?? "unknown";
  return (
    <Badge
      variant="outline"
      className={cn("gap-1", config?.className ?? "border-border bg-muted text-muted-foreground")}
    >
      {Icon && <Icon className="h-3 w-3" aria-hidden />}
      {text}
    </Badge>
  );
}
