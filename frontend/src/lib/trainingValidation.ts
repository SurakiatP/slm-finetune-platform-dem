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
  MAX_FILES: 10,
  MAX_FILE_SIZE_BYTES: 50 * 1024 * 1024, // 50MB per file
  MAX_TOTAL_SIZE_BYTES: 200 * 1024 * 1024, // 200MB combined
  MIN_PROMPT_LEN: 10,
  MAX_PROMPT_LEN: 2000,
  MAX_NAME_LEN: 100,
  ALLOWED_EXTENSIONS: ["csv", "json", "jsonl"] as const,
  RECOMMENDED_MIN_FILES: 1,
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
  }
  if (!input.baseModel) {
    errors.push({ code: "base_model_missing", message: tr("preflight.baseModelMissing", "Please select a base model.") });
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

  // File count
  if (input.files.length > TRAINING_LIMITS.MAX_FILES) {
    errors.push({
      code: "too_many_files",
      message: tr("preflight.tooManyFiles", `Too many files (${input.files.length}). Maximum is ${TRAINING_LIMITS.MAX_FILES}.`),
    });
  }

  // Per-file checks
  let totalSize = 0;
  for (const f of input.files) {
    totalSize += f.size;
    const ext = fileExt(f.name);
    if (!TRAINING_LIMITS.ALLOWED_EXTENSIONS.includes(ext as typeof TRAINING_LIMITS.ALLOWED_EXTENSIONS[number])) {
      errors.push({
        code: "bad_format",
        message: tr("preflight.badFormat", `Unsupported file type: ${f.name}. Use CSV, JSON, or JSONL.`),
      });
    }
    if (f.size === 0) {
      errors.push({
        code: "empty_file",
        message: tr("preflight.emptyFile", `File is empty: ${f.name}`),
      });
    } else if (f.size > TRAINING_LIMITS.MAX_FILE_SIZE_BYTES) {
      errors.push({
        code: "file_too_large",
        message: tr(
          "preflight.fileTooLarge",
          `File too large: ${f.name} (${formatBytes(f.size)}). Maximum is ${formatBytes(TRAINING_LIMITS.MAX_FILE_SIZE_BYTES)} per file.`,
        ),
      });
    }
  }

  if (totalSize > TRAINING_LIMITS.MAX_TOTAL_SIZE_BYTES) {
    errors.push({
      code: "total_size_exceeded",
      message: tr(
        "preflight.totalSizeExceeded",
        `Total upload size (${formatBytes(totalSize)}) exceeds limit of ${formatBytes(TRAINING_LIMITS.MAX_TOTAL_SIZE_BYTES)}.`,
      ),
    });
  }

  // Warnings (do not block)
  if (input.files.length === 0) {
    warnings.push({
      code: "no_files",
      message: tr(
        "preflight.noFilesWarning",
        "No training files uploaded — the system will generate synthetic data automatically.",
      ),
    });
  }

  return { ok: errors.length === 0, errors, warnings };
}
