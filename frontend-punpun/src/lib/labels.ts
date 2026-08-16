/**
 * Display-label helpers backed by the Engine meta endpoints
 * (GET /api/v1/tasks, GET /api/v1/base-models), replacing the old
 * static `taskTypeLabels` / `getBaseModelLabel` from src/data/mockData.ts.
 *
 * Both hooks fall back to a small static table (mirroring mockData's shape)
 * when the meta query hasn't resolved yet (loading) or a given id/task type
 * isn't in the server's list — so callers always get a string back, never
 * `undefined`, without having to special-case the loading state themselves.
 */
import { useBaseModels, useTaskTypes } from '@/hooks/queries'
import type { TaskType } from '@/api/types'

/** Static fallback — mirrors src/data/mockData.ts's `taskTypeLabels` for the
 *  three task types the backend actually supports (ADR-005). */
const TASK_TYPE_LABEL_FALLBACK: Record<TaskType, string> = {
  classification: 'Classification',
  tool_calling: 'Function Calling',
  qa: 'Question Answering',
}

/** Static fallback — mirrors src/data/mockData.ts's `baseModelLabels`. Keyed
 *  by the backend's `BaseModelInfo.id`; unknown ids fall through to the raw id. */
const BASE_MODEL_LABEL_FALLBACK: Record<string, string> = {
  'qwen2.5-1.5b': 'Qwen 2.5 1.5B',
  'qwen2.5-3b': 'Qwen 2.5 3B',
  'gemma-2-2b': 'Gemma 2 2B',
  'phi-3-mini': 'Phi-3 Mini',
  'llama-3.2-1b': 'Llama 3.2 1B',
  'smollm2-1.7b': 'SmolLM2 1.7B',
}

/**
 * Resolve a `TaskType` to its display name via `GET /api/v1/tasks`
 * (`TaskTypeInfo.display_name`), falling back to a static label while the
 * query is loading/errored or for a task type the server doesn't list.
 */
export function useTaskTypeLabel(): (taskType: TaskType | string | null | undefined) => string {
  const { data: taskTypes } = useTaskTypes()
  return (taskType) => {
    if (!taskType) return '—'
    const known = taskTypes?.find((t) => t.task_type === taskType)
    if (known) return known.display_name
    return TASK_TYPE_LABEL_FALLBACK[taskType as TaskType] ?? taskType
  }
}

/**
 * Resolve a base model id to its display name via `GET /api/v1/base-models`
 * (`BaseModelInfo.display_name`), falling back to a static label while the
 * query is loading/errored or for an id the server doesn't list.
 */
export function useBaseModelLabel(): (baseModel: string | null | undefined) => string {
  const { data: baseModels } = useBaseModels()
  return (baseModel) => {
    if (!baseModel) return '—'
    const known = baseModels?.find((m) => m.id === baseModel)
    if (known) return known.display_name
    return BASE_MODEL_LABEL_FALLBACK[baseModel] ?? baseModel
  }
}
