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
  return message;
}
