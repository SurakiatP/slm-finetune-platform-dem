import { ActivityChart } from "@/components/dashboard/ActivityChart";
import { NewProjectDialog } from "@/components/dashboard/NewProjectDialog";
import { RecentProjects } from "@/components/dashboard/RecentProjects";
import { StatsCards } from "@/components/dashboard/StatsCards";
import { FadeIn, PageTransition } from "@/components/motion";
import { useLanguage } from "@/i18n/LanguageContext";

export default function Dashboard() {
  const { t } = useLanguage();

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
