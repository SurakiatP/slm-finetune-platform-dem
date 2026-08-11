export interface ApiCallDataPoint {
  time: string;
  calls: number;
  errors: number;
}

export interface LatencyDataPoint {
  time: string;
  p50: number;
  p95: number;
  p99: number;
}

export interface EndpointStats {
  endpoint: string;
  calls: number;
  avgLatency: number;
  errorRate: number;
}

function generateTimeLabels(range: string): string[] {
  const now = new Date();
  const labels: string[] = [];
  
  if (range === "24h") {
    for (let i = 23; i >= 0; i--) {
      const d = new Date(now.getTime() - i * 3600000);
      labels.push(`${d.getHours().toString().padStart(2, "0")}:00`);
    }
  } else if (range === "7d") {
    for (let i = 6; i >= 0; i--) {
      const d = new Date(now.getTime() - i * 86400000);
      labels.push(d.toLocaleDateString("en-US", { weekday: "short" }));
    }
  } else if (range === "30d") {
    for (let i = 29; i >= 0; i--) {
      const d = new Date(now.getTime() - i * 86400000);
      labels.push(`${d.getMonth() + 1}/${d.getDate()}`);
    }
  } else {
    for (let i = 11; i >= 0; i--) {
      const d = new Date(now.getTime() - i * 7 * 86400000);
      labels.push(`W${Math.ceil(d.getDate() / 7)} ${d.toLocaleDateString("en-US", { month: "short" })}`);
    }
  }
  return labels;
}

export function generateApiCallData(range: string): ApiCallDataPoint[] {
  const labels = generateTimeLabels(range);
  const base = range === "24h" ? 800 : range === "7d" ? 5000 : range === "30d" ? 4500 : 30000;
  return labels.map((time) => ({
    time,
    calls: Math.floor(base + Math.random() * base * 0.6),
    errors: Math.floor(Math.random() * base * 0.03),
  }));
}

export function generateLatencyData(range: string): LatencyDataPoint[] {
  const labels = generateTimeLabels(range);
  return labels.map((time) => ({
    time,
    p50: +(2 + Math.random() * 3).toFixed(1),
    p95: +(8 + Math.random() * 7).toFixed(1),
    p99: +(20 + Math.random() * 15).toFixed(1),
  }));
}

export function generateEndpointStats(): EndpointStats[] {
  return [
    { endpoint: "/v1/chat/completions", calls: 45200, avgLatency: 4.2, errorRate: 0.8 },
    { endpoint: "/v1/embeddings", calls: 28100, avgLatency: 1.8, errorRate: 0.3 },
    { endpoint: "/v1/models", calls: 12400, avgLatency: 0.5, errorRate: 0.1 },
    { endpoint: "/v1/fine-tuning/jobs", calls: 3200, avgLatency: 12.3, errorRate: 2.1 },
    { endpoint: "/v1/files", calls: 1800, avgLatency: 8.7, errorRate: 1.5 },
  ];
}

export function getAnalyticsSummary(range: string) {
  const multiplier = range === "24h" ? 1 : range === "7d" ? 7 : range === "30d" ? 30 : 90;
  return {
    totalCalls: Math.floor(18500 * multiplier * (0.8 + Math.random() * 0.4)),
    avgLatency: +(3.2 + Math.random() * 1.5).toFixed(1),
    errorRate: +(0.5 + Math.random() * 1.2).toFixed(2),
    uptime: +(99.5 + Math.random() * 0.49).toFixed(2),
  };
}
