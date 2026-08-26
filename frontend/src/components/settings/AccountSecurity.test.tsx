import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccountSecurity } from "./AccountSecurity";

const mocks = vi.hoisted(() => ({
  changePassword: vi.fn(),
  requestPasswordReauthentication: vi.fn(),
  enrollMfa: vi.fn(),
  verifyMfa: vi.fn(),
  unenrollMfa: vi.fn(),
  deleteAccount: vi.fn(),
  clearLocalAuthSession: vi.fn(),
  clearAppliedTuningRuns: vi.fn(),
  refreshMfa: vi.fn(),
  toast: vi.fn(),
}));

let authState: Record<string, unknown>;

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => authState }));
vi.mock("@/i18n/LanguageContext", () => ({ useLanguage: () => ({ t: (key: string) => key }) }));
vi.mock("@/hooks/use-toast", () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock("@/lib/accountSecurity", () => ({
  changePassword: mocks.changePassword,
  requestPasswordReauthentication: mocks.requestPasswordReauthentication,
  enrollMfa: mocks.enrollMfa,
  verifyMfa: mocks.verifyMfa,
  unenrollMfa: mocks.unenrollMfa,
  deleteAccount: mocks.deleteAccount,
  clearLocalAuthSession: mocks.clearLocalAuthSession,
}));
vi.mock("@/lib/tuningGenerator", () => ({ clearAppliedTuningRuns: mocks.clearAppliedTuningRuns }));

function renderSecurity() {
  return render(
    <MemoryRouter initialEntries={["/settings"]}>
      <Routes>
        <Route path="/settings" element={<AccountSecurity />} />
        <Route path="/login" element={<div>login destination</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function openPasswordDialog() {
  fireEvent.click(screen.getByRole("button", { name: "security.changePassword" }));
}

function fillPasswords(password: string, confirmation: string) {
  fireEvent.change(screen.getByLabelText("security.newPassword"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("security.confirmPassword"), { target: { value: confirmation } });
}

describe("AccountSecurity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState = {
      user: { email: "user@example.com" },
      mfaFactors: [],
      mfaLoading: false,
      refreshMfa: mocks.refreshMfa,
    };
    mocks.requestPasswordReauthentication.mockResolvedValue(undefined);
    mocks.clearLocalAuthSession.mockResolvedValue(undefined);
  });

  it("validates password confirmation before calling Supabase", async () => {
    renderSecurity();
    openPasswordDialog();
    fillPasswords("new-password", "different-password");

    fireEvent.click(screen.getByRole("button", { name: "security.updatePassword" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("security.passwordMismatch");
    expect(mocks.changePassword).not.toHaveBeenCalled();
  });

  it("completes the nonce flow when Supabase requires reauthentication", async () => {
    mocks.changePassword
      .mockRejectedValueOnce({ code: "reauthentication_needed", message: "reauthentication needed" })
      .mockResolvedValueOnce(undefined);
    renderSecurity();
    openPasswordDialog();
    fillPasswords("new-password", "new-password");

    fireEvent.click(screen.getByRole("button", { name: "security.updatePassword" }));
    await waitFor(() => expect(mocks.requestPasswordReauthentication).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("security.verificationCode"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "security.updatePassword" }));

    await waitFor(() => expect(mocks.changePassword).toHaveBeenLastCalledWith("new-password", "123456"));
    expect(mocks.toast).toHaveBeenCalledWith({ title: "security.passwordUpdated" });
  });

  it("removes an unverified factor when enrollment is cancelled", async () => {
    mocks.enrollMfa.mockResolvedValue({ factorId: "factor-1", qrCode: "data:image/svg+xml,qr", secret: "SECRET" });
    mocks.unenrollMfa.mockResolvedValue(undefined);
    renderSecurity();

    fireEvent.click(screen.getByRole("button", { name: "security.enableMfa" }));
    expect(await screen.findByAltText("security.mfaQrAlt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "security.cancel" }));

    await waitFor(() => expect(mocks.unenrollMfa).toHaveBeenCalledWith("factor-1"));
  });

  it("requires the exact email before permanently deleting the account", async () => {
    mocks.deleteAccount.mockResolvedValue(undefined);
    renderSecurity();
    fireEvent.click(screen.getByRole("button", { name: "security.delete" }));

    const deleteButton = screen.getByRole("button", { name: "security.deletePermanently" });
    expect(deleteButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText("security.emailConfirmation"), { target: { value: "user@example.com" } });
    expect(deleteButton).toBeEnabled();
    fireEvent.click(deleteButton);

    await waitFor(() => expect(mocks.deleteAccount).toHaveBeenCalledWith("user@example.com"));
    expect(mocks.clearAppliedTuningRuns).toHaveBeenCalled();
    expect(await screen.findByText("login destination")).toBeInTheDocument();
  });
});
