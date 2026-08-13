import { beforeEach, describe, expect, it, vi } from "vitest";

const { getSession } = vi.hoisted(() => ({ getSession: vi.fn() }));

vi.mock("@/integrations/supabase/client", () => ({
  supabase: { auth: { getSession } },
}));

import {
  EngineApiError,
  engineCreateProject,
  engineGenerateDataset,
  engineHealthCheck,
  engineUploadSeed,
} from "@/lib/engineApi";

function jsonResponse(body: unknown, status = 200, headers?: Record<string, string>) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("Engine API client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getSession.mockResolvedValue({ data: { session: { access_token: "jwt-token" } } });
    vi.stubGlobal("fetch", vi.fn());
  });

  it("sends the Supabase token and external project id", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ id: "engine-project" }));

    await engineCreateProject("Project", "Description", "qa", "supabase-project");

    expect(fetch).toHaveBeenCalledWith("/api/v1/projects", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({
        Authorization: "Bearer jwt-token",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({
        name: "Project",
        description: "Description",
        task_type: "qa",
        external_project_id: "supabase-project",
      }),
    }));
  });

  it("keeps the browser-generated multipart boundary", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ dataset_id: "dataset" }, 201));

    await engineUploadSeed(new File(["{}"], "seed.json", { type: "application/json" }), "project", "qa");

    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    expect(init.headers).toMatchObject({ Authorization: "Bearer jwt-token" });
    expect(init.headers).not.toHaveProperty("Content-Type");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("keeps health public", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("ok", { status: 200 }));

    await expect(engineHealthCheck()).resolves.toBe(true);

    expect(fetch).toHaveBeenCalledWith("/health");
    expect(getSession).not.toHaveBeenCalled();
  });

  it("fails locally when a protected request has no session", async () => {
    getSession.mockResolvedValue({ data: { session: null } });

    const error = await engineCreateProject("Project", "Description", "qa", "external")
      .catch((reason) => reason);

    expect(error).toBeInstanceOf(EngineApiError);
    expect(error).toMatchObject({ status: 401, code: "missing_session" });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sends a stable idempotency key for SDG", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ dataset_id: "generated", job_id: "job" }, 202));

    await engineGenerateDataset("project", "qa", "Answer questions about this policy.", "seed", 200, 50, "sdg:external");

    expect(fetch).toHaveBeenCalledWith("/api/v1/datasets/generate", expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: "Bearer jwt-token",
        "Idempotency-Key": "sdg:external",
      }),
    }));
  });

  it("parses the FastAPI error envelope and request id", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(
      { detail: "invalid payload", code: "validation_error", extra: { field: "task_type" } },
      422,
      { "X-Request-ID": "req-123" },
    ));

    const error = await engineCreateProject("Project", "Description", "qa", "external")
      .catch((reason) => reason);

    expect(error).toBeInstanceOf(EngineApiError);
    expect(error).toMatchObject({
      message: "invalid payload",
      status: 422,
      code: "validation_error",
      requestId: "req-123",
    });
  });
});
