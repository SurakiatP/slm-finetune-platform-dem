import { useState } from "react";
import { Search } from "lucide-react";

import { NewProjectDialog } from "@/components/dashboard/NewProjectDialog";
import { ProjectCard } from "@/components/dashboard/ProjectCard";
import { FadeIn, MotionCard, PageTransition, StaggerContainer } from "@/components/motion";
import { ProjectCardSkeleton } from "@/components/skeletons/ProjectCardSkeleton";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useProjects, useTaskTypes } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";
import type { TaskType } from "@/api/types";

export default function Projects() {
  const [search, setSearch] = useState("");
  const [filterTask, setFilterTask] = useState<TaskType | "all">("all");
  const { t } = useLanguage();
  const { data, isLoading, isError, error } = useProjects({ limit: 100 });
  const { data: taskTypes } = useTaskTypes();

  const projects = data?.items ?? [];

  const filtered = projects
    .filter((p) => {
      const matchesSearch = p.name.toLowerCase().includes(search.toLowerCase());
      const matchesTask = filterTask === "all" || p.task_type === filterTask;
      return matchesSearch && matchesTask;
    })
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <h1 className="text-2xl font-bold text-foreground truncate">{t("projects.title")}</h1>
              <p className="text-sm text-muted-foreground">
                {t("projects.total").replace("{count}", String(data?.total ?? 0))}
              </p>
            </div>
            <div className="flex-shrink-0">
              <NewProjectDialog />
            </div>
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div className="relative w-full lg:flex-1 lg:min-w-[260px]">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder={t("projects.searchPlaceholder")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9"
                aria-label={t("projects.searchPlaceholder")}
              />
            </div>
            <Select value={filterTask} onValueChange={(v) => setFilterTask(v as TaskType | "all")}>
              <SelectTrigger className="w-full sm:w-48" aria-label={t("projects.filterByTask")}>
                <SelectValue placeholder={t("projects.filterByTask")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("projects.allTasks")}</SelectItem>
                {(taskTypes ?? []).map((info) => (
                  <SelectItem key={info.task_type} value={info.task_type}>{info.display_name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </FadeIn>

        {isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <ProjectCardSkeleton key={i} />
            ))}
          </div>
        ) : isError ? (
          <FadeIn>
            <div className="text-center py-12 text-sm text-destructive">
              {error instanceof Error ? error.message : "Failed to load projects."}
            </div>
          </FadeIn>
        ) : (
          <StaggerContainer className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((project) => (
              <MotionCard key={project.id}>
                <ProjectCard project={project} />
              </MotionCard>
            ))}
          </StaggerContainer>
        )}

        {!isLoading && !isError && filtered.length === 0 && (
          <FadeIn>
            <div className="text-center py-12 text-muted-foreground">
              <p className="text-sm">{t("projects.noResults")}</p>
            </div>
          </FadeIn>
        )}
      </div>
    </PageTransition>
  );
}
