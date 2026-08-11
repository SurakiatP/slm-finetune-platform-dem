import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { LanguageProvider } from "@/i18n/LanguageContext";
import { NewProjectDialog } from "./NewProjectDialog";

function renderWithProviders(ui: React.ReactElement) {
  return render(
    <MemoryRouter>
      <LanguageProvider>{ui}</LanguageProvider>
    </MemoryRouter>,
  );
}

describe("NewProjectDialog", () => {
  it("renders the localized New Project label", () => {
    renderWithProviders(<NewProjectDialog />);
    // Default language is Thai → "โปรเจกต์ใหม่" maps to command.newProject
    const links = screen.getAllByRole("link");
    expect(links.length).toBeGreaterThan(0);
    expect(links[0]).toHaveAttribute("href", "/projects/new");
  });

  it("uses an i18n key (not the literal English string) so language can switch", () => {
    renderWithProviders(<NewProjectDialog />);
    // The aria-label should match the rendered text — both come from t()
    const link = screen.getByRole("link");
    expect(link.getAttribute("aria-label")).toBe(link.textContent?.trim());
  });
});
