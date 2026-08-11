import { useRef, useEffect } from "react";
import type { TrainingLogEntry } from "@/data/trainingMockData";

const levelStyle: Record<string, string> = {
  info: "text-muted-foreground",
  warning: "text-warning",
  error: "text-destructive",
  success: "text-success",
};

const levelPrefix: Record<string, string> = {
  info: "INFO",
  warning: "WARN",
  error: "ERR ",
  success: " OK ",
};

export function TrainingLog({ logs }: { logs: TrainingLogEntry[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [logs]);

  return (
    <div
      ref={scrollRef}
      className="h-[320px] overflow-y-auto bg-foreground/[0.03] rounded-lg border border-border font-mono text-[11px] p-3 space-y-0.5"
    >
      {logs.map((log, i) => (
        <div key={i} className="flex gap-2 leading-relaxed">
          <span className="text-muted-foreground shrink-0">{log.timestamp}</span>
          <span className={`shrink-0 font-bold ${levelStyle[log.level]}`}>[{levelPrefix[log.level]}]</span>
          <span className={levelStyle[log.level]}>{log.message}</span>
        </div>
      ))}
      <div className="flex items-center gap-1 pt-1">
        <span className="w-2 h-2 rounded-full bg-primary animate-pulse" />
        <span className="text-muted-foreground">Waiting for next event...</span>
      </div>
    </div>
  );
}
