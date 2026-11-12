import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ProtectedRoute } from "./ProtectedRoute";

const useAuth = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => useAuth() }));
vi.mock("@/i18n/LanguageContext", () => ({ useLanguage: () => ({ t: (key: string) => key }) }));

function renderRoute() {
  return render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <Routes>
        <Route path="/dashboard" element={<ProtectedRoute><div>protected dashboard</div></ProtectedRoute>} />
        <Route path="/login" element={<div>login page</div>} />
        <Route path="/mfa" element={<div>mfa page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProtectedRoute OIDC enforcement", () => {
  it("renders protected content for a fully authenticated user", () => {
    useAuth.mockReturnValue({ user: { id: "user" }, loading: false, mfaLoading: false, mfaRequired: false });
    renderRoute();
    expect(screen.getByText("protected dashboard")).toBeInTheDocument();
  });

  it("does not expose protected content while initializing", () => {
    useAuth.mockReturnValue({ user: { id: "user" }, loading: true });
    renderRoute();
    expect(screen.queryByText("protected dashboard")).not.toBeInTheDocument();
  });

  it("redirects a signed-out user to login", () => {
    useAuth.mockReturnValue({ user: null, loading: false, mfaLoading: false, mfaRequired: false });
    renderRoute();
    expect(screen.getByText("login page")).toBeInTheDocument();
  });
});
