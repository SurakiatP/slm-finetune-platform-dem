import { supabase } from "@/integrations/supabase/client";

export interface ApiKey {
  id: string;
  name: string;
  keyPrefix: string;
  keySuffix: string;
  status: "active" | "revoked";
  lastUsedAt: string | null;
  createdAt: string;
}

interface KeyRow {
  id: string;
  name: string;
  key_prefix: string;
  key_suffix: string;
  status: string;
  last_used_at: string | null;
  created_at: string;
}

function toKey(r: KeyRow): ApiKey {
  return {
    id: r.id,
    name: r.name,
    keyPrefix: r.key_prefix,
    keySuffix: r.key_suffix,
    status: r.status as ApiKey["status"],
    lastUsedAt: r.last_used_at,
    createdAt: r.created_at,
  };
}

export async function listApiKeys(): Promise<ApiKey[]> {
  const { data, error } = await supabase.from("api_keys").select("*").order("created_at", { ascending: false });
  if (error) throw error;
  return (data as KeyRow[]).map(toKey);
}

export async function createApiKey(name: string): Promise<ApiKey> {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error("Not authenticated");
  const prefix = `sk-slm-${name.toLowerCase().slice(0, 4)}`;
  const suffix = Math.random().toString(36).slice(2, 6);
  const { data, error } = await supabase.from("api_keys")
    .insert({ user_id: user.id, name, key_prefix: prefix, key_suffix: suffix, status: "active" })
    .select("*").single();
  if (error) throw error;
  return toKey(data as KeyRow);
}

export async function revokeApiKey(id: string): Promise<void> {
  const { error } = await supabase.from("api_keys").update({ status: "revoked" }).eq("id", id);
  if (error) throw error;
}
