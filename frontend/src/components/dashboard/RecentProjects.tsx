import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useProjects, useTrainings } from "@/hooks/queries";
import { useTaskTypeLabel } from "@/lib/labels";
import type { JobStatus } from "@/api/types";

// The Engine has no project-level status column; the closest equivalent is the
// status of the project's most recent training job. Variants/labels kept in the
// same shape the original used for its mock `ProjectStatus`.
const statusVariant: Record<JobStatus, "default" | "secondary" | "destructive" | "outline"> = {
  completed: "default",
  running: "secondary",
  pending: "outline",
  cancelled: "outline",
  failed: "destructive",
};

const statusLabel: Record<JobStatus, string> = {
  completed: "Completed",
  running: "Training",
  pending: "Queued",
  cancelled: "Cancelled",
  failed: "Failed",
};

export function RecentProjects() {
  const { data } = useProjects({ limit: 100 });
  const { data: trainingsPage } = useTrainings({ limit: 100 });
  const taskTypeLabel = useTaskTypeLabel();

  const projects = data?.items ?? [];
  const recent = [...projects]
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    .slice(0, 5);

  const latestStatus = (projectId: string): JobStatus | null => {
    const runs = (trainingsPage?.items ?? [])
      .filter((tr) => tr.project_id === projectId)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    return runs[0]?.status ?? null;
  };

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
                <TableHead className="text-right">Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recent.map((p) => {
                const status = latestStatus(p.id);
                return (
                  <TableRow key={p.id}>
                    <TableCell>
                      <Link to={`/projects/${p.id}`} className="font-medium text-foreground hover:text-primary transition-colors">
                        {p.name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">{taskTypeLabel(p.task_type)}</TableCell>
                    <TableCell>
                      {status ? (
                        <Badge variant={statusVariant[status]}>{statusLabel[status]}</Badge>
                      ) : (
                        <span className="text-muted-foreground text-xs">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground">
                      {new Date(p.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
