import { createContext, useContext, useEffect, useRef, ReactNode, useCallback } from "react";
import { supabase } from "@/integrations/supabase/client";
import { buildWsUrl } from "@/hooks/useTrainingWebSocket";
import { getEngineMeta } from "@/lib/engineStore";
import { listProjects, updateProject } from "@/lib/projectsApi";
import type { EngineJobProgress, EngineTrainingProgressEvent } from "@/lib/engineApi";

interface JobHandle {
  jobId: string;
  projectId: string;
  ws: WebSocket | null;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  destroyed: boolean;
  terminal: boolean;
  reconnectFailures: number;
  totalAttempts: number;
}

interface TrainingSyncContextValue {
  activeJobIds: string[];
}

const TrainingSyncContext = createContext<TrainingSyncContextValue>({ activeJobIds: [] });

export function TrainingSyncProvider({ children }: { children: ReactNode }) {
  const jobHandlesRef = useRef<Map<string, JobHandle>>(new Map());

  const destroyJobHandle = useCallback((handle: JobHandle) => {
    handle.destroyed = true;
    if (handle.reconnectTimer !== null) {
      clearTimeout(handle.reconnectTimer);
      handle.reconnectTimer = null;
    }
    if (handle.ws && handle.ws.readyState !== WebSocket.CLOSED) {
      try {
        handle.ws.close(1000, "cleanup");
      } catch {
        // ignore close errors
      }
    }
    handle.ws = null;
  }, []);

  const connectJob = useCallback((jobId: string, projectId: string) => {
    if (jobHandlesRef.current.has(jobId)) return;

    const handle: JobHandle = {
      jobId,
      projectId,
      ws: null,
      reconnectTimer: null,
      destroyed: false,
      terminal: false,
      reconnectFailures: 0,
      totalAttempts: 0,
    };

    jobHandlesRef.current.set(jobId, handle);

    const MAX_RECONNECT_FAILURES = 3;
    const MAX_TOTAL_ATTEMPTS = 8;

    async function startConnection() {
      if (handle.destroyed || handle.terminal || handle.reconnectFailures >= MAX_RECONNECT_FAILURES || handle.totalAttempts >= MAX_TOTAL_ATTEMPTS) {
        return;
      }
      handle.totalAttempts++;

      const { data: { session } } = await supabase.auth.getSession();
      if (handle.destroyed || handle.terminal) return;

      const token = session?.access_token;
      if (!token) return;

      try {
        const ws = new WebSocket(buildWsUrl(jobId), ["bearer", token]);
        handle.ws = ws;

        ws.onopen = () => {
          if (handle.destroyed) {
            ws.close(1000, "destroyed");
            return;
          }
          handle.reconnectFailures = 0;
        };

        ws.onmessage = async (e) => {
          if (handle.destroyed) return;
          try {
            const event = JSON.parse(e.data as string) as EngineJobProgress;
            if (event.type === "training_progress") {
              const progressEvent = event as EngineTrainingProgressEvent;
              const pct = progressEvent.steps_total > 0
                ? Math.round((progressEvent.step / progressEvent.steps_total) * 100)
                : 0;
              await updateProject(projectId, { status: "training", progress: Math.min(99, pct) });
            } else if (event.type === "completed") {
              handle.terminal = true;
              await updateProject(projectId, { status: "completed", progress: 100 });
              destroyJobHandle(handle);
              jobHandlesRef.current.delete(jobId);
            } else if (event.type === "failed") {
              handle.terminal = true;
              await updateProject(projectId, { status: "failed" });
              destroyJobHandle(handle);
              jobHandlesRef.current.delete(jobId);
            }
          } catch {
            // ignore malformed frame
          }
        };

        ws.onerror = () => {
          // websocket error handled in onclose
        };

        ws.onclose = () => {
          if (handle.destroyed || handle.terminal) return;

          handle.reconnectFailures++;
          if (handle.reconnectFailures >= MAX_RECONNECT_FAILURES || handle.totalAttempts >= MAX_TOTAL_ATTEMPTS) {
            destroyJobHandle(handle);
            jobHandlesRef.current.delete(jobId);
            return;
          }

          const delay = Math.min(1000 * 2 ** handle.reconnectFailures, 30000);
          handle.reconnectTimer = setTimeout(() => void startConnection(), delay);
        };
      } catch {
        destroyJobHandle(handle);
        jobHandlesRef.current.delete(jobId);
      }
    }

    void startConnection();
  }, [destroyJobHandle]);

  const syncActiveProjects = useCallback(async () => {
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        jobHandlesRef.current.forEach(destroyJobHandle);
        jobHandlesRef.current.clear();
        return;
      }

      const projects = await listProjects();
      const activeProjects = projects.filter((p) => p.status === "training" || p.status === "queued");

      const activeJobMap = new Map<string, string>(); // jobId -> projectId

      for (const p of activeProjects) {
        const meta = getEngineMeta(p.id);
        if (meta?.jobId) {
          activeJobMap.set(meta.jobId, p.id);
        }
      }

      // Cleanup jobs no longer active
      for (const [existingJobId, handle] of Array.from(jobHandlesRef.current.entries())) {
        if (!activeJobMap.has(existingJobId)) {
          destroyJobHandle(handle);
          jobHandlesRef.current.delete(existingJobId);
        }
      }

      // Connect newly active jobs
      for (const [jobId, projectId] of activeJobMap.entries()) {
        connectJob(jobId, projectId);
      }
    } catch {
      // Ignore sync errors
    }
  }, [connectJob, destroyJobHandle]);

  useEffect(() => {
    void syncActiveProjects();

    const interval = setInterval(() => {
      void syncActiveProjects();
    }, 5000);

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event) => {
      if (event === "SIGNED_OUT") {
        jobHandlesRef.current.forEach(destroyJobHandle);
        jobHandlesRef.current.clear();
      } else {
        void syncActiveProjects();
      }
    });

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void syncActiveProjects();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      clearInterval(interval);
      subscription.unsubscribe();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      jobHandlesRef.current.forEach(destroyJobHandle);
      jobHandlesRef.current.clear();
    };
  }, [destroyJobHandle, syncActiveProjects]);

  return (
    <TrainingSyncContext.Provider value={{ activeJobIds: Array.from(jobHandlesRef.current.keys()) }}>
      {children}
    </TrainingSyncContext.Provider>
  );
}

export function useTrainingSyncContext() {
  return useContext(TrainingSyncContext);
}
