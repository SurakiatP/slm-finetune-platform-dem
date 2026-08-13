import { useEffect, useRef, useState } from "react";
import { supabase } from "@/integrations/supabase/client";
import type {
  EngineJobCompletedEvent as TrainingCompletedEvent,
  EngineJobFailedEvent as TrainingFailedEvent,
  EngineJobProgress,
  EngineTrainingProgressEvent as TrainingProgressEvent,
} from "@/lib/engineApi";

export type { TrainingCompletedEvent, TrainingFailedEvent, TrainingProgressEvent };

export interface UseTrainingWebSocketResult {
  latestProgress: TrainingProgressEvent | null;
  completed: TrainingCompletedEvent | null;
  failed: TrainingFailedEvent | null;
  connected: boolean;
  connectionFailed: boolean;
  connectionError: string | null;
}

// Builds the WS URL from VITE_ENGINE_HOST (works in prod without a WS proxy)
export function buildWsUrl(
  jobId: string,
  configuredHost = ((import.meta.env.VITE_ENGINE_HOST as string | undefined) ?? "").trim(),
  browserOrigin = window.location.origin,
): string {
  const url = new URL(configuredHost || browserOrigin, browserOrigin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `/ws/jobs/${encodeURIComponent(jobId)}`;
  url.search = "";
  url.hash = "";
  return url.toString();
}

export function useTrainingWebSocket(jobId: string | null | undefined): UseTrainingWebSocketResult {
  const [latestProgress, setLatestProgress] = useState<TrainingProgressEvent | null>(null);
  const [completed, setCompleted] = useState<TrainingCompletedEvent | null>(null);
  const [failed, setFailed] = useState<TrainingFailedEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const [connectionFailed, setConnectionFailed] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!jobId) {
      setConnected(false);
      setConnectionFailed(false);
      setConnectionError(null);
      return;
    }

    let destroyed = false;
    let terminal = false;
    let reconnectFailures = 0;
    let totalAttempts = 0;
    const MAX_RECONNECT_FAILURES = 3;
    const MAX_TOTAL_ATTEMPTS = 8;

    async function connect() {
      if (destroyed || reconnectFailures >= MAX_RECONNECT_FAILURES || totalAttempts >= MAX_TOTAL_ATTEMPTS) return;
      totalAttempts++;

      const { data: { session } } = await supabase.auth.getSession();
      if (destroyed) return;

      const token = session?.access_token;
      if (!token) {
        setConnectionError("Please sign in before connecting to training progress.");
        setConnectionFailed(true);
        return;
      }

      const ws = new WebSocket(buildWsUrl(jobId), ["bearer", token]);
      wsRef.current = ws;

      ws.onopen = () => {
        if (destroyed) return;
        setConnectionError(null);
        setConnectionFailed(false);
        setConnected(true);
      };

      ws.onmessage = (e) => {
        if (destroyed) return;
        try {
          const event = JSON.parse(e.data as string) as EngineJobProgress;
          reconnectFailures = 0;
          if (event.type === "training_progress") {
            setLatestProgress(event as TrainingProgressEvent);
          } else if (event.type === "completed") {
            terminal = true;
            setCompleted(event as TrainingCompletedEvent);
            setConnected(false);
            ws.close();
          } else if (event.type === "failed") {
            terminal = true;
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
        if (terminal) return;

        reconnectFailures++;
        if (reconnectFailures >= MAX_RECONNECT_FAILURES || totalAttempts >= MAX_TOTAL_ATTEMPTS) {
          setConnectionError("WebSocket reconnect attempts were exhausted.");
          setConnectionFailed(true);
          return;
        }

        const delay = Math.min(1000 * 2 ** reconnectFailures, 30000);
        reconnectTimer.current = setTimeout(() => void connect(), delay);
      };
    }

    setLatestProgress(null);
    setCompleted(null);
    setFailed(null);
    setConnectionFailed(false);
    setConnectionError(null);
    void connect();

    return () => {
      destroyed = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [jobId]);

  return { latestProgress, completed, failed, connected, connectionFailed, connectionError };
}
