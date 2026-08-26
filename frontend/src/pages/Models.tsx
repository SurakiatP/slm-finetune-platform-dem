import { useMemo } from "react";
import { Link } from "react-router-dom";
import { ModelCard } from "@/components/dashboard/ModelCard";
import { buildTrainingNameMap, modelDisplayName } from "@/components/model/modelNaming";
import { PageTransition, FadeIn, StaggerContainer, MotionCard } from "@/components/motion";
import { ModelCardSkeleton } from "@/components/skeletons/ModelCardSkeleton";
import { Button } from "@/components/ui/button";
import { GitCompare, Box } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useModels, useTrainings } from "@/hooks/queries";

export default function Models() {
  const { data, isLoading } = useModels();
  // One unscoped trainings list, joined client-side below — avoids a
  // per-model GET /trainings/{id} just to resolve training_name.
  const { data: trainingsPage } = useTrainings({ limit: 200 });
  const { t } = useLanguage();
  const models = data?.items ?? [];
  const trainingNames = useMemo(() => buildTrainingNameMap(trainingsPage?.items), [trainingsPage]);

  return (
    <PageTransition>
      <div className="space-y-6 max-w-7xl">
        <FadeIn>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-foreground">{t("models.title")}</h1>
              <p className="text-sm text-muted-foreground">{t("models.available").replace("{count}", String(models.length))}</p>
            </div>
            <Button variant="outline" size="sm" asChild>
              <Link to="/models/compare" className="gap-2">
                <GitCompare className="h-4 w-4" /> {t("models.compare")}
              </Link>
            </Button>
          </div>
        </FadeIn>

        {isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <ModelCardSkeleton key={i} />
            ))}
          </div>
        ) : models.length === 0 ? (
          <div className="text-center py-16 border border-dashed rounded-lg">
            <Box className="h-10 w-10 mx-auto text-muted-foreground mb-3" />
            <p className="text-sm text-muted-foreground mb-3">{t("model.empty")}</p>
            <Button asChild size="sm"><Link to="/projects/new">Start a training project</Link></Button>
          </div>
        ) : (
          <StaggerContainer className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {models.map((model) => (
              <MotionCard key={model.id}>
                <ModelCard model={model} displayName={modelDisplayName(model, trainingNames)} />
              </MotionCard>
            ))}
          </StaggerContainer>
        )}
      </div>
    </PageTransition>
  );
}
