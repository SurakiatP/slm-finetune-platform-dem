import { useEffect, useRef } from "react";
import { updateProject } from "@/lib/projectsApi";
import { getEngineMeta, patchEngineMeta } from "@/lib/engineStore";
import { useTrainingWebSocket } from "@/hooks/useTrainingWebSocket";
import type { Project, ProjectStatus } from "@/types";

// Real backend integration: connects to Engine WebSocket when a jobId is stored
// in engineStore for this project. Falls back to the progress simulation for
// projects that were created without an engine connection.

export function useTrainingSimulator(
  project: Project | null,
  onUpdate: (next: Project) => void,
  options: { tickMs?: number; stepPercent?: number } = {},
) {
  const { tickMs = 2000, stepPercent = 4 } = options;

  const meta = project ? getEngineMeta(project.id) : null;
  const jobId = meta?.jobId ?? null;

  // ── Real WebSocket path ────────────────────────────────────────────────────
  const { latestProgress, completed, failed } = useTrainingWebSocket(
    project?.status === "training" || project?.status === "queued" ? jobId : null,
  );

  useEffect(() => {
    if (!project || !jobId) return;
    if (latestProgress === null && completed === null && failed === null) return;

    const run = async () => {
      if (completed) {
        // Store artifact ID so Playground can use it
        if (completed.model_artifact_id) {
          patchEngineMeta(project.id, { modelArtifactId: completed.model_artifact_id });
        }
        const next = await updateProject(project.id, { status: "completed", progress: 100 });
        onUpdate(next);
        return;
      }

      if (failed) {
        const next = await updateProject(project.id, { status: "failed" });
        onUpdate(next);
        return;
      }

      if (latestProgress) {
        const pct = latestProgress.steps_total > 0
          ? Math.round((latestProgress.step / latestProgress.steps_total) * 100)
          : project.progress;
        if (pct === project.progress) return;
        const next = await updateProject(project.id, {
          status: "training",
          progress: Math.min(99, pct),
        });
        onUpdate(next);
      }
    };

    run().catch(() => {/* swallow */});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestProgress, completed, failed]);

  // ── Fallback simulation (no engine job) ───────────────────────────────────
  const runningRef = useRef(false);

  useEffect(() => {
    if (!project) return;
    if (jobId) return; // real WS is handling it

    const shouldRun: ProjectStatus[] = ["queued", "training"];
    if (!shouldRun.includes(project.status)) return;
    if (runningRef.current) return;
    runningRef.current = true;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      if (cancelled) return;
      try {
        let current = project;
        if (current.status === "queued") {
          const next = await updateProject(current.id, { status: "training", progress: 5 });
          if (cancelled) return;
          current = next;
          onUpdate(next);
        } else {
          const newProgress = Math.min(100, current.progress + stepPercent + Math.floor(Math.random() * 3));
          const newStatus: ProjectStatus = newProgress >= 100 ? "completed" : "training";
          const next = await updateProject(current.id, {
            progress: newProgress,
            status: newStatus,
            ...(newStatus === "completed"
              ? { creditsCost: Math.max(current.creditsCost, 50 + Math.floor(Math.random() * 80)) }
              : {}),
          });
          if (cancelled) return;
          onUpdate(next);
          if (newStatus === "completed") {
            runningRef.current = false;
            return;
          }
        }
      } catch {
        // swallow transient errors; loop will retry on next tick
      }
      timer = setTimeout(tick, tickMs);
    };

    timer = setTimeout(tick, tickMs);

    return () => {
      cancelled = true;
      runningRef.current = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, project?.status, jobId]);
}
