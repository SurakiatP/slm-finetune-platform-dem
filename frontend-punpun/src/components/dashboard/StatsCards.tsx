import { motion } from "framer-motion";
import { Box, FolderKanban, Sparkles } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StaggerContainer, StaggerItem } from "@/components/motion";
import { useModels, useProjects } from "@/hooks/queries";

const DAY_MS = 24 * 60 * 60 * 1000;

export function StatsCards() {
  const { data: projectsPage, isLoading: projectsLoading } = useProjects({ limit: 100 });
  const { data: modelsPage, isLoading: modelsLoading } = useModels(undefined, { limit: 100 });

  if (projectsLoading || modelsLoading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-[76px] rounded-xl" />
        ))}
      </div>
    );
  }

  const projects = projectsPage?.items ?? [];
  const weekAgo = Date.now() - 7 * DAY_MS;
  const newThisWeek = projects.filter((p) => new Date(p.created_at).getTime() >= weekAgo).length;

  const stats = [
    { label: "Total Projects", value: projectsPage?.total ?? 0, icon: FolderKanban, color: "text-primary" },
    { label: "Models Trained", value: modelsPage?.total ?? 0, icon: Box, color: "text-success" },
    { label: "New This Week", value: newThisWeek, icon: Sparkles, color: "text-warning" },
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
