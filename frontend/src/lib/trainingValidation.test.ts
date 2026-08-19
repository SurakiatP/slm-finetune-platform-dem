import { describe, expect, it } from "vitest";
import { TRAINING_LIMITS, validatePreflight, type PreflightInput } from "@/lib/trainingValidation";

const supportedBaseModelIds = new Set(["unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit"]);

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

function validate(overrides: Partial<PreflightInput> = {}) {
  return validatePreflight(input(overrides), undefined, supportedBaseModelIds);
}

describe("training preflight contract", () => {
  it("accepts one JSON seed for a supported task and model", () => {
    expect(validate({ baseModel: "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit" })).toEqual({ ok: true, errors: [], warnings: [] });
  });

  it("requires exactly one seed file", () => {
    expect(validate({ baseModel: "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit", files: [] }).errors.map((e) => e.code)).toContain("file_required");
    expect(validate({ baseModel: "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit", files: [sizedFile("a.json", 1), sizedFile("b.jsonl", 1)] }).errors.map((e) => e.code)).toContain("too_many_files");
  });

  it("rejects unsupported task, model and CSV input", () => {
    const result = validate({
      taskType: "ner",
      baseModel: "phi-3-mini",
      files: [sizedFile("seed.csv", 100)],
    });
    expect(result.errors.map((e) => e.code)).toEqual(expect.arrayContaining([
      "task_type_unsupported",
      "base_model_unsupported",
      "bad_format",
    ]));
  });

  it("allows PDF only for QA and enforces format-specific limits", () => {
    expect(validate({ baseModel: "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit", files: [sizedFile("seed.pdf", TRAINING_LIMITS.MAX_PDF_FILE_SIZE_BYTES)] }).ok).toBe(true);
    expect(validate({ baseModel: "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit", taskType: "classification", files: [sizedFile("seed.pdf", 10)] }).errors.map((e) => e.code)).toContain("pdf_qa_only");
    expect(validate({ baseModel: "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit", files: [sizedFile("seed.json", TRAINING_LIMITS.MAX_JSON_FILE_SIZE_BYTES + 1)] }).errors.map((e) => e.code)).toContain("file_too_large");
  });
});
