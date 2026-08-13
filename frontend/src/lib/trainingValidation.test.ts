import { describe, expect, it } from "vitest";
import { TRAINING_LIMITS, validatePreflight, type PreflightInput } from "@/lib/trainingValidation";

function input(overrides: Partial<PreflightInput> = {}): PreflightInput {
  return {
    projectName: "Project",
    taskPrompt: "Answer questions about the supplied policy document.",
    taskType: "qa",
    baseModel: "qwen2.5-1.5b",
    files: [new File(["{}"], "seed.json", { type: "application/json" })],
    ...overrides,
  };
}

function sizedFile(name: string, size: number): File {
  return { name, size } as File;
}

describe("training preflight contract", () => {
  it("accepts one JSON seed for a supported task and model", () => {
    expect(validatePreflight(input())).toEqual({ ok: true, errors: [], warnings: [] });
  });

  it("requires exactly one seed file", () => {
    expect(validatePreflight(input({ files: [] })).errors.map((e) => e.code)).toContain("file_required");
    expect(validatePreflight(input({ files: [sizedFile("a.json", 1), sizedFile("b.jsonl", 1)] })).errors.map((e) => e.code)).toContain("too_many_files");
  });

  it("rejects unsupported task, model and CSV input", () => {
    const result = validatePreflight(input({
      taskType: "ner",
      baseModel: "phi-3-mini",
      files: [sizedFile("seed.csv", 100)],
    }));
    expect(result.errors.map((e) => e.code)).toEqual(expect.arrayContaining([
      "task_type_unsupported",
      "base_model_unsupported",
      "bad_format",
    ]));
  });

  it("allows PDF only for QA and enforces format-specific limits", () => {
    expect(validatePreflight(input({ files: [sizedFile("seed.pdf", TRAINING_LIMITS.MAX_PDF_FILE_SIZE_BYTES)] })).ok).toBe(true);
    expect(validatePreflight(input({ taskType: "classification", files: [sizedFile("seed.pdf", 10)] })).errors.map((e) => e.code)).toContain("pdf_qa_only");
    expect(validatePreflight(input({ files: [sizedFile("seed.json", TRAINING_LIMITS.MAX_JSON_FILE_SIZE_BYTES + 1)] })).errors.map((e) => e.code)).toContain("file_too_large");
  });
});
