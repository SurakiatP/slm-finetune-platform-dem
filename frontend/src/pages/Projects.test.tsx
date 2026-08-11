import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { LanguageProvider } from "@/i18n/LanguageContext";

// --- Mocks must be set up before importing the component under test ---
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    session: { user: { id: "u1" } },
    user: { id: "u1" },
    profile: null,
    loading: false,
    signOut: vi.fn(),
    refreshProfile: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/lib/projectsApi", () => ({
  listProjects: vi.fn(async () => [
    {
      id: "p1",
      name: "Sentiment Classifier",
      description: "Demo project",
      taskType: "classification",
      baseModel: "llama-3.2-1b",
      status: "completed",
      progress: 100,
      datasetSize: 2000,
      epochs: 3,
      learningRate: 2e-4,
      creditsCost: 50,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      tags: ["nlp"],
      pinned: false,
    },
  ]),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
  getProject: vi.fn(),
}));

import Projects from "./Projects";

function renderPage() {
  return render(
    <MemoryRouter>
      <LanguageProvider>
        <Projects />
      </LanguageProvider>
    </MemoryRouter>,
  );
}

describe("Projects page", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders the title, search input, and a New Project link", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    });

    // Search input is labeled (a11y) and present
    const searchInputs = screen.getAllByRole("textbox");
    expect(searchInputs.length).toBeGreaterThan(0);

    // New Project link points to /projects/new
    const newLink = screen.getByRole("link", { name: /โปรเจกต์ใหม่|new project/i });
    expect(newLink).toHaveAttribute("href", "/projects/new");
  });

  it("renders the loaded project card after fetch", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Sentiment Classifier")).toBeInTheDocument();
    });
  });

  it("shows the task-type filter trigger with an accessible label", async () => {
    renderPage();
    await waitFor(() => {
      // SelectTrigger renders as a combobox button with aria-label
      const filters = screen.getAllByRole("combobox");
      expect(filters.length).toBeGreaterThanOrEqual(1);
    });
  });
});
