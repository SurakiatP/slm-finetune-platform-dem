import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";

import Leaderboard from "./Leaderboard";
import * as queries from "@/hooks/queries";

vi.mock("@/hooks/queries", () => ({
  useModels: vi.fn(),
  useTrainings: vi.fn(),
  useProjects: vi.fn(),
  useEvaluations: vi.fn(),
  useTaskTypes: vi.fn().mockReturnValue({ data: [] }),
  useBaseModels: vi.fn().mockReturnValue({ data: [] }),
}));

vi.mock("@/i18n/LanguageContext", () => ({
  useLanguage: () => ({
    t: (key: string) => key,
  }),
}));

describe("Leaderboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders empty state when there are no models", () => {
    vi.mocked(queries.useModels).mockReturnValue({
      data: { items: [], total: 0, limit: 200, offset: 0 },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useModels>);
    vi.mocked(queries.useTrainings).mockReturnValue({
      data: { items: [], total: 0, limit: 200, offset: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof queries.useTrainings>);
    vi.mocked(queries.useProjects).mockReturnValue({
      data: { items: [], total: 0, limit: 200, offset: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof queries.useProjects>);
    vi.mocked(queries.useEvaluations).mockReturnValue({
      data: { items: [], total: 0, limit: 200, offset: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof queries.useEvaluations>);

    render(
      <MemoryRouter>
        <Leaderboard />
      </MemoryRouter>
    );

    expect(screen.getByText("leaderboard.title")).toBeInTheDocument();
    expect(screen.getByText("leaderboard.emptyTitle")).toBeInTheDocument();
  });

  it("ranks models correctly and renders ranking table with metrics", () => {
    const mockModels = [
      {
        id: "art-1",
        training_job_id: "tr-1",
        name: "model-a",
        base_model: "qwen2.5-1.5b",
        size_mb: 850,
        ollama_model_tag: "model-a:latest",
        created_at: "2026-09-01T10:00:00Z",
      },
      {
        id: "art-2",
        training_job_id: "tr-2",
        name: "model-b",
        base_model: "llama-3.2-1b",
        size_mb: 1200,
        ollama_model_tag: null,
        created_at: "2026-09-02T10:00:00Z",
      },
    ];

    const mockTrainings = [
      {
        id: "tr-1",
        project_id: "p-1",
        training_name: "alpha-classifier",
        base_model: "qwen2.5-1.5b",
      },
      {
        id: "tr-2",
        project_id: "p-2",
        training_name: "beta-qa",
        base_model: "llama-3.2-1b",
      },
    ];

    const mockProjects = [
      {
        id: "p-1",
        name: "Alpha Project",
        task_type: "classification",
      },
      {
        id: "p-2",
        name: "Beta Project",
        task_type: "qa",
      },
    ];

    const mockEvaluations = [
      {
        id: "ev-1",
        model_artifact_id: "art-1",
        dataset_id: "ds-1",
        status: "completed",
        metrics_json: { accuracy: 0.952, f1_macro: 0.93 },
        llm_judge_score: 4.8,
        created_at: "2026-09-03T10:00:00Z",
      },
      {
        id: "ev-2",
        model_artifact_id: "art-2",
        dataset_id: "ds-2",
        status: "completed",
        metrics_json: { rougeL: 0.785, bleu: 0.65 },
        llm_judge_score: 4.1,
        created_at: "2026-09-04T10:00:00Z",
      },
    ];

    vi.mocked(queries.useModels).mockReturnValue({
      data: { items: mockModels, total: 2, limit: 200, offset: 0 },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof queries.useModels>);
    vi.mocked(queries.useTrainings).mockReturnValue({
      data: { items: mockTrainings, total: 2, limit: 200, offset: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof queries.useTrainings>);
    vi.mocked(queries.useProjects).mockReturnValue({
      data: { items: mockProjects, total: 2, limit: 200, offset: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof queries.useProjects>);
    vi.mocked(queries.useEvaluations).mockReturnValue({
      data: { items: mockEvaluations, total: 2, limit: 200, offset: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof queries.useEvaluations>);

    render(
      <MemoryRouter>
        <Leaderboard />
      </MemoryRouter>
    );

    // Header and titles
    expect(screen.getByText("leaderboard.title")).toBeInTheDocument();
    expect(screen.getByText("leaderboard.rankings")).toBeInTheDocument();

    // Model display names from training names (appears in KPI card and table)
    expect(screen.getAllByText("alpha-classifier").length).toBe(2);
    expect(screen.getByText("beta-qa")).toBeInTheDocument();

    // Project names
    expect(screen.getByText("Alpha Project")).toBeInTheDocument();
    expect(screen.getByText("Beta Project")).toBeInTheDocument();

    // Primary scores
    expect(screen.getByText("95.2%")).toBeInTheDocument();
    expect(screen.getByText("78.5%")).toBeInTheDocument();

    // Judge scores
    expect(screen.getByText("4.8")).toBeInTheDocument();
    expect(screen.getByText("4.1")).toBeInTheDocument();

    // Deployable badge for model-a
    expect(screen.getByText("leaderboard.deployable")).toBeInTheDocument();

    // Search filter
    const searchInput = screen.getByPlaceholderText("leaderboard.searchPlaceholder");
    fireEvent.change(searchInput, { target: { value: "alpha" } });

    expect(screen.getAllByText("alpha-classifier").length).toBe(2);
    expect(screen.queryByText("beta-qa")).not.toBeInTheDocument();
  });
});
