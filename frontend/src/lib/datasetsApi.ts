import { supabase } from "@/integrations/supabase/client";

export interface UserDataset {
  id: string;
  projectId: string | null;
  name: string;
  description: string;
  rows: number;
  columns: number;
  fileSize: string;
  format: string;
  qualityScore: number;
  createdAt: string;
}

interface DatasetRow {
  id: string;
  project_id: string | null;
  name: string;
  description: string;
  rows: number;
  columns: number;
  file_size: string;
  format: string;
  quality_score: number;
  created_at: string;
}

function toDataset(r: DatasetRow): UserDataset {
  return {
    id: r.id,
    projectId: r.project_id,
    name: r.name,
    description: r.description,
    rows: r.rows,
    columns: r.columns,
    fileSize: r.file_size,
    format: r.format,
    qualityScore: Number(r.quality_score),
    createdAt: r.created_at,
  };
}

export async function listDatasets(): Promise<UserDataset[]> {
  const { data, error } = await supabase.from("datasets").select("*").order("created_at", { ascending: false });
  if (error) throw error;
  return (data as DatasetRow[]).map(toDataset);
}
