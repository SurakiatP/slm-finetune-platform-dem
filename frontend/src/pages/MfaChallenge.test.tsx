import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MfaChallenge from "./MfaChallenge";

const mocks = vi.hoisted(() => ({ verifyMfa: vi.fn(), refreshMfa: vi.fn() }));
let authState: Record<string, unknown>;

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => authState }));
vi.mock("@/i18n/LanguageContext", () => ({ useLanguage: () => ({ t: (key: string) => key }) }));
vi.mock("@/lib/accountSecurity", () => ({ verifyMfa: mocks.verifyMfa }));

function renderChallenge() {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/mfa", state: { from: { pathname: "/dashboard" } } }]}>
      <Routes>
        <Route path="/mfa" element={<MfaChallenge />} />
        <Route path="/dashboard" element={<div>dashboard destination</div>} />
        <Route path="/login" element={<div>login destination</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("MfaChallenge", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState = {
      user: { id: "user" },
      loading: false,
      mfaLoading: false,
      mfaRequired: true,
      mfaFactors: [{ id: "factor-1", friendlyName: "TuneLab", createdAt: "2026-08-13T00:00:00Z" }],
      refreshMfa: mocks.refreshMfa,
    };
    mocks.refreshMfa.mockResolvedValue(undefined);
  });

  it("verifies the selected factor and continues to the requested page", async () => {
    mocks.verifyMfa.mockResolvedValue(undefined);
    renderChallenge();

    fireEvent.change(screen.getByLabelText("security.verificationCode"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "security.verifyAndContinue" }));

    await waitFor(() => expect(mocks.verifyMfa).toHaveBeenCalledWith("factor-1", "123456"));
    expect(await screen.findByText("dashboard destination")).toBeInTheDocument();
  });

  it("keeps the challenge visible when the code is rejected", async () => {
    mocks.verifyMfa.mockRejectedValue(new Error("Invalid code"));
    renderChallenge();

    fireEvent.change(screen.getByLabelText("security.verificationCode"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "security.verifyAndContinue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid code");
    expect(screen.queryByText("dashboard destination")).not.toBeInTheDocument();
  });
});
