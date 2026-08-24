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
import type { Dataset, TaskType } from '@/api/types'

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

/**
 * Classifies a dataset's role in the seed → sdg → hold-out lineage for
 * display (e.g. a badge next to its name). A dataset is "hold-out" when it
 * is a split held out of another dataset — signalled by `parent_dataset_id`
 * being set, or (fallback, in case a given response doesn't populate that
 * field) `generation_metadata.role === 'holdout'`. A directly-uploaded
 * dataset (not a seed upload — `source === 'uploaded'`) is its own
 * "uploaded" role: it's trainable as-is, unlike a "seed" which only feeds
 * SDG. Otherwise a plain seed upload is "seed", and everything else
 * (SDG-generated training data) is "training".
 */
export function datasetRoleTag(ds: Dataset): 'seed' | 'training' | 'hold-out' | 'uploaded' {
  const role = (ds.generation_metadata as { role?: string } | null)?.role
  if (ds.parent_dataset_id != null || role === 'holdout') return 'hold-out'
  if (ds.source === 'seed') return 'seed'
  if (ds.source === 'uploaded') return 'uploaded'
  return 'training'
}
