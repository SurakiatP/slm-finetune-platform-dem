import { isEngineModelSupported, isEngineTaskSupported } from "@/lib/engineMappings";
import type { TaskType, BaseModel } from "@/types";

export interface ValidationIssue {
  code: string;
  message: string;
}

export interface ValidationResult {
  ok: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface PreflightInput {
  projectName: string;
  taskPrompt: string;
  taskType: TaskType | null;
  baseModel: BaseModel | null;
  files: File[];
}

// Production-grade limits
export const TRAINING_LIMITS = {
  MAX_FILES: 1,
  MAX_JSON_FILE_SIZE_BYTES: 10 * 1024 * 1024,
  MAX_PDF_FILE_SIZE_BYTES: 25 * 1024 * 1024,
  MIN_PROMPT_LEN: 10,
  MAX_PROMPT_LEN: 2000,
  MAX_NAME_LEN: 100,
  ALLOWED_EXTENSIONS: ["json", "jsonl", "pdf"] as const,
} as const;

function fileExt(name: string): string {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i + 1).toLowerCase();
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Validate training inputs before launching a job.
 * Returns structured errors (block launch) and warnings (advisory only).
 *
 * Translator `t` is optional — when provided, returns localized messages.
 */
export function validatePreflight(
  input: PreflightInput,
  t?: (key: string) => string,
): ValidationResult {
  const errors: ValidationIssue[] = [];
  const warnings: ValidationIssue[] = [];
  const tr = (key: string, fallback: string) => (t ? t(key) : fallback);

  // Required selections
  if (!input.taskType) {
    errors.push({ code: "task_type_missing", message: tr("preflight.taskTypeMissing", "Please select a task type.") });
  } else if (!isEngineTaskSupported(input.taskType)) {
    errors.push({ code: "task_type_unsupported", message: tr("preflight.taskTypeUnsupported", "This task type is not supported by the Engine yet.") });
  }
  if (!input.baseModel) {
    errors.push({ code: "base_model_missing", message: tr("preflight.baseModelMissing", "Please select a base model.") });
  } else if (!isEngineModelSupported(input.baseModel)) {
    errors.push({ code: "base_model_unsupported", message: tr("preflight.baseModelUnsupported", "This base model is not supported by the Engine.") });
  }

  // Prompt
  const promptLen = input.taskPrompt.trim().length;
  if (promptLen < TRAINING_LIMITS.MIN_PROMPT_LEN) {
    errors.push({
      code: "prompt_too_short",
      message: tr("preflight.promptTooShort", `Task description must be at least ${TRAINING_LIMITS.MIN_PROMPT_LEN} characters.`),
    });
  } else if (promptLen > TRAINING_LIMITS.MAX_PROMPT_LEN) {
    errors.push({
      code: "prompt_too_long",
      message: tr("preflight.promptTooLong", `Task description must be under ${TRAINING_LIMITS.MAX_PROMPT_LEN} characters.`),
    });
  }

  // Project name
  if (input.projectName.length > TRAINING_LIMITS.MAX_NAME_LEN) {
    errors.push({
      code: "name_too_long",
      message: tr("preflight.nameTooLong", `Project name must be under ${TRAINING_LIMITS.MAX_NAME_LEN} characters.`),
    });
  }

  // The Engine upload endpoint accepts one seed dataset per workflow.
  if (input.files.length === 0) {
    errors.push({ code: "file_required", message: tr("preflight.fileRequired", "Upload one seed file to start training.") });
  } else if (input.files.length > TRAINING_LIMITS.MAX_FILES) {
    errors.push({
      code: "too_many_files",
      message: tr("preflight.tooManyFiles", `Too many files (${input.files.length}). Maximum is ${TRAINING_LIMITS.MAX_FILES}.`),
    });
  }

  // Per-file checks
  for (const f of input.files) {
    const ext = fileExt(f.name);
    if (!TRAINING_LIMITS.ALLOWED_EXTENSIONS.includes(ext as typeof TRAINING_LIMITS.ALLOWED_EXTENSIONS[number])) {
      errors.push({
        code: "bad_format",
        message: tr("preflight.badFormat", `Unsupported file type: ${f.name}. Use JSON, JSONL, or PDF for QA.`),
      });
    }
    if (ext === "pdf" && input.taskType !== "qa") {
      errors.push({ code: "pdf_qa_only", message: tr("preflight.pdfQaOnly", "PDF seed files are supported for QA projects only.") });
    }
    if (f.size === 0) {
      errors.push({
        code: "empty_file",
        message: tr("preflight.emptyFile", `File is empty: ${f.name}`),
      });
    } else {
      const maxBytes = ext === "pdf"
        ? TRAINING_LIMITS.MAX_PDF_FILE_SIZE_BYTES
        : TRAINING_LIMITS.MAX_JSON_FILE_SIZE_BYTES;
      if (f.size <= maxBytes) continue;
      errors.push({
        code: "file_too_large",
        message: tr(
          "preflight.fileTooLarge",
          `File too large: ${f.name} (${formatBytes(f.size)}). Maximum is ${formatBytes(maxBytes)} for this format.`,
        ),
      });
    }
  }

  return { ok: errors.length === 0, errors, warnings };
}
