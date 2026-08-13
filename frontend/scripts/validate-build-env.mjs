import { loadEnv } from "vite";

const env = { ...loadEnv("production", process.cwd(), ""), ...process.env };
const required = [
  "VITE_SUPABASE_PROJECT_ID",
  "VITE_SUPABASE_PUBLISHABLE_KEY",
  "VITE_SUPABASE_URL",
];

const missing = required.filter((name) => !env[name]?.trim());
if (missing.length > 0) {
  throw new Error(`Missing required frontend build variables: ${missing.join(", ")}`);
}

const projectRef = env.VITE_SUPABASE_PROJECT_ID.trim();
const publishableKey = env.VITE_SUPABASE_PUBLISHABLE_KEY.trim();
const supabaseUrl = new URL(env.VITE_SUPABASE_URL.trim());

if (!/^[a-z0-9]{20}$/.test(projectRef)) {
  throw new Error("VITE_SUPABASE_PROJECT_ID must be a 20-character Supabase project ref");
}

if (supabaseUrl.protocol !== "https:" || supabaseUrl.hostname !== `${projectRef}.supabase.co`) {
  throw new Error("VITE_SUPABASE_URL must be https://<VITE_SUPABASE_PROJECT_ID>.supabase.co");
}

if (projectRef.startsWith("replace-") || publishableKey.startsWith("replace-")) {
  throw new Error("Replace the example Supabase project ref and publishable key before building");
}

if (!(publishableKey.startsWith("sb_publishable_") || publishableKey.startsWith("eyJ"))) {
  throw new Error("VITE_SUPABASE_PUBLISHABLE_KEY must be a Supabase publishable key or legacy anon JWT");
}

console.log(`Frontend auth target validated: ${supabaseUrl.hostname}`);
