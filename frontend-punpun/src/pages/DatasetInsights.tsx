import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { PageTransition, FadeIn } from "@/components/motion";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ArrowLeft, Database, Loader2, Sparkles } from "lucide-react";
import { DatasetList } from "@/components/dataset/DatasetList";
import { QualityReport } from "@/components/dataset/QualityReport";
import { mockDatasets } from "@/data/datasetMockData";
import { useLanguage } from "@/i18n/LanguageContext";
import { useProject } from "@/hooks/queries";

export default function DatasetInsights() {
  const { id } = useParams<{ id: string }>();
  const { data: project, isLoading } = useProject(id ?? "");
  const { t } = useLanguage();
  const [demoDatasetId, setDemoDatasetId] = useState(mockDatasets[0].id);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">{t("projectDetail.notFound")}</p>
        <Button variant="link" asChild><Link to="/projects">{t("projectDetail.backToProjects")}</Link></Button>
      </div>
    );
  }

  return (
    <PageTransition>
      <div className="space-y-6 max-w-6xl">
        <FadeIn>
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="icon" asChild>
              <Link to={`/projects/${id}`}><ArrowLeft className="h-4 w-4" /></Link>
            </Button>
            <div className="flex-1">
              <h1 className="text-xl font-bold text-foreground flex items-center gap-2">
                <Database className="h-5 w-5 text-primary" />
                {t("insights.title")}
              </h1>
              <p className="text-sm text-muted-foreground">{project.name}</p>
            </div>
          </div>
        </FadeIn>

        <FadeIn delay={0.05}>
          <Tabs defaultValue="datasets">
            <TabsList>
              <TabsTrigger value="datasets">
                <Database className="h-3.5 w-3.5 mr-1.5" />
                Datasets
              </TabsTrigger>
              <TabsTrigger value="quality">
                <Sparkles className="h-3.5 w-3.5 mr-1.5" />
                Quality Report
              </TabsTrigger>
            </TabsList>

            <TabsContent value="datasets" className="mt-4">
              <DatasetList projectId={project.id} />
            </TabsContent>

            <TabsContent value="quality" className="mt-4 space-y-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-muted-foreground">
                  Illustrative quality analysis over a sample dataset — not yet wired to a real
                  dataset's rows.
                </p>
                <Select value={demoDatasetId} onValueChange={setDemoDatasetId}>
                  <SelectTrigger className="w-56"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {mockDatasets.map((d) => (
                      <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <QualityReport datasetId={demoDatasetId} />
            </TabsContent>
          </Tabs>
        </FadeIn>
      </div>
    </PageTransition>
  );
}
