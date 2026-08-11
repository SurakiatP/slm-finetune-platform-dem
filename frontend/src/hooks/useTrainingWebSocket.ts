import { useEffect, useRef, useState } from "react";

export interface TrainingProgressEvent {
  type: "training_progress";
  epoch: number;
  epochs_total: number;
  step: number;
  steps_total: number;
  train_loss: number;
  eval_loss: number;
  learning_rate: number;
  samples_per_second: number;
  gpu_memory_mb: number;
}

export interface TrainingCompletedEvent {
  type: "completed";
  result: {
    training_id: string;
    model_artifact_id: string;
    lora_adapter_uri: string;
    final_train_loss: number;
    final_eval_loss: number;
    steps_completed: number;
    train_runtime_seconds: number;
  };
  mlflow_run_id: string;
  model_artifact_id: string;
}

export interface TrainingFailedEvent {
  type: "failed";
  error: string;
}

export type TrainingWsEvent = TrainingProgressEvent | TrainingCompletedEvent | TrainingFailedEvent;

export interface UseTrainingWebSocketResult {
  latestProgress: TrainingProgressEvent | null;
  completed: TrainingCompletedEvent | null;
  failed: TrainingFailedEvent | null;
  connected: boolean;
}

// Builds the WS URL from VITE_ENGINE_HOST (works in prod without a WS proxy)
function buildWsUrl(jobId: string): string {
  const engineHost = (import.meta.env.VITE_ENGINE_HOST as string | undefined) ?? window.location.origin;
  const wsProtocol = engineHost.startsWith("https") ? "wss:" : "ws:";
  const hostPart = engineHost.replace(/^https?:\/\//, "").replace(/\/$/, "");
  return `${wsProtocol}//${hostPart}/ws/jobs/${jobId}`;
}

export function useTrainingWebSocket(jobId: string | null | undefined): UseTrainingWebSocketResult {
  const [latestProgress, setLatestProgress] = useState<TrainingProgressEvent | null>(null);
  const [completed, setCompleted] = useState<TrainingCompletedEvent | null>(null);
  const [failed, setFailed] = useState<TrainingFailedEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!jobId) return;

    let destroyed = false;
    let attempts = 0;
    const MAX_ATTEMPTS = 8;

    function connect() {
      if (destroyed || attempts >= MAX_ATTEMPTS) return;
      attempts++;

      const ws = new WebSocket(buildWsUrl(jobId));
      wsRef.current = ws;

      ws.onopen = () => {
        if (!destroyed) setConnected(true);
      };

      ws.onmessage = (e) => {
        if (destroyed) return;
        try {
          const event = JSON.parse(e.data as string) as TrainingWsEvent;
          if (event.type === "training_progress") {
            setLatestProgress(event);
          } else if (event.type === "completed") {
            setCompleted(event as TrainingCompletedEvent);
            setConnected(false);
            ws.close();
          } else if (event.type === "failed") {
            setFailed(event as TrainingFailedEvent);
            setConnected(false);
            ws.close();
          }
        } catch {
          // ignore malformed frames
        }
      };

      ws.onerror = () => {
        if (!destroyed) setConnected(false);
      };

      ws.onclose = () => {
        if (destroyed) return;
        setConnected(false);
        // Retry with exponential back-off if not yet finished
        if (attempts < MAX_ATTEMPTS) {
          const delay = Math.min(1000 * 2 ** attempts, 30000);
          reconnectTimer.current = setTimeout(connect, delay);
        }
      };
    }

    connect();

    return () => {
      destroyed = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [jobId]);

  return { latestProgress, completed, failed, connected };
}
