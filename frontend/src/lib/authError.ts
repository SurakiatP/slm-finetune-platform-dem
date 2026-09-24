interface AuthErrorLike {
  message?: string;
  status?: number;
  code?: string;
}

export function describeAuthError(error: unknown): string {
  const authError = error as AuthErrorLike;
  const message = authError?.message || "Authentication failed";
  const normalized = message.toLowerCase();
  if (
    authError?.status === 0
    || authError?.code === "fetch_error"
    || normalized.includes("failed to fetch")
    || normalized.includes("network")
    || normalized.includes("enotfound")
  ) {
    return "Authentication service is unreachable. Check the Supabase project URL and project availability.";
  }
  if (normalized.includes("invalid api key")) {
    return "This deployed frontend has an invalid or outdated Supabase API key. Rebuild and redeploy it with the current VITE_SUPABASE_URL, VITE_SUPABASE_PROJECT_ID, and VITE_SUPABASE_PUBLISHABLE_KEY values.";
  }
  return message;
}
