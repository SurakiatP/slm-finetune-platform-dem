import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { getSession } = vi.hoisted(() => ({ getSession: vi.fn() }));

vi.mock("@/integrations/supabase/client", () => ({
  supabase: { auth: { getSession } },
}));

import { buildWsUrl, useTrainingWebSocket } from "@/hooks/useTrainingWebSocket";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  readonly url: string;
  readonly protocols?: string | string[];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  close = vi.fn();

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
    MockWebSocket.instances.push(this);
  }
}

describe("useTrainingWebSocket", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockWebSocket.instances = [];
    getSession.mockResolvedValue({ data: { session: { access_token: "jwt-token" } } });
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("builds same-origin websocket URLs", () => {
    expect(buildWsUrl("job id")).toBe("ws://localhost:3000/ws/jobs/job%20id");
    expect(buildWsUrl("job", "https://engine.example.com", "https://app.example.com"))
      .toBe("wss://engine.example.com/ws/jobs/job");
    expect(buildWsUrl("job", "http://engine.local:8088", "https://app.example.com"))
      .toBe("ws://engine.local:8088/ws/jobs/job");
  });

  it("offers the Supabase JWT as the bearer subprotocol", async () => {
    const { unmount } = renderHook(() => useTrainingWebSocket("job-1"));

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    expect(MockWebSocket.instances[0]).toMatchObject({
      url: "ws://localhost:3000/ws/jobs/job-1",
      protocols: ["bearer", "jwt-token"],
    });
    unmount();
  });

  it("does not reconnect after a terminal event", async () => {
    vi.useFakeTimers();
    const { result, unmount } = renderHook(() => useTrainingWebSocket("job-2"));
    await act(async () => Promise.resolve());
    const socket = MockWebSocket.instances[0];

    act(() => {
      socket.onmessage?.({ data: JSON.stringify({
        type: "failed",
        job_id: "job-2",
        timestamp: new Date().toISOString(),
        error: "worker failed",
      }) } as MessageEvent);
      socket.onclose?.();
      vi.runAllTimers();
    });

    expect(result.current.failed?.error).toBe("worker failed");
    expect(MockWebSocket.instances).toHaveLength(1);
    unmount();
  });

  it("exposes connection failure after three rejected handshakes", async () => {
    vi.useFakeTimers();
    const { result, unmount } = renderHook(() => useTrainingWebSocket("job-3"));
    await act(async () => Promise.resolve());

    act(() => MockWebSocket.instances[0].onclose?.());
    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
    });
    act(() => MockWebSocket.instances[1].onclose?.());
    await act(async () => {
      vi.advanceTimersByTime(4000);
      await Promise.resolve();
    });
    act(() => MockWebSocket.instances[2].onclose?.());

    expect(result.current.connectionFailed).toBe(true);
    expect(MockWebSocket.instances).toHaveLength(3);
    unmount();
  });

  it("fails without opening a socket when the session is missing", async () => {
    getSession.mockResolvedValue({ data: { session: null } });
    const { result, unmount } = renderHook(() => useTrainingWebSocket("job-4"));

    await waitFor(() => expect(result.current.connectionFailed).toBe(true));
    expect(result.current.connectionError).toMatch(/sign in/i);
    expect(MockWebSocket.instances).toHaveLength(0);
    unmount();
  });
});
