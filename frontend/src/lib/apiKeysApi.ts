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

const LOCAL_STORAGE_KEY = "smt_fallback_api_keys";

function getLocalKeys(): ApiKey[] {
  try {
    const raw = localStorage.getItem(LOCAL_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveLocalKeys(keys: ApiKey[]): void {
  try {
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(keys));
  } catch {
    // Ignore storage quota errors
  }
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
  try {
    const { data, error } = await supabase
      .from("api_keys")
      .select("*")
      .order("created_at", { ascending: false });

    if (error) {
      console.warn("Supabase api_keys query failed, falling back to local store:", error.message);
      return getLocalKeys();
    }

    return (data as KeyRow[]).map(toKey);
  } catch (err) {
    console.warn("Supabase api_keys fetch error:", err);
    return getLocalKeys();
  }
}

export interface CreatedApiKeyResult {
  key: ApiKey;
  rawKey: string;
}

export async function createApiKey(name: string): Promise<CreatedApiKeyResult> {
  const rawKey = `smt_live_${crypto.randomUUID().replace(/-/g, "")}`;
  const prefix = "smt_live_";
  const suffix = rawKey.slice(-4);
  const now = new Date().toISOString();

  try {
    const {
      data: { user },
    } = await supabase.auth.getUser();

    if (user) {
      const { data, error } = await supabase
        .from("api_keys")
        .insert({
          user_id: user.id,
          name,
          key_prefix: prefix,
          key_suffix: suffix,
          status: "active",
        })
        .select("*")
        .single();

      if (!error && data) {
        return {
          key: toKey(data as KeyRow),
          rawKey,
        };
      }
    }
  } catch (err) {
    console.warn("Could not insert into Supabase api_keys:", err);
  }

  // Fallback to local storage
  const localItem: ApiKey = {
    id: `key_${crypto.randomUUID().slice(0, 8)}`,
    name,
    keyPrefix: prefix,
    keySuffix: suffix,
    status: "active",
    lastUsedAt: null,
    createdAt: now,
  };

  const current = getLocalKeys();
  saveLocalKeys([localItem, ...current]);

  return {
    key: localItem,
    rawKey,
  };
}

export async function revokeApiKey(id: string): Promise<void> {
  try {
    await supabase.from("api_keys").update({ status: "revoked" }).eq("id", id);
  } catch {
    // Ignore error and proceed to local update
  }

  const current = getLocalKeys();
  const updated = current.map((k) => (k.id === id ? { ...k, status: "revoked" as const } : k));
  saveLocalKeys(updated);
}
