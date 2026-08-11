import { FolderKanban, Box, Clock } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { StaggerContainer, StaggerItem } from "@/components/motion";
import { motion } from "framer-motion";
import { useProjects } from "@/hooks/useProjects";
import { useModels } from "@/hooks/useUserData";

export function StatsCards() {
  const { projects } = useProjects();
  const { models } = useModels();

  const totalProjects = projects.length;
  const modelsTrained = models.length;
  const trainingHours = projects.reduce((s, p) => s + (p.epochs * 0.5), 0).toFixed(1);

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
