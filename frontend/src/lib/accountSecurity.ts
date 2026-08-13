import { supabase } from "@/integrations/supabase/client";

export interface MfaFactor {
  id: string;
  friendlyName: string | null;
  createdAt: string;
}

export interface MfaEnrollment {
  factorId: string;
  qrCode: string;
  secret: string;
}

export interface MfaAssuranceLevel {
  currentLevel: string | null;
  nextLevel: string | null;
}

function throwIfError(error: unknown): void {
  if (error) throw error;
}

export async function changePassword(password: string, nonce?: string): Promise<void> {
  const { error } = await supabase.auth.updateUser({
    password,
    ...(nonce ? { nonce } : {}),
  });
  throwIfError(error);
}

export async function requestPasswordReauthentication(): Promise<void> {
  const { error } = await supabase.auth.reauthenticate();
  throwIfError(error);
}

export async function listMfaFactors(): Promise<MfaFactor[]> {
  const { data, error } = await supabase.auth.mfa.listFactors();
  throwIfError(error);
  return data.totp.map((factor) => ({
    id: factor.id,
    friendlyName: factor.friendly_name ?? null,
    createdAt: factor.created_at,
  }));
}

export async function removeUnverifiedTotpFactors(): Promise<void> {
  const { data, error } = await supabase.auth.mfa.listFactors();
  throwIfError(error);
  const staleFactors = data.all.filter(
    (factor) => factor.factor_type === "totp" && factor.status === "unverified",
  );
  for (const factor of staleFactors) {
    const { error: unenrollError } = await supabase.auth.mfa.unenroll({ factorId: factor.id });
    throwIfError(unenrollError);
  }
}

export async function enrollMfa(): Promise<MfaEnrollment> {
  await removeUnverifiedTotpFactors();
  const { data, error } = await supabase.auth.mfa.enroll({
    factorType: "totp",
    friendlyName: "TuneLab Authenticator",
  });
  throwIfError(error);
  if (data.type !== "totp") throw new Error("Supabase returned an unsupported MFA factor type.");
  return {
    factorId: data.id,
    qrCode: data.totp.qr_code,
    secret: data.totp.secret,
  };
}

export async function verifyMfa(factorId: string, code: string): Promise<void> {
  const { data: challenge, error: challengeError } = await supabase.auth.mfa.challenge({ factorId });
  throwIfError(challengeError);
  const { error } = await supabase.auth.mfa.verify({
    factorId,
    challengeId: challenge.id,
    code,
  });
  throwIfError(error);
}

export async function unenrollMfa(factorId: string): Promise<void> {
  const { error } = await supabase.auth.mfa.unenroll({ factorId });
  throwIfError(error);
}

export async function getMfaAssuranceLevel(): Promise<MfaAssuranceLevel> {
  const { data, error } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
  throwIfError(error);
  return {
    currentLevel: data.currentLevel,
    nextLevel: data.nextLevel,
  };
}

export async function deleteAccount(confirmationEmail: string): Promise<void> {
  const { data, error } = await supabase.functions.invoke("delete-account", {
    method: "POST",
    body: { confirmationEmail },
  });
  if (error) {
    const context = (error as { context?: Response }).context;
    if (context) {
      const body = await context.clone().json().catch(() => null) as { error?: string; code?: string } | null;
      if (body?.error) {
        const failure = new Error(body.error) as Error & { code?: string };
        failure.code = body.code;
        throw failure;
      }
    }
    throw error;
  }
  if (!data?.success) {
    const failure = new Error(data?.error || "Account deletion failed.") as Error & { code?: string };
    failure.code = data?.code;
    throw failure;
  }
}

export async function clearLocalAuthSession(): Promise<void> {
  const { error } = await supabase.auth.signOut({ scope: "local" });
  throwIfError(error);
}
