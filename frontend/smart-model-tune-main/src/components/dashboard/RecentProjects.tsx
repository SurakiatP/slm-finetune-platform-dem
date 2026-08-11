import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { taskTypeLabels } from "@/data/mockData";
import type { ProjectStatus } from "@/types";
import { useProjects } from "@/hooks/useProjects";

const statusVariant: Record<ProjectStatus, "default" | "secondary" | "destructive" | "outline"> = {
  completed: "default",
  training: "secondary",
  queued: "outline",
  paused: "outline",
  failed: "destructive",
};

const statusLabel: Record<ProjectStatus, string> = {
  completed: "Completed",
  training: "Training",
  queued: "Queued",
  paused: "Paused",
  failed: "Failed",
};

export function RecentProjects() {
  const { projects } = useProjects();
  const recent = [...projects]
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
    .slice(0, 5);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Recent Projects</CardTitle>
          <Link to="/projects" className="text-xs text-primary hover:underline">View all</Link>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {recent.length === 0 ? (
          <div className="p-6 text-center text-sm text-muted-foreground">
            No projects yet. <Link to="/projects/new" className="text-primary hover:underline">Create your first project</Link>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Task</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Credits</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recent.map((p) => (
                <TableRow key={p.id}>
                  <TableCell>
                    <Link to={`/projects/${p.id}`} className="font-medium text-foreground hover:text-primary transition-colors">
                      {p.name}
                    </Link>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-xs">{taskTypeLabels[p.taskType]}</TableCell>
                  <TableCell>
                    <Badge variant={statusVariant[p.status]}>{statusLabel[p.status]}</Badge>
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground">{p.creditsCost}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
