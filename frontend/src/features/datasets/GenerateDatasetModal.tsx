import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Info } from 'lucide-react'
import { useState } from 'react'

import { generateDataset } from '@/api/endpoints/datasets'
import type { Project, SDGRequest, ToolDefinition } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Field } from '@/components/ui/Field'
import { Input, Textarea } from '@/components/ui/Input'
import { Modal } from '@/components/ui/Modal'
import { Select } from '@/components/ui/Select'
import { useToast } from '@/components/ui/toast-context'
import { useDatasets, queryKeys } from '@/hooks/queries'
import { cn } from '@/lib/cn'

export interface SdgJobRef {
  jobId: string
  datasetId: string
}

interface GenerateDatasetModalProps {
  project: Project
  open: boolean
  onClose: () => void
  onJobStarted: (job: SdgJobRef) => void
}

/**
 * Models are fixed per Phase 9 decision Q6.1 — no per-request override.
 * Mirror of ai_engine/data_gen/models.py; update together.
 */
const PIPELINE_MODELS = [
  { role: 'Generator', model: 'qwen/qwen3-235b-a22b-2507' },
  { role: 'Judge', model: 'deepseek/deepseek-v4-flash' },
  { role: 'Diversity rules', model: 'deepseek/deepseek-v4-flash' },
]

const toolsPlaceholder = `[
  {
    "name": "set_oven",
    "description": "Set oven temperature",
    "parameters": {"celsius": {"type": "integer", "required": true}}
  }
]`

