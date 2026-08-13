import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Signup from "./Signup";

const { signUp, signInWithOAuth, toast } = vi.hoisted(() => ({
  signUp: vi.fn(),
  signInWithOAuth: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/integrations/supabase/client", () => ({
  supabase: { auth: { signUp, signInWithOAuth } },
}));

vi.mock("@/hooks/use-toast", () => ({
  useToast: () => ({ toast }),
}));

vi.mock("@/i18n/LanguageContext", () => ({
  useLanguage: () => ({ t: (key: string) => key }),
}));

function renderSignup() {
  return render(
    <MemoryRouter initialEntries={["/signup"]}>
      <Routes>
        <Route path="/signup" element={<Signup />} />
        <Route path="/dashboard" element={<div>dashboard</div>} />
        <Route path="/login" element={<div>login</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function submitSignup() {
  fireEvent.change(screen.getByLabelText("signup.name"), { target: { value: "Test User" } });
  fireEvent.change(screen.getByLabelText("signup.email"), { target: { value: "test@example.com" } });
  fireEvent.change(screen.getByLabelText("signup.password"), { target: { value: "password123" } });
  fireEvent.click(screen.getByRole("button", { name: "signup.create" }));
}

describe("Signup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows email-confirmation state when Supabase does not return a session", async () => {
    signUp.mockResolvedValue({ data: { session: null }, error: null });
    renderSignup();

    submitSignup();

    expect(await screen.findByText("signup.checkEmailTitle")).toBeInTheDocument();
    expect(screen.getByText("test@example.com")).toBeInTheDocument();
    expect(screen.queryByText("dashboard")).not.toBeInTheDocument();
    expect(toast).not.toHaveBeenCalled();
  });

  it("navigates to dashboard when signup returns a session", async () => {
    signUp.mockResolvedValue({ data: { session: { access_token: "token" } }, error: null });
    renderSignup();

    submitSignup();

    await waitFor(() => expect(screen.getByText("dashboard")).toBeInTheDocument());
    expect(toast).toHaveBeenCalled();
  });

  it("keeps the form visible when signup fails", async () => {
    signUp.mockResolvedValue({ data: { session: null }, error: new Error("signup failed") });
    renderSignup();

    submitSignup();

    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({
      title: "auth.signUpFailed",
      description: "signup failed",
    })));
    expect(screen.getByRole("button", { name: "signup.create" })).toBeInTheDocument();
  });
});
