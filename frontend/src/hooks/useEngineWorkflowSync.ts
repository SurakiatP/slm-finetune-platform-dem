import { useCallback, useEffect, useRef, useState } from "react";
import { getProject, updateProject } from "@/lib/projectsApi";
import { getEngineMeta, patchEngineMeta, type EngineProjectMeta } from "@/lib/engineStore";
import { useTrainingWebSocket } from "@/hooks/useTrainingWebSocket";
import {
  EngineApiError,
  engineExportModel,
  engineGetJobProgress,
  engineGetModelArtifact,
  engineGetModelArtifacts,
  engineGetTraining,
  type EngineJobFailedEvent,
  type EngineTrainingProgressEvent,
} from "@/lib/engineApi";
import {
  isEngineWorkflowActive,
  runEngineWorkflow,
  workflowInputFromProject,
} from "@/lib/engineWorkflow";
import type { Project } from "@/types";

function engineFailurePatch(error: unknown, failureStep: "training" | "export"): Partial<EngineProjectMeta> {
  if (error instanceof EngineApiError) {
    return {
      error: error.message,
      failureStep: error.code === "missing_session" ? "auth" : failureStep,
      errorStatus: error.status,
      errorCode: error.code ?? undefined,
      errorRequestId: error.requestId ?? undefined,
    };
  }
  return { error: error instanceof Error ? error.message : `Engine ${failureStep} failed`, failureStep };
}

