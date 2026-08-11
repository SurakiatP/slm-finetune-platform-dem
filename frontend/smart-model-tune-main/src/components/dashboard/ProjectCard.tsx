import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Pin } from "lucide-react";
import type { Project, ProjectStatus } from "@/types";
import { taskTypeLabels, baseModelLabels } from "@/data/mockData";

const statusVariant: Record<ProjectStatus, "default" | "secondary" | "destructive" | "outline"> = {
  completed: "default",
  training: "secondary",
  queued: "outline",
  paused: "outline",
  failed: "destructive",
};

const tagColor = (tag: string) => {
  if (tag === "production") return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30";
  if (tag === "experiment") return "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30";
  if (tag.startsWith("client")) return "bg-purple-500/15 text-purple-700 dark:text-purple-400 border-purple-500/30";
  return "bg-muted text-muted-foreground";
};

export function ProjectCard({ project }: { project: Project }) {
  return (
    <Link to={`/projects/${project.id}`}>
      <Card className={`hover:shadow-md transition-shadow cursor-pointer h-full ${project.pinned ? "border-primary/40" : ""}`}>
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-start gap-1.5 flex-1 min-w-0">
              {project.pinned && <Pin className="h-3.5 w-3.5 text-primary shrink-0 mt-0.5 fill-primary" />}
              <CardTitle className="text-sm font-semibold leading-tight">{project.name}</CardTitle>
            </div>
            <Badge variant={statusVariant[project.status]} className="shrink-0 text-[10px]">
              {project.status}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground line-clamp-2">{project.description}</p>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="outline" className="text-[10px]">{taskTypeLabels[project.taskType]}</Badge>
            <Badge variant="outline" className="text-[10px]">{baseModelLabels[project.baseModel]}</Badge>
            {project.tags?.map((tag) => (
              <Badge key={tag} variant="outline" className={`text-[10px] ${tagColor(tag)}`}>
                {tag}
              </Badge>
            ))}
          </div>
          {project.status === "training" && (
            <div className="space-y-1">
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>Progress</span>
                <span>{project.progress}%</span>
              </div>
              <Progress value={project.progress} className="h-1.5" />
            </div>
          )}
          <p className="text-[10px] text-muted-foreground">
            {new Date(project.createdAt).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
          </p>
        </CardContent>
      </Card>
    </Link>
  );
}
