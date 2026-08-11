import { supabase } from "@/integrations/supabase/client";

export interface DeployedEndpoint {
  id: string;
  modelId: string | null;
  modelName: string;
  projectName: string;
  endpointUrl: string;
  status: "active" | "inactive" | "scaling";
  requestsPerMin: number;
  avgLatencyMs: number;
  errorRate: number;
  uptime: number;
  rateLimitPerMin: number;
  burstLimit: number;
}

interface EpRow {
  id: string;
  model_id: string | null;
  model_name: string;
  project_name: string;
  endpoint_url: string;
  status: string;
  requests_per_min: number;
  avg_latency_ms: number;
  error_rate: number;
  uptime: number;
  rate_limit_per_min: number;
  burst_limit: number;
}

function toEp(r: EpRow): DeployedEndpoint {
  return {
    id: r.id,
    modelId: r.model_id,
    modelName: r.model_name,
    projectName: r.project_name,
    endpointUrl: r.endpoint_url,
    status: r.status as DeployedEndpoint["status"],
    requestsPerMin: r.requests_per_min,
    avgLatencyMs: r.avg_latency_ms,
    errorRate: Number(r.error_rate),
    uptime: Number(r.uptime),
    rateLimitPerMin: r.rate_limit_per_min,
    burstLimit: r.burst_limit,
  };
}

export async function listEndpoints(): Promise<DeployedEndpoint[]> {
  const { data, error } = await supabase.from("deployed_endpoints").select("*").order("created_at", { ascending: false });
  if (error) throw error;
  return (data as EpRow[]).map(toEp);
}

export async function setEndpointStatus(id: string, status: "active" | "inactive"): Promise<void> {
  const { error } = await supabase.from("deployed_endpoints").update({ status }).eq("id", id);
  if (error) throw error;
}
