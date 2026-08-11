import { useEffect, useState, useCallback } from "react";
import type { Project } from "@/types";
import { listProjects, getProject, createProject, updateProject, deleteProject, type CreateProjectInput } from "@/lib/projectsApi";
import { useAuth } from "@/contexts/AuthContext";

export function useProjects() {
  const { user } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const refresh = useCallback(async () => {
    if (!user) {
      setProjects([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const data = await listProjects();
      setProjects(data);
      setError(null);
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const create = useCallback(async (input: CreateProjectInput) => {
    const created = await createProject(input);
    setProjects((prev) => [created, ...prev]);
    return created;
  }, []);

  const update = useCallback(async (id: string, patch: Partial<Project>) => {
    const updated = await updateProject(id, patch);
    setProjects((prev) => prev.map((p) => (p.id === id ? updated : p)));
    return updated;
  }, []);

  const remove = useCallback(async (id: string) => {
    await deleteProject(id);
    setProjects((prev) => prev.filter((p) => p.id !== id));
  }, []);

  return { projects, loading, error, refresh, create, update, remove };
}

export function useProject(id: string | undefined) {
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) {
      setProject(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    getProject(id)
      .then((p) => setProject(p))
      .finally(() => setLoading(false));
  }, [id]);

  return { project, loading, setProject };
}
