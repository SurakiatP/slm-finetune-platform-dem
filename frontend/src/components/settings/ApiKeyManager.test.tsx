import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiKeyManager } from "./ApiKeyManager";
import * as apiKeysApi from "@/lib/apiKeysApi";

const mockToast = vi.fn();
vi.mock("@/hooks/use-toast", () => ({ useToast: () => ({ toast: mockToast }) }));

describe("ApiKeyManager", () => {
  const sampleKey: apiKeysApi.ApiKey = {
    id: "key-123",
    name: "Production Service",
    keyPrefix: "sk-slm-prod",
    keySuffix: "abcdef1234567890",
    rawKey: "sk-slm-prod-abcdef1234567890",
    status: "active",
    lastUsedAt: null,
    createdAt: "2026-09-24T10:00:00.000Z",
  };

  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  it("renders masked API key by default and reveals real API key when eye toggle is clicked", async () => {
    vi.spyOn(apiKeysApi, "listApiKeys").mockResolvedValue([sampleKey]);

    render(<ApiKeyManager />);

    expect(await screen.findByText("Production Service")).toBeInTheDocument();
    // Default masked view
    expect(screen.getByText("sk-slm-prod••••••••7890")).toBeInTheDocument();
    expect(screen.queryByText("sk-slm-prod-abcdef1234567890")).not.toBeInTheDocument();

    // Click Eye button to show real API key
    const revealBtn = screen.getByTitle("Show real API key");
    fireEvent.click(revealBtn);

    // Now real API key is revealed
    expect(await screen.findByText("sk-slm-prod-abcdef1234567890")).toBeInTheDocument();

    // Click EyeOff button to hide it again
    const hideBtn = screen.getByTitle("Hide API key");
    fireEvent.click(hideBtn);

    expect(screen.getByText("sk-slm-prod••••••••7890")).toBeInTheDocument();
    expect(screen.queryByText("sk-slm-prod-abcdef1234567890")).not.toBeInTheDocument();
  });

  it("copies the real API key to clipboard", async () => {
    vi.spyOn(apiKeysApi, "listApiKeys").mockResolvedValue([sampleKey]);

    render(<ApiKeyManager />);
    expect(await screen.findByText("Production Service")).toBeInTheDocument();

    const copyBtn = screen.getByTitle("Copy real API key");
    fireEvent.click(copyBtn);

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("sk-slm-prod-abcdef1234567890");
    expect(mockToast).toHaveBeenCalledWith({ title: "API Key copied to clipboard" });
  });

  it("generates a new key and displays the real API key in the save modal", async () => {
    vi.spyOn(apiKeysApi, "listApiKeys").mockResolvedValue([]);
    const createdKey: apiKeysApi.ApiKey = {
      id: "key-999",
      name: "New Integration",
      keyPrefix: "sk-slm-newi",
      keySuffix: "fedcba9876543210",
      rawKey: "sk-slm-newi-fedcba9876543210",
      status: "active",
      lastUsedAt: null,
      createdAt: "2026-09-24T12:00:00.000Z",
    };
    const createSpy = vi.spyOn(apiKeysApi, "createApiKey").mockResolvedValue(createdKey);

    render(<ApiKeyManager />);
    expect(await screen.findByText("No API keys created yet")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /create new key/i }));

    const input = screen.getByPlaceholderText(/e\.g\. Production Service/i);
    fireEvent.change(input, { target: { value: "New Integration" } });

    fireEvent.click(screen.getByRole("button", { name: /generate key/i }));

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith("New Integration");
    });

    // Save modal should display the real API key
    expect(await screen.findByText("Save your API Key")).toBeInTheDocument();
    expect(screen.getByText("sk-slm-newi-fedcba9876543210")).toBeInTheDocument();
  });
});
