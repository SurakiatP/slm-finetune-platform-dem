import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Login from "./Login";

const mocks = vi.hoisted(() => ({
  signInWithPassword: vi.fn(),
  signInWithOAuth: vi.fn(),
  getMfaAssuranceLevel: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/integrations/supabase/client", () => ({
  supabase: { auth: { signInWithPassword: mocks.signInWithPassword, signInWithOAuth: mocks.signInWithOAuth } },
}));
vi.mock("@/lib/accountSecurity", () => ({ getMfaAssuranceLevel: mocks.getMfaAssuranceLevel }));
vi.mock("@/hooks/use-toast", () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock("@/i18n/LanguageContext", () => ({ useLanguage: () => ({ t: (key: string) => key }) }));

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/mfa" element={<div>mfa destination</div>} />
        <Route path="/dashboard" element={<div>dashboard destination</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function submitLogin() {
  fireEvent.change(screen.getByLabelText("login.email"), { target: { value: "user@example.com" } });
  fireEvent.change(screen.getByLabelText("login.password"), { target: { value: "password" } });
  fireEvent.click(screen.getByRole("button", { name: "login.signIn" }));
}

describe("Login MFA routing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.signInWithPassword.mockResolvedValue({ error: null });
  });

  it("sends an enrolled AAL1 user to the MFA challenge", async () => {
    mocks.getMfaAssuranceLevel.mockResolvedValue({ currentLevel: "aal1", nextLevel: "aal2" });
    renderLogin();
    submitLogin();
    expect(await screen.findByText("mfa destination")).toBeInTheDocument();
  });

  it("continues normally when no second factor is enrolled", async () => {
    mocks.getMfaAssuranceLevel.mockResolvedValue({ currentLevel: "aal1", nextLevel: "aal1" });
    renderLogin();
    submitLogin();
    await waitFor(() => expect(screen.getByText("dashboard destination")).toBeInTheDocument());
  });
});
