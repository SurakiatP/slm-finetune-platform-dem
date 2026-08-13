// Maps Supabase project IDs → Engine IDs stored in localStorage.
// Engine IDs are not persisted in Supabase to avoid schema migrations.

const STORAGE_KEY = "slm_engine_meta";

export interface EngineProjectMeta {
  engineProjectId?: string;
  phase?:
    | "project_created"
    | "uploading"
    | "generating"
    | "training"
    | "exporting"
    | "ready"
    | "completed"
    | "export_failed"
    | "failed";
  error?: string;
  failureStep?: "auth" | "project" | "upload" | "sdg" | "training" | "export" | "inference";
  errorStatus?: number;
  errorCode?: string;
  errorRequestId?: string;
  seedDatasetId?: string;
  sdgJobId?: string;
  trainDatasetId?: string;
  holdoutDatasetId?: string;
  trainingId?: string;
  jobId?: string;
  modelArtifactId?: string;
  exportJobId?: string;
  ollamaModelTag?: string;
  baseOllamaTag?: string;
}

type StoreMap = Record<string, EngineProjectMeta>;

function load(): StoreMap {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as StoreMap;
  } catch {
    return {};
  }
}

function save(map: StoreMap): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
}

export function getEngineMeta(supabaseProjectId: string): EngineProjectMeta | null {
  return load()[supabaseProjectId] ?? null;
}

export function setEngineMeta(supabaseProjectId: string, meta: EngineProjectMeta): void {
  const map = load();
  map[supabaseProjectId] = meta;
  save(map);
}

export function patchEngineMeta(supabaseProjectId: string, patch: Partial<EngineProjectMeta>): void {
  const map = load();
  map[supabaseProjectId] = { ...map[supabaseProjectId], ...patch };
  save(map);
}

export function clearEngineMeta(supabaseProjectId: string): void {
  const map = load();
  delete map[supabaseProjectId];
  save(map);
}
