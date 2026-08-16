import { FolderKanban } from "lucide-react";
import { Link } from "react-router-dom";

import { EngineEmptyState } from "@/components/engine/EngineEmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { taskTypeLabel } from "@/components/dashboard/ProjectCard";
import { useProjects } from "@/hooks/queries";

export function RecentProjects() {
  const { data, isLoading } = useProjects({ limit: 100 });

  const recent = [...(data?.items ?? [])]
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
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
        {isLoading ? (
          <div className="p-4 space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : recent.length === 0 ? (
          <EngineEmptyState
            icon={FolderKanban}
            title="No projects yet"
            hint="Create your first project to start generating data and fine-tuning models."
            className="border-0 py-8"
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Task</TableHead>
                <TableHead className="text-right">Created</TableHead>
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
                  <TableCell className="text-muted-foreground text-xs">{taskTypeLabel[p.task_type]}</TableCell>
                  <TableCell className="text-right text-muted-foreground text-xs">
                    {new Date(p.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
