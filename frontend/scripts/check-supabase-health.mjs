import { loadEnv } from "vite";

const env = { ...loadEnv("production", process.cwd(), ""), ...process.env };
const baseUrl = env.VITE_SUPABASE_URL.trim().replace(/\/$/, "");
const publishableKey = env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim();
const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), 10_000);

try {
  const response = await fetch(`${baseUrl}/auth/v1/health`, {
    signal: controller.signal,
    headers: publishableKey ? { apikey: publishableKey } : undefined,
  });
  if (!response.ok) {
    throw new Error(`Supabase Auth health returned HTTP ${response.status}`);
  }
  console.log(`Supabase Auth is reachable: ${new URL(baseUrl).hostname}`);
} finally {
  clearTimeout(timeout);
}
