import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ModelSelectionStep } from "./ModelSelectionStep";

const models = [{
  id: "unsloth/Qwen3-0.6B-unsloth-bnb-4bit",
  display_name: "Qwen3 0.6B Instruct (4-bit)",
  family: "qwen",
  params_billions: 0.75,
  context_length: 32768,
  recommended_max_seq_length: 2048,
  quantization: "bnb-4bit",
  license: "apache-2.0",
  notes: "Smallest Qwen3 variant.",
  ollama_tag: "qwen3:0.6b",
}];

describe("ModelSelectionStep", () => {
  it("renders the Engine catalog metadata and selects its model ID", () => {
    const updateForm = vi.fn();
    render(
      <ModelSelectionStep
        formData={{ projectName: "", taskPrompt: "", taskType: null, baseModel: null, files: [] }}
        updateForm={updateForm}
        models={models}
        loading={false}
        error={null}
        onRetry={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Qwen3 0.6B Instruct/i }));

    expect(screen.getByText("32,768")).toBeInTheDocument();
    expect(screen.getByText("Smallest Qwen3 variant.")).toBeInTheDocument();
    expect(screen.getByText("Qwen3 0.6B Instruct (4-bit)")).toHaveClass("text-sky-700");
    expect(screen.getByText("0.75B")).toHaveClass("text-muted-foreground");
    expect(screen.getByText("Family")).toBeInTheDocument();
    expect(screen.getByText("Context")).toHaveClass("text-violet-700");
    expect(screen.getByText("Quantization")).toHaveClass("text-amber-700");
    expect(screen.getByText("qwen3:0.6b")).toBeInTheDocument();
    expect(updateForm).toHaveBeenCalledWith({ baseModel: models[0].id });
  });

  it("shows the retry action when the catalog request fails", () => {
    const onRetry = vi.fn();
    render(
      <ModelSelectionStep
        formData={{ projectName: "", taskPrompt: "", taskType: null, baseModel: null, files: [] }}
        updateForm={vi.fn()}
        models={[]}
        loading={false}
        error="Engine unavailable"
        onRetry={onRetry}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
