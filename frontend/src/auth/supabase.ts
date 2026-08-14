import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js'

/**
 * Supabase auth wiring for the Engine API's `AUTH_REQUIRED=true` mode.
 *
 * Both env vars empty (the default) disables auth entirely: no login gate,
 * no Authorization headers, no WS subprotocol — exactly the pre-auth
 * behaviour, which is what a local dev backend (`AUTH_REQUIRED=false`)
 * expects. Set both to target an authenticated deployment.
 *
 * The publishable (anon) key is public by design — it ships in the browser
 * bundle either way; row access is enforced server-side. Never put the
 * `service_role` key or the JWT secret here.
 */
const SUPABASE_URL: string = import.meta.env.VITE_SUPABASE_URL ?? ''
const SUPABASE_KEY: string = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ?? ''

export const authEnabled: boolean = SUPABASE_URL !== '' && SUPABASE_KEY !== ''

export const supabase: SupabaseClient | null = authEnabled
  ? createClient(SUPABASE_URL, SUPABASE_KEY, {
      auth: { persistSession: true, autoRefreshToken: true },
    })
  : null

export type { Session }

/**
 * Current access token, or null when signed out / auth disabled.
 *
 * `getSession()` transparently refreshes an expired token, so callers get
 * either a fresh credential or nothing — never a stale one. Sending no
 * header beats sending a stale header: an absent credential is a 401 the
 * caller can react to; a stale one sent anyway is a 401 that looks like a
 * backend bug (see docs/patches/smart-model-tune-auth.md §1).
 */
export async function getAccessToken(): Promise<string | null> {
  if (!supabase) return null
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}