export function GenerateDatasetModal({ project, open, onClose, onJobStarted }: GenerateDatasetModalProps) {
  const [mode, setMode] = useState<'with_seed' | 'description_only'>('description_only')
  const [taskDescription, setTaskDescription] = useState(project.description ?? '')
  const [numSamples, setNumSamples] = useState('200')
  const [holdoutSize, setHoldoutSize] = useState('0')
  const [temperature, setTemperature] = useState('0.9')
  const [datasetName, setDatasetName] = useState('')
  const [seedDatasetId, setSeedDatasetId] = useState('')
  const [labelsText, setLabelsText] = useState('')
  const [toolsText, setToolsText] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const toast = useToast()

  // Pretty-print the tool definitions JSON. Tolerates the common copy-paste
  // failure where line wrapping injects raw newlines/tabs inside string
  // literals ("Bad control character") by collapsing control whitespace to a
  // single space before parsing — then re-indents the result.
  const formatToolsJson = () => {
    if (!toolsText.trim()) return
    const tryParse = (s: string) => JSON.parse(s) as unknown
    let parsed: unknown
    try {
      parsed = tryParse(toolsText)
    } catch {
      try {
        parsed = tryParse(toolsText.replace(/[\r\n\t]+/g, ' '))
      } catch (err) {
        setFormError(`Tool definitions: ${(err as Error).message}`)
        return
      }
    }
    setToolsText(JSON.stringify(parsed, null, 2))
    setFormError(null)
  }

  const { data: datasets } = useDatasets(project.id, { limit: 200 })
  // PDF seeds (QA) carry no rows (num_samples === 0) — their content is the PDF
  // itself, extracted at SDG time — so they must still be selectable as seeds.
  const isPdfSeed = (d: { generation_metadata: Record<string, unknown> | null }) =>
    !!d.generation_metadata && 'pdf_uri' in d.generation_metadata
  const seedDatasets = (datasets?.items ?? []).filter(
    (d) => d.source === 'seed' && (d.num_samples > 0 || isPdfSeed(d)),
  )

  const mutation = useMutation({
    mutationFn: (body: SDGRequest) => generateDataset(body),
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets(project.id) })
      toast.success('SDG job queued')
      onJobStarted({ jobId: res.job_id, datasetId: res.dataset_id })
      onClose()
    },
    onError: (err) => toast.error(err.message),
  })

  const buildRequest = (): SDGRequest | null => {
    setFormError(null)
    const base = {
      project_id: project.id,
      task_type: project.task_type,
      task_description: taskDescription.trim(),
      num_samples: Number(numSamples),
      holdout_size: Number(holdoutSize) || 0,
      temperature: Number(temperature),
      dataset_name: datasetName.trim() || null,
    }

    if (base.task_description.length < 10) {
      setFormError('Task description must be at least 10 characters.')
      return null
    }
    if (!Number.isFinite(base.num_samples) || base.num_samples < 1 || base.num_samples > 10_000) {
      setFormError('Samples must be between 1 and 10,000.')
      return null
    }
    if (base.holdout_size < 0 || base.holdout_size > 2_000) {
      setFormError('Holdout must be between 0 and 2,000.')
      return null
    }

    if (mode === 'with_seed') {
      if (!seedDatasetId) {
        setFormError('Pick a seed dataset (upload one first if the list is empty).')
        return null
      }
      return { ...base, sdg_mode: 'with_seed', seed_dataset_id: seedDatasetId }
    }

    if (project.task_type === 'classification') {
      const labels = [...new Set(labelsText.split(',').map((l) => l.trim()).filter(Boolean))]
      if (labels.length < 2) {
        setFormError('Provide at least 2 unique labels (comma-separated).')
        return null
      }
      return { ...base, sdg_mode: 'description_only', classification_config: { labels } }
    }

    if (project.task_type === 'tool_calling') {
      let tools: ToolDefinition[]
      try {
        const parsed = JSON.parse(toolsText) as unknown
        if (!Array.isArray(parsed) || parsed.length === 0) throw new Error('expected a non-empty array')
        tools = parsed as ToolDefinition[]
        const names = tools.map((t) => t.name)
        if (new Set(names).size !== names.length) {
          throw new Error('tool names must be unique')
        }
      } catch (err) {
        setFormError(`Tool definitions: ${(err as Error).message}`)
        return null
      }
      return { ...base, sdg_mode: 'description_only', tool_calling_config: { tool_definitions: tools } }
    }

    return { ...base, sdg_mode: 'description_only' }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Generate synthetic data"
      description="Teacher models (via OpenRouter) write, judge, and dedup training rows for this task."
      widthClass="max-w-2xl"
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          const body = buildRequest()
          if (body) mutation.mutate(body)
        }}
      >
        <div role="radiogroup" aria-label="SDG mode" className="grid gap-2 sm:grid-cols-2">
          {(
            [
              { value: 'description_only', title: 'From description', desc: 'No examples needed — describe the task and let the teacher invent rows.' },
              { value: 'with_seed', title: 'From seed dataset', desc: 'Pick an uploaded seed dataset; the teacher extrapolates in the same style.' },
            ] as const
          ).map((opt) => (
            <label
              key={opt.value}
              className={cn(
                'cursor-pointer rounded-lg border p-3 transition-colors',
                mode === opt.value ? 'border-accent bg-accent-muted' : 'border-line/60 bg-bg hover:border-body-muted',
              )}
            >
              <input
                type="radio"
                name="sdg_mode"
                value={opt.value}
                checked={mode === opt.value}
                onChange={() => setMode(opt.value)}
                className="sr-only"
              />
              <p className="text-sm font-medium text-body">{opt.title}</p>
              <p className="mt-1 text-xs leading-snug text-body-muted">{opt.desc}</p>
            </label>
          ))}
        </div>

        <Field label="Task description" required hint="What should the fine-tuned model learn? (min 10 chars)">
          {(id) => (
            <Textarea
              id={id}
              value={taskDescription}
              onChange={(e) => setTaskDescription(e.target.value)}
              placeholder="Answer questions about our 30-day return policy"
              required
            />
          )}
        </Field>

        {mode === 'with_seed' && (
          <Field
            label="Seed dataset"
            required
            hint={seedDatasets.length === 0 ? 'No seed datasets yet — use "Upload seed" first.' : undefined}
          >
            {(id) => (
              <Select id={id} value={seedDatasetId} onChange={(e) => setSeedDatasetId(e.target.value)} required>
                <option value="" disabled>
                  {seedDatasets.length ? 'Select a seed dataset…' : 'No seed datasets available'}
                </option>
                {seedDatasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({isPdfSeed(d) ? 'PDF' : `${d.num_samples} rows`})
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}

        {mode === 'description_only' && project.task_type === 'classification' && (
          <Field label="Labels" required hint="Comma-separated closed set, e.g. billing, technical, general">
            {(id) => (
              <Input
                id={id}
                value={labelsText}
                onChange={(e) => setLabelsText(e.target.value)}
                placeholder="billing, technical, general"
                required
              />
            )}
          </Field>
        )}

        {mode === 'description_only' && project.task_type === 'tool_calling' && (
          <Field
            label="Tool definitions (JSON)"
            required
            hint="Array of tools the samples may invoke. Use Format to tidy & validate."
          >
            {(id) => (
              <div className="space-y-2">
                <Textarea
                  id={id}
                  value={toolsText}
                  onChange={(e) => setToolsText(e.target.value)}
                  placeholder={toolsPlaceholder}
                  className="min-h-36 font-mono text-xs"
                  required
                />
                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={formatToolsJson}
                    disabled={!toolsText.trim()}
                  >
                    Format JSON
                  </Button>
                </div>
              </div>
            )}
          </Field>
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Samples" required hint="1–10,000">
            {(id) => (
              <Input
                id={id}
                type="number"
                min={1}
                max={10000}
                value={numSamples}
                onChange={(e) => setNumSamples(e.target.value)}
                required
              />
            )}
          </Field>
          <Field label="Holdout rows" hint="Extra rows for leak-free eval (0 = off)">
            {(id) => (
              <Input
                id={id}
                type="number"
                min={0}
                max={2000}
                value={holdoutSize}
                onChange={(e) => setHoldoutSize(e.target.value)}
              />
            )}
          </Field>
          <Field label="Temperature" hint="0–2; higher = more diverse">
            {(id) => (
              <Input
                id={id}
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={temperature}
                onChange={(e) => setTemperature(e.target.value)}
              />
            )}
          </Field>
        </div>

        <Field label="Dataset name" hint="Defaults to project + timestamp.">
          {(id) => <Input id={id} value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />}
        </Field>

        <div className="flex items-start gap-2 rounded-md border border-line/60 bg-bg p-3">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info" aria-hidden />
          <div className="text-xs text-body-muted">
            <p className="font-medium text-body">Pipeline models (fixed by the platform)</p>
            <dl className="mt-1 space-y-0.5 font-mono text-[11px]">
              {PIPELINE_MODELS.map((m) => (
                <div key={m.role} className="flex gap-2">
                  <dt className="w-28 shrink-0">{m.role}:</dt>
                  <dd className="text-body-muted/80">{m.model}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>

        {formError && (
          <p role="alert" className="text-xs text-danger">
            {formError}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={mutation.isPending}>
            Generate
          </Button>
        </div>
      </form>
    </Modal>
  )
}