export function useEngineWorkflowSync(project: Project | null, onUpdate: (next: Project) => void) {
  const [meta, setMeta] = useState<EngineProjectMeta | null>(() => project ? getEngineMeta(project.id) : null);
  const handledTrainingTerminal = useRef<string | null>(null);
  const handledExportTerminal = useRef<string | null>(null);

  useEffect(() => {
    setMeta(project ? getEngineMeta(project.id) : null);
  }, [project]);

  const saveMeta = useCallback((patch: Partial<EngineProjectMeta>) => {
    if (!project) return;
    patchEngineMeta(project.id, patch);
    setMeta((current) => ({ ...current, ...patch }));
  }, [project]);

  const trainingSocket = useTrainingWebSocket(
    project?.status === "training" || project?.status === "queued" ? meta?.jobId : null,
  );
  const exportSocket = useTrainingWebSocket(meta?.phase === "exporting" ? meta.exportJobId : null);

  const ensureExport = useCallback(async (artifactId?: string | null, force = false) => {
    if (!project || !meta?.trainingId) return;
    try {
      let artifact = artifactId ? await engineGetModelArtifact(artifactId) : null;
      if (!artifact) artifact = (await engineGetModelArtifacts(meta.trainingId)).items[0] ?? null;
      if (!artifact) throw new Error("Training completed without a model artifact.");
      saveMeta({ modelArtifactId: artifact.id });
      if (artifact.ollama_model_tag) {
        saveMeta({ phase: "ready", ollamaModelTag: artifact.ollama_model_tag, error: undefined, failureStep: undefined });
      } else if (!force && (artifact.export_status === "failed" || artifact.export_status === "cancelled")) {
        saveMeta({ phase: "export_failed", error: artifact.export_error_message || `Model export ${artifact.export_status}`, failureStep: "export" });
      } else if (artifact.export_celery_task_id && (artifact.export_status === "pending" || artifact.export_status === "running")) {
        saveMeta({ phase: "exporting", exportJobId: artifact.export_celery_task_id, error: undefined });
      } else {
        const started = await engineExportModel(artifact.id);
        saveMeta({ phase: "exporting", exportJobId: started.job_id, error: undefined, failureStep: undefined });
      }
    } catch (error) {
      saveMeta({ phase: "export_failed", ...engineFailurePatch(error, "export") });
    }
  }, [meta?.trainingId, project, saveMeta]);

  const finishTraining = useCallback(async (artifactId?: string | null) => {
    if (!project) return;
    saveMeta({ phase: "completed", modelArtifactId: artifactId ?? meta?.modelArtifactId, error: undefined, failureStep: undefined });
    const next = await updateProject(project.id, { status: "completed", progress: 100 });
    onUpdate(next);
    await ensureExport(artifactId);
  }, [ensureExport, meta?.modelArtifactId, onUpdate, project, saveMeta]);

  const failTraining = useCallback(async (failure: EngineJobFailedEvent | string) => {
    if (!project) return;
    const message = typeof failure === "string" ? failure : failure.error;
    saveMeta({ phase: "failed", error: message, failureStep: "training" });
    onUpdate(await updateProject(project.id, { status: "failed" }));
  }, [onUpdate, project, saveMeta]);

  const applyProgress = useCallback(async (progress: EngineTrainingProgressEvent) => {
    if (!project) return;
    const pct = progress.steps_total > 0 ? Math.round((progress.step / progress.steps_total) * 100) : project.progress;
    if (pct === project.progress) return;
    onUpdate(await updateProject(project.id, { status: "training", progress: Math.min(99, pct) }));
  }, [onUpdate, project]);

  useEffect(() => {
    if (trainingSocket.completed && handledTrainingTerminal.current !== trainingSocket.completed.job_id) {
      handledTrainingTerminal.current = trainingSocket.completed.job_id;
      void finishTraining(trainingSocket.completed.model_artifact_id);
    } else if (trainingSocket.failed && handledTrainingTerminal.current !== trainingSocket.failed.job_id) {
      handledTrainingTerminal.current = trainingSocket.failed.job_id;
      void failTraining(trainingSocket.failed);
    }
    else if (trainingSocket.latestProgress) void applyProgress(trainingSocket.latestProgress);
  }, [applyProgress, failTraining, finishTraining, trainingSocket.completed, trainingSocket.failed, trainingSocket.latestProgress]);

  useEffect(() => {
    if (!exportSocket.completed || !meta?.modelArtifactId || handledExportTerminal.current === exportSocket.completed.job_id) return;
    handledExportTerminal.current = exportSocket.completed.job_id;
    let active = true;
    void engineGetModelArtifact(meta.modelArtifactId).then((artifact) => {
      if (!active) return;
      if (!artifact.ollama_model_tag) throw new Error("Export completed without an Ollama model tag.");
      saveMeta({ phase: "ready", ollamaModelTag: artifact.ollama_model_tag, error: undefined, failureStep: undefined });
    }).catch((error) => {
      if (active) saveMeta({ phase: "export_failed", ...engineFailurePatch(error, "export") });
    });
    return () => { active = false; };
  }, [exportSocket.completed, meta?.modelArtifactId, saveMeta]);

  useEffect(() => {
    if (exportSocket.failed && handledExportTerminal.current !== exportSocket.failed.job_id) {
      handledExportTerminal.current = exportSocket.failed.job_id;
      saveMeta({ phase: "export_failed", error: exportSocket.failed.error, failureStep: "export" });
    }
  }, [exportSocket.failed, saveMeta]);

  useEffect(() => {
    if (!project || !["queued", "training", "completed"].includes(project.status)) return;
    const needsRecovery = !meta?.engineProjectId || (!meta.trainingId && project.status !== "queued") || (project.status === "queued" && !isEngineWorkflowActive(project.id));
    if (!needsRecovery || isEngineWorkflowActive(project.id)) return;
    let active = true;
    void runEngineWorkflow(workflowInputFromProject(project)).then(async () => {
      if (!active) return;
      setMeta(getEngineMeta(project.id));
      const next = await getProject(project.id);
      if (active && next) onUpdate(next);
    });
    return () => { active = false; };
  }, [meta?.engineProjectId, meta?.trainingId, onUpdate, project]);

  useEffect(() => {
    if (!project || meta?.jobId || project.status !== "queued") return;
    let active = true;
    const poll = async () => {
      const next = await getProject(project.id);
      if (active && next && next.updatedAt !== project.updatedAt) onUpdate(next);
    };
    const interval = setInterval(() => void poll(), 3000);
    void poll();
    return () => { active = false; clearInterval(interval); };
  }, [meta?.jobId, onUpdate, project]);

  useEffect(() => {
    if (!project || !meta?.trainingId || !meta.jobId || !trainingSocket.connectionFailed) return;
    let active = true;
    const poll = async () => {
      try {
        const event = await engineGetJobProgress(meta.jobId!);
        if (!active) return;
        if (event.type === "training_progress") await applyProgress(event);
        else if (event.type === "completed") await finishTraining(event.model_artifact_id);
        else if (event.type === "failed") await failTraining(event);
      } catch (error) {
        if (!active) return;
        if (!(error instanceof EngineApiError) || error.status !== 404) {
          saveMeta(engineFailurePatch(error, "training"));
          return;
        }
        try {
          const training = await engineGetTraining(meta.trainingId!);
          if (!active) return;
          if (training.status === "completed") await finishTraining();
          else if (training.status === "failed" || training.status === "cancelled") await failTraining(training.error_message || `Training ${training.status}`);
        } catch (recoveryError) {
          if (active) saveMeta(engineFailurePatch(recoveryError, "training"));
        }
      }
    };
    const interval = setInterval(() => void poll(), 10000);
    void poll();
    return () => { active = false; clearInterval(interval); };
  }, [applyProgress, failTraining, finishTraining, meta?.jobId, meta?.trainingId, project, saveMeta, trainingSocket.connectionFailed]);

  useEffect(() => {
    if (!meta?.exportJobId || !exportSocket.connectionFailed || !meta.modelArtifactId) return;
    let active = true;
    const poll = async () => {
      try {
        const event = await engineGetJobProgress(meta.exportJobId!);
        if (!active) return;
        if (event.type === "completed") {
          const artifact = await engineGetModelArtifact(meta.modelArtifactId!);
          if (!artifact.ollama_model_tag) throw new Error("Export completed without an Ollama model tag.");
          saveMeta({ phase: "ready", ollamaModelTag: artifact.ollama_model_tag, error: undefined });
        } else if (event.type === "failed") saveMeta({ phase: "export_failed", error: event.error, failureStep: "export" });
      } catch (error) {
        if (!active) return;
        if (error instanceof EngineApiError && error.status === 404) {
          try {
            const artifact = await engineGetModelArtifact(meta.modelArtifactId!);
            if (!active) return;
            if (artifact.ollama_model_tag) {
              saveMeta({ phase: "ready", ollamaModelTag: artifact.ollama_model_tag, error: undefined });
            } else if (artifact.export_status === "failed" || artifact.export_status === "cancelled") {
              saveMeta({ phase: "export_failed", error: artifact.export_error_message || `Model export ${artifact.export_status}`, failureStep: "export" });
            }
          } catch (recoveryError) {
            if (active) saveMeta({ phase: "export_failed", ...engineFailurePatch(recoveryError, "export") });
          }
        } else {
          saveMeta({ phase: "export_failed", ...engineFailurePatch(error, "export") });
        }
      }
    };
    const interval = setInterval(() => void poll(), 10000);
    void poll();
    return () => { active = false; clearInterval(interval); };
  }, [exportSocket.connectionFailed, meta?.exportJobId, meta?.modelArtifactId, saveMeta]);

  useEffect(() => {
    if (project?.status === "completed" && meta?.phase === "completed") void ensureExport(meta.modelArtifactId);
  }, [ensureExport, meta?.modelArtifactId, meta?.phase, project?.status]);

  return {
    retryExport: () => void ensureExport(meta?.modelArtifactId, true),
    latestProgress: trainingSocket.latestProgress,
  };
}
