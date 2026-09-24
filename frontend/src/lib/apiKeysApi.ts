import { supabase } from "@/integrations/supabase/client";

export interface ApiKey {
  id: string;
  name: string;
  keyPrefix: string;
  keySuffix: string;
  rawKey: string;
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
  user_id?: string;
}

const STORAGE_KEYS_LIST = "smt_api_keys_local_list";
const STORAGE_KEYS_SECRETS = "smt_api_keys_local_secrets";

function getLocalSecrets(): Record<string, string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS_SECRETS);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveLocalSecret(id: string, secret: string) {
  try {
    const map = getLocalSecrets();
    map[id] = secret;
    localStorage.setItem(STORAGE_KEYS_SECRETS, JSON.stringify(map));
  } catch {
    // Ignore storage quota or disabled storage
  }
}

function getLocalKeys(): ApiKey[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS_LIST);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveLocalKeys(keys: ApiKey[]) {
  try {
    localStorage.setItem(STORAGE_KEYS_LIST, JSON.stringify(keys));
  } catch {
    // Ignore
  }
}

function generateSecureToken(byteLength = 20): string {
  const bytes = new Uint8Array(byteLength);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < byteLength; i++) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, "0");
  }
  return hex;
}

function assembleRawKey(prefix: string, suffix: string, cached?: string): string {
  if (cached) return cached;
  const cleanPrefix = prefix.endsWith("-") ? prefix.slice(0, -1) : prefix;
  return `${cleanPrefix}-${suffix}`;
}

function toKey(r: KeyRow): ApiKey {
  const secrets = getLocalSecrets();
  const rawKey = assembleRawKey(r.key_prefix, r.key_suffix, secrets[r.id]);
  return {
    id: r.id,
    name: r.name,
    keyPrefix: r.key_prefix,
    keySuffix: r.key_suffix,
    rawKey,
    status: (r.status as ApiKey["status"]) || "active",
    lastUsedAt: r.last_used_at,
    createdAt: r.created_at,
  };
}

export async function listApiKeys(): Promise<ApiKey[]> {
  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      return getLocalKeys();
    }
    const { data, error } = await supabase
      .from("api_keys")
      .select("*")
      .order("created_at", { ascending: false });

    if (error) {
      console.warn("Failed to fetch api_keys from Supabase, using local cache", error);
      return getLocalKeys();
    }

    const fetched = (data as KeyRow[]).map(toKey);
    // Merge with any offline created keys if not already in fetched list
    const local = getLocalKeys();
    const fetchedIds = new Set(fetched.map((k) => k.id));
    const merged = [...fetched, ...local.filter((k) => !fetchedIds.has(k.id))];
    saveLocalKeys(merged);
    return merged;
  } catch (err) {
    console.warn("Error querying api_keys, falling back to local storage:", err);
    return getLocalKeys();
  }
}

export async function createApiKey(name: string): Promise<ApiKey> {
  const token = generateSecureToken(20);
  const slug = name.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 8) || "key";
  const prefix = `sk-slm-${slug}`;
  const suffix = token;
  const rawKey = `${prefix}-${suffix}`;

  let createdKey: ApiKey | null = null;

  try {
    const { data: { user } } = await supabase.auth.getUser();
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
        saveLocalSecret(data.id, rawKey);
        createdKey = toKey(data as KeyRow);
      } else if (error) {
        console.warn("Supabase insert error, falling back to local:", error);
      }
    }
  } catch (err) {
    console.warn("Could not insert API key into Supabase, saving locally:", err);
  }

  if (!createdKey) {
    // Fallback to local storage if user not logged in or supabase error
    const localId = `key-${Date.now()}-${token.slice(0, 6)}`;
    createdKey = {
      id: localId,
      name,
      keyPrefix: prefix,
      keySuffix: suffix,
      rawKey,
      status: "active",
      lastUsedAt: null,
      createdAt: new Date().toISOString(),
    };
    saveLocalSecret(localId, rawKey);
    const existing = getLocalKeys();
    saveLocalKeys([createdKey, ...existing]);
  }

  return createdKey;
}

export async function revokeApiKey(id: string): Promise<void> {
  try {
    const { error } = await supabase
      .from("api_keys")
      .update({ status: "revoked" })
      .eq("id", id);

    if (error) {
      console.warn("Supabase revoke error:", error);
    }
  } catch (err) {
    console.warn("Error updating supabase api_keys:", err);
  }

  // Update local storage representation as well
  const local = getLocalKeys();
  const updated = local.map((k) => (k.id === id ? { ...k, status: "revoked" as const } : k));
  saveLocalKeys(updated);
}
