import { createClient } from "npm:@supabase/supabase-js@2.105.1";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: Record<string, unknown>, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

function secretKey(): string | null {
  const secretKeys = Deno.env.get("SUPABASE_SECRET_KEYS");
  if (secretKeys) {
    try {
      const keys = JSON.parse(secretKeys) as Record<string, string>;
      return keys.default || Object.values(keys)[0] || null;
    } catch {
      return null;
    }
  }
  return Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || null;
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (request.method !== "POST") return json({ error: "Method not allowed.", code: "method_not_allowed" }, 405);

  const authorization = request.headers.get("Authorization");
  const token = authorization?.startsWith("Bearer ") ? authorization.slice(7).trim() : "";
  if (!token) return json({ error: "Authentication required.", code: "authentication_required" }, 401);

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const adminKey = secretKey();
  if (!supabaseUrl || !adminKey) {
    return json({ error: "Account deletion is not configured.", code: "server_misconfigured" }, 500);
  }

  let confirmationEmail = "";
  try {
    const body = await request.json() as { confirmationEmail?: string };
    confirmationEmail = body.confirmationEmail?.trim().toLowerCase() || "";
  } catch {
    return json({ error: "Invalid request body.", code: "invalid_request" }, 400);
  }

  const admin = createClient(supabaseUrl, adminKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
  const { data: { user }, error: userError } = await admin.auth.getUser(token);
  if (userError || !user) {
    return json({ error: "Invalid or expired authentication token.", code: "invalid_token" }, 401);
  }

  if (!user.email || confirmationEmail !== user.email.toLowerCase()) {
    return json({ error: "Email confirmation does not match.", code: "confirmation_mismatch" }, 400);
  }

  const { error: deletionError } = await admin.auth.admin.deleteUser(user.id, false);
  if (deletionError) {
    console.error("delete-account failed", { userId: user.id, code: deletionError.code });
    return json({ error: "Unable to delete the account.", code: "delete_failed" }, 500);
  }

  return json({ success: true }, 200);
});
