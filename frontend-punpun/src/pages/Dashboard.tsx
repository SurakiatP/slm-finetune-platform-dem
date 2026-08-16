import { StatsCards } from "@/components/dashboard/StatsCards";
import { RecentProjects } from "@/components/dashboard/RecentProjects";
import { ActivityChart } from "@/components/dashboard/ActivityChart";
import { NewProjectDialog } from "@/components/dashboard/NewProjectDialog";
import { PageTransition, FadeIn } from "@/components/motion";
import { DashboardSkeleton } from "@/components/skeletons/DashboardSkeleton";
import { useModels, useProjects, useTrainings } from "@/hooks/queries";
import { useLanguage } from "@/i18n/LanguageContext";

export default function Dashboard() {
  const { t } = useLanguage();
  // The original gated the whole page on a fake 1.5s timer; keep the same
  // skeleton rhythm but drive it off the real Engine queries the sections
  // below consume (react-query dedupes, so the children reuse these).
  const { isLoading: projectsLoading } = useProjects({ limit: 100 });
  const { isLoading: modelsLoading } = useModels(undefined, { limit: 100 });
  const { isLoading: trainingsLoading } = useTrainings({ limit: 100 });
  const loading = projectsLoading || modelsLoading || trainingsLoading;

  if (loading) return <DashboardSkeleton />;

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-foreground">{t("dashboard.title")}</h1>
              <p className="text-sm text-muted-foreground">{t("dashboard.subtitle")}</p>
            </div>
            <NewProjectDialog />
          </div>
        </FadeIn>

        <FadeIn delay={0.1}>
          <StatsCards />
        </FadeIn>

        <FadeIn delay={0.2}>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <RecentProjects />
            <ActivityChart />
          </div>
        </FadeIn>
      </div>
    </PageTransition>
  );
}
