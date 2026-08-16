import { FolderKanban, Box, Clock } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { StaggerContainer, StaggerItem } from "@/components/motion";
import { motion } from "framer-motion";
import { useModels, useProjects, useTrainings } from "@/hooks/queries";

export function StatsCards() {
  const { data: projectsPage } = useProjects({ limit: 100 });
  const { data: modelsPage } = useModels(undefined, { limit: 100 });
  const { data: trainingsPage } = useTrainings({ limit: 100 });

  const totalProjects = projectsPage?.total ?? 0;
  const modelsTrained = modelsPage?.total ?? 0;
  // The original derived "training hours" from a mock `epochs * 0.5`; the
  // Engine reports real wall-clock start/end per training job instead.
  const trainingHours = (trainingsPage?.items ?? [])
    .reduce((s, tr) => {
      if (!tr.started_at) return s;
      const end = tr.ended_at ? new Date(tr.ended_at).getTime() : Date.now();
      const ms = end - new Date(tr.started_at).getTime();
      return ms > 0 ? s + ms / 3_600_000 : s;
    }, 0)
    .toFixed(1);

  const stats = [
    { label: "Total Projects", value: totalProjects, icon: FolderKanban, color: "text-primary" },
    { label: "Models Trained", value: modelsTrained, icon: Box, color: "text-success" },
    { label: "Training Hours", value: `${trainingHours}h`, icon: Clock, color: "text-warning" },
  ];

  return (
    <StaggerContainer className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {stats.map((s) => (
        <StaggerItem key={s.label}>
          <motion.div whileHover={{ y: -2, scale: 1.02 }} transition={{ duration: 0.2 }}>
            <Card>
              <CardContent className="p-5 flex items-center gap-4">
                <div className={`p-2.5 rounded-lg bg-accent ${s.color}`}>
                  <s.icon className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-foreground">{s.value}</p>
                  <p className="text-xs text-muted-foreground">{s.label}</p>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        </StaggerItem>
      ))}
    </StaggerContainer>
  );
}
