import { supabase } from "@/integrations/supabase/client";

export interface CallEvent {
  id: string;
  endpoint: string;
  statusCode: number;
  latencyMs: number;
  createdAt: string;
}

const RANGE_DAYS: Record<string, number> = { "24h": 1, "7d": 7, "30d": 30, "90d": 90 };

export async function listCallEvents(range: string): Promise<CallEvent[]> {
  const days = RANGE_DAYS[range] ?? 7;
  const since = new Date(Date.now() - days * 86400_000).toISOString();
  const { data, error } = await supabase
    .from("api_call_events")
    .select("*")
    .gte("created_at", since)
    .order("created_at", { ascending: true })
    .limit(5000);
  if (error) throw error;
  return (data ?? []).map((r: { id: string; endpoint: string; status_code: number; latency_ms: number; created_at: string }) => ({
    id: r.id,
    endpoint: r.endpoint,
    statusCode: r.status_code,
    latencyMs: r.latency_ms,
    createdAt: r.created_at,
  }));
}

export interface AnalyticsSummary {
  totalCalls: number;
  avgLatency: number;
  errorRate: number;
  uptime: number;
}

export function summarize(events: CallEvent[]): AnalyticsSummary {
  if (events.length === 0) return { totalCalls: 0, avgLatency: 0, errorRate: 0, uptime: 100 };
  const errors = events.filter((e) => e.statusCode >= 400).length;
  const totalLatency = events.reduce((s, e) => s + e.latencyMs, 0);
  return {
    totalCalls: events.length,
    avgLatency: Math.round(totalLatency / events.length),
    errorRate: Number(((errors / events.length) * 100).toFixed(2)),
    uptime: Number((100 - (errors / events.length) * 100 * 0.1).toFixed(2)),
  };
}

export interface BucketPoint { time: string; calls: number; errors: number; }
export interface LatencyPoint { time: string; p50: number; p95: number; p99: number; }

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  return sorted[base + 1] !== undefined ? Math.round(sorted[base] + rest * (sorted[base + 1] - sorted[base])) : sorted[base];
}

export function bucketByTime(events: CallEvent[], range: string): { calls: BucketPoint[]; latency: LatencyPoint[] } {
  const days = RANGE_DAYS[range] ?? 7;
  const buckets = days <= 1 ? 24 : days; // hours for 24h else days
  const now = Date.now();
  const start = now - days * 86400_000;
  const span = (now - start) / buckets;
  const callsArr: BucketPoint[] = [];
  const latArr: LatencyPoint[] = [];
  for (let i = 0; i < buckets; i++) {
    const bStart = start + i * span;
    const bEnd = bStart + span;
    const bucket = events.filter((e) => {
      const t = new Date(e.createdAt).getTime();
      return t >= bStart && t < bEnd;
    });
    const label = days <= 1
      ? new Date(bStart).getHours().toString().padStart(2, "0") + ":00"
      : new Date(bStart).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    callsArr.push({
      time: label,
      calls: bucket.length,
      errors: bucket.filter((e) => e.statusCode >= 400).length,
    });
    const lats = bucket.map((e) => e.latencyMs).sort((a, b) => a - b);
    latArr.push({
      time: label,
      p50: quantile(lats, 0.5),
      p95: quantile(lats, 0.95),
      p99: quantile(lats, 0.99),
    });
  }
  return { calls: callsArr, latency: latArr };
}

export interface EndpointStat {
  endpoint: string;
  calls: number;
  avgLatency: number;
  errorRate: number;
}

export function endpointStats(events: CallEvent[]): EndpointStat[] {
  const map = new Map<string, CallEvent[]>();
  events.forEach((e) => {
    const arr = map.get(e.endpoint) ?? [];
    arr.push(e);
    map.set(e.endpoint, arr);
  });
  return Array.from(map.entries()).map(([endpoint, evts]) => {
    const errors = evts.filter((e) => e.statusCode >= 400).length;
    const avg = Math.round(evts.reduce((s, e) => s + e.latencyMs, 0) / evts.length);
    return {
      endpoint,
      calls: evts.length,
      avgLatency: avg,
      errorRate: Number(((errors / evts.length) * 100).toFixed(2)),
    };
  }).sort((a, b) => b.calls - a.calls);
}
