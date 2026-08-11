import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ProjectCard } from "@/components/dashboard/ProjectCard";
import { NewProjectDialog } from "@/components/dashboard/NewProjectDialog";
import { taskTypeLabels } from "@/data/mockData";
import { Search } from "lucide-react";
import { PageTransition, FadeIn, StaggerContainer, MotionCard } from "@/components/motion";
import { ProjectCardSkeleton } from "@/components/skeletons/ProjectCardSkeleton";
import { useLanguage } from "@/i18n/LanguageContext";
import { useProjects } from "@/hooks/useProjects";

export default function Projects() {
  const [search, setSearch] = useState("");
  const [filterTask, setFilterTask] = useState("all");
  const { t } = useLanguage();
  const { projects, loading } = useProjects();

  const allTags = Array.from(new Set(projects.flatMap((p) => p.tags ?? [])));
  const [filterTag, setFilterTag] = useState("all");

  const filtered = projects
    .filter((p) => {
      const matchesSearch = p.name.toLowerCase().includes(search.toLowerCase());
      const matchesTask = filterTask === "all" || p.taskType === filterTask;
      const matchesTag = filterTag === "all" || p.tags?.includes(filterTag);
      return matchesSearch && matchesTask && matchesTag;
    })
    .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned));

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <h1 className="text-2xl font-bold text-foreground truncate">{t("projects.title")}</h1>
              <p className="text-sm text-muted-foreground">{t("projects.total").replace("{count}", String(projects.length))}</p>
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
            <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap lg:flex-nowrap">
              <Select value={filterTask} onValueChange={setFilterTask}>
                <SelectTrigger className="w-full sm:w-48" aria-label={t("projects.filterByTask")}>
                  <SelectValue placeholder={t("projects.filterByTask")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("projects.allTasks")}</SelectItem>
                  {Object.entries(taskTypeLabels).map(([key, label]) => (
                    <SelectItem key={key} value={key}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {allTags.length > 0 && (
                <Select value={filterTag} onValueChange={setFilterTag}>
                  <SelectTrigger className="w-full sm:w-44" aria-label={t("projects.filterByTag")}>
                    <SelectValue placeholder={t("projects.filterByTag")} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">{t("projects.allTags")}</SelectItem>
                    {allTags.map((tag) => (
                      <SelectItem key={tag} value={tag}>{tag}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>
        </FadeIn>

        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <ProjectCardSkeleton key={i} />
            ))}
          </div>
        ) : (
          <StaggerContainer className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((project) => (
              <MotionCard key={project.id}>
                <ProjectCard project={project} />
              </MotionCard>
            ))}
          </StaggerContainer>
        )}

        {!loading && filtered.length === 0 && (
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
