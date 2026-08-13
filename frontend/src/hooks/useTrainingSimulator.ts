import { useEffect } from "react";
import { getProject, updateProject } from "@/lib/projectsApi";
import { getEngineMeta, patchEngineMeta } from "@/lib/engineStore";
import { useTrainingWebSocket } from "@/hooks/useTrainingWebSocket";
import { engineGetModelArtifacts, engineGetTraining } from "@/lib/engineApi";
import type { Project } from "@/types";

// Real Engine synchronization. The historical name is kept to avoid a broad
// call-site rename, but this hook never fabricates status or progress.

export function useTrainingSimulator(
  project: Project | null,
  onUpdate: (next: Project) => void,
) {
  const meta = project ? getEngineMeta(project.id) : null;
  const jobId = meta?.jobId ?? null;

  // ── Real WebSocket path ────────────────────────────────────────────────────
  const { latestProgress, completed, failed, connectionFailed } = useTrainingWebSocket(
    project?.status === "training" || project?.status === "queued" ? jobId : null,
  );

  useEffect(() => {
    if (!project || !jobId) return;
    if (latestProgress === null && completed === null && failed === null) return;

    const run = async () => {
      if (completed) {
        // Store artifact ID so Playground can use it
        if (completed.model_artifact_id) {
          patchEngineMeta(project.id, { modelArtifactId: completed.model_artifact_id, phase: "completed", error: undefined });
        } else {
          patchEngineMeta(project.id, { phase: "completed", error: undefined });
        }
        const next = await updateProject(project.id, { status: "completed", progress: 100 });
        onUpdate(next);
        return;
      }

      if (failed) {
        patchEngineMeta(project.id, { phase: "failed", error: failed.error });
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

    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestProgress, completed, failed]);

  // While upload/SDG runs in the background there is no training job yet.
  // Poll only the real Supabase row so the page sees training/failed transitions.
  useEffect(() => {
    if (!project || jobId || project.status !== "queued") return;
    let active = true;
    const poll = async () => {
      const next = await getProject(project.id);
      if (active && next && next.updatedAt !== project.updatedAt) onUpdate(next);
    };
    const interval = setInterval(() => void poll(), 3000);
    void poll();
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [jobId, onUpdate, project]);

  // A browser cannot read the backend's pre-accept 4401/4403 close code.
  // Once repeated handshakes fail, poll the canonical training resource.
  useEffect(() => {
    if (!project || !meta?.trainingId || !connectionFailed) return;
    let active = true;
    let interval: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      const training = await engineGetTraining(meta.trainingId!);
      if (!active) return;
      if (training.status === "completed") {
        const artifacts = await engineGetModelArtifacts(training.id);
        const artifactId = artifacts.items[0]?.id;
        patchEngineMeta(project.id, {
          phase: "completed",
          error: undefined,
          ...(artifactId ? { modelArtifactId: artifactId } : {}),
        });
        const next = await updateProject(project.id, { status: "completed", progress: 100 });
        if (active) onUpdate(next);
        if (interval) clearInterval(interval);
      } else if (training.status === "failed" || training.status === "cancelled") {
        patchEngineMeta(project.id, {
          phase: "failed",
          error: training.error_message || `Training ${training.status}`,
        });
        const next = await updateProject(project.id, { status: "failed" });
        if (active) onUpdate(next);
        if (interval) clearInterval(interval);
      }
    };

    interval = setInterval(() => void poll(), 10000);
    void poll();
    return () => {
      active = false;
      if (interval) clearInterval(interval);
    };
  }, [connectionFailed, meta?.trainingId, onUpdate, project]);
}
