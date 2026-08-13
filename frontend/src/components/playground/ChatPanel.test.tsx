import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { engineChatCompletion } = vi.hoisted(() => ({ engineChatCompletion: vi.fn() }));

vi.mock("@/lib/engineApi", () => ({
  EngineApiError: class EngineApiError extends Error {},
  engineChatCompletion,
}));

import { ChatPanel } from "@/components/playground/ChatPanel";

describe("ChatPanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("sends the Engine model id rather than the display label", async () => {
    engineChatCompletion.mockResolvedValue({
      choices: [{ message: { content: "real response" } }],
      usage: { completion_tokens: 2 },
    });
    render(<ChatPanel modelId="artifact-uuid" displayName="My model" />);

    fireEvent.change(screen.getByPlaceholderText("Type a message..."), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(engineChatCompletion).toHaveBeenCalledWith(expect.objectContaining({
      model: "artifact-uuid",
    })));
    expect(await screen.findByText("real response")).toBeInTheDocument();
  });

  it("shows the real API error and does not fabricate a response", async () => {
    engineChatCompletion.mockRejectedValue(new Error("Ollama unavailable"));
    render(<ChatPanel modelId="artifact-uuid" displayName="My model" />);

    fireEvent.change(screen.getByPlaceholderText("Type a message..."), { target: { value: "hello" } });
    fireEvent.keyDown(screen.getByPlaceholderText("Type a message..."), { key: "Enter" });

    expect(await screen.findByRole("alert")).toHaveTextContent("Ollama unavailable");
    expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
  });
});
