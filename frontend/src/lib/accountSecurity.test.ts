import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  changePassword,
  deleteAccount,
  enrollMfa,
  getMfaAssuranceLevel,
  listMfaFactors,
  verifyMfa,
} from "./accountSecurity";

const mocks = vi.hoisted(() => ({
  updateUser: vi.fn(),
  listFactors: vi.fn(),
  enroll: vi.fn(),
  challenge: vi.fn(),
  verify: vi.fn(),
  unenroll: vi.fn(),
  getAuthenticatorAssuranceLevel: vi.fn(),
  invoke: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("@/integrations/supabase/client", () => ({
  supabase: {
    auth: {
      updateUser: mocks.updateUser,
      signOut: mocks.signOut,
      mfa: {
        listFactors: mocks.listFactors,
        enroll: mocks.enroll,
        challenge: mocks.challenge,
        verify: mocks.verify,
        unenroll: mocks.unenroll,
        getAuthenticatorAssuranceLevel: mocks.getAuthenticatorAssuranceLevel,
      },
    },
    functions: { invoke: mocks.invoke },
  },
}));

describe("account security service", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("updates a password with an optional reauthentication nonce", async () => {
    mocks.updateUser.mockResolvedValue({ error: null });

    await changePassword("new-password", "123456");

    expect(mocks.updateUser).toHaveBeenCalledWith({ password: "new-password", nonce: "123456" });
  });

  it("returns only verified TOTP factors", async () => {
    mocks.listFactors.mockResolvedValue({
      data: {
        all: [],
        totp: [{ id: "factor-1", friendly_name: "Phone", created_at: "2026-08-13T00:00:00Z" }],
      },
      error: null,
    });

    await expect(listMfaFactors()).resolves.toEqual([{
      id: "factor-1",
      friendlyName: "Phone",
      createdAt: "2026-08-13T00:00:00Z",
    }]);
  });

  it("cleans stale enrollment before creating a TOTP factor", async () => {
    mocks.listFactors.mockResolvedValue({
      data: {
        all: [{ id: "stale", factor_type: "totp", status: "unverified" }],
        totp: [],
      },
      error: null,
    });
    mocks.unenroll.mockResolvedValue({ error: null });
    mocks.enroll.mockResolvedValue({
      data: { id: "new-factor", type: "totp", totp: { qr_code: "data:image/svg+xml,qr", secret: "SECRET" } },
      error: null,
    });

    await expect(enrollMfa()).resolves.toEqual({
      factorId: "new-factor",
      qrCode: "data:image/svg+xml,qr",
      secret: "SECRET",
    });
    expect(mocks.unenroll).toHaveBeenCalledWith({ factorId: "stale" });
  });

  it("creates and verifies an MFA challenge", async () => {
    mocks.challenge.mockResolvedValue({ data: { id: "challenge-1" }, error: null });
    mocks.verify.mockResolvedValue({ error: null });

    await verifyMfa("factor-1", "123456");

    expect(mocks.verify).toHaveBeenCalledWith({
      factorId: "factor-1",
      challengeId: "challenge-1",
      code: "123456",
    });
  });

  it("reports the current and next assurance level", async () => {
    mocks.getAuthenticatorAssuranceLevel.mockResolvedValue({
      data: { currentLevel: "aal1", nextLevel: "aal2" },
      error: null,
    });

    await expect(getMfaAssuranceLevel()).resolves.toEqual({ currentLevel: "aal1", nextLevel: "aal2" });
  });

  it("requires a successful Edge Function response before reporting deletion", async () => {
    mocks.invoke.mockResolvedValue({ data: { success: true }, error: null });
    await expect(deleteAccount("user@example.com")).resolves.toBeUndefined();
    expect(mocks.invoke).toHaveBeenCalledWith("delete-account", {
      method: "POST",
      body: { confirmationEmail: "user@example.com" },
    });

    mocks.invoke.mockResolvedValue({ data: { error: "Not deployed", code: "not_found" }, error: null });
    await expect(deleteAccount("user@example.com")).rejects.toMatchObject({ message: "Not deployed", code: "not_found" });

    mocks.invoke.mockResolvedValue({
      data: null,
      error: {
        context: new Response(JSON.stringify({ error: "Email mismatch", code: "confirmation_mismatch" }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
      },
    });
    await expect(deleteAccount("user@example.com")).rejects.toMatchObject({
      message: "Email mismatch",
      code: "confirmation_mismatch",
    });
  });
});
