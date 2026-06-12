import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { startTraining } from '@/api/endpoints/trainings'
import type {
  HPOSearchSpace,
  ManualTrainingConfig,
  Project,
  TrainingRequest,
} from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Field } from '@/components/ui/Field'
import { Input } from '@/components/ui/Input'
import { Modal } from '@/components/ui/Modal'
import { Select } from '@/components/ui/Select'
import { useToast } from '@/components/ui/toast-context'
import { useBaseModels, useDatasets } from '@/hooks/queries'
import { cn } from '@/lib/cn'

interface NewTrainingModalProps {
  project: Project
  open: boolean
  onClose: () => void
}

// --- Manual hyperparameters (defaults mirror ManualTrainingConfig) -----------

interface ManualFormState {
  learning_rate: string
  num_train_epochs: string
  per_device_train_batch_size: string
  gradient_accumulation_steps: string
  warmup_ratio: string
  weight_decay: string
  lr_scheduler_type: 'linear' | 'cosine' | 'constant'
  max_seq_length: string
  seed: string
  lora_r: string
  lora_alpha: string
  lora_dropout: string
}

const manualDefaults: ManualFormState = {
  learning_rate: '0.0002',
  num_train_epochs: '3',
  per_device_train_batch_size: '2',
  gradient_accumulation_steps: '8',
  warmup_ratio: '0.03',
  weight_decay: '0.01',
  lr_scheduler_type: 'cosine',
  max_seq_length: '2048',
  seed: '42',
  lora_r: '16',
  lora_alpha: '32',
  lora_dropout: '0.05',
}

function buildManualConfig(form: ManualFormState): ManualTrainingConfig {
  return {
    learning_rate: Number(form.learning_rate),
    num_train_epochs: Number(form.num_train_epochs),
    per_device_train_batch_size: Number(form.per_device_train_batch_size),
    gradient_accumulation_steps: Number(form.gradient_accumulation_steps),
    warmup_ratio: Number(form.warmup_ratio),
    weight_decay: Number(form.weight_decay),
    lr_scheduler_type: form.lr_scheduler_type,
    max_seq_length: Number(form.max_seq_length),
    seed: Number(form.seed),
    lora: {
      r: Number(form.lora_r),
      alpha: Number(form.lora_alpha),
      dropout: Number(form.lora_dropout),
    },
  }
}

// --- HPO search-space builder -------------------------------------------------

type SpaceKey = keyof HPOSearchSpace

interface SpaceRowDef {
  key: SpaceKey
  label: string
  kind: 'float' | 'int' | 'categorical'
  defaults: { low?: string; high?: string; log?: boolean; choices?: string }
}

const spaceRows: SpaceRowDef[] = [
  { key: 'learning_rate', label: 'learning_rate', kind: 'float', defaults: { low: '0.00001', high: '0.001', log: true } },
  { key: 'num_train_epochs', label: 'num_train_epochs', kind: 'int', defaults: { low: '2', high: '5' } },
  { key: 'per_device_train_batch_size', label: 'batch_size', kind: 'categorical', defaults: { choices: '1, 2, 4' } },
  { key: 'gradient_accumulation_steps', label: 'grad_accum', kind: 'categorical', defaults: { choices: '2, 4, 8' } },
  { key: 'warmup_ratio', label: 'warmup_ratio', kind: 'float', defaults: { low: '0', high: '0.1' } },
  { key: 'weight_decay', label: 'weight_decay', kind: 'float', defaults: { low: '0', high: '0.1' } },
  { key: 'lr_scheduler_type', label: 'lr_scheduler', kind: 'categorical', defaults: { choices: 'linear, cosine' } },
  { key: 'lora_r', label: 'lora_r', kind: 'categorical', defaults: { choices: '8, 16, 32' } },
  { key: 'lora_alpha', label: 'lora_alpha', kind: 'categorical', defaults: { choices: '16, 32, 64' } },
  { key: 'lora_dropout', label: 'lora_dropout', kind: 'float', defaults: { low: '0', high: '0.2' } },
]

interface SpaceRowState {
  enabled: boolean
  low: string
  high: string
  log: boolean
  choices: string
}

function initialSpaceState(): Record<SpaceKey, SpaceRowState> {
  return Object.fromEntries(
    spaceRows.map((row) => [
      row.key,
      {
        enabled: row.key === 'learning_rate', // sensible default: tune LR
        low: row.defaults.low ?? '',
        high: row.defaults.high ?? '',
        log: row.defaults.log ?? false,
        choices: row.defaults.choices ?? '',
      },
    ]),
  ) as Record<SpaceKey, SpaceRowState>
}

function parseChoices(text: string): (string | number)[] {
  return text
    .split(',')
    .map((c) => c.trim())
    .filter(Boolean)
    .map((c) => (c !== '' && !Number.isNaN(Number(c)) ? Number(c) : c))
}

function buildSearchSpace(state: Record<SpaceKey, SpaceRowState>): { space: HPOSearchSpace; error?: string } {
  const space: HPOSearchSpace = {}
  for (const row of spaceRows) {
    const s = state[row.key]
    if (!s.enabled) continue
    if (row.kind === 'categorical') {
      const choices = parseChoices(s.choices)
      if (choices.length === 0) return { space, error: `${row.label}: provide at least one choice` }
      space[row.key] = { type: 'categorical', choices } as never
    } else {
      const low = Number(s.low)
      const high = Number(s.high)
      if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low) {
        return { space, error: `${row.label}: high must be greater than low` }
      }
      if (s.log && low <= 0) return { space, error: `${row.label}: log scale requires low > 0` }
      space[row.key] =
        row.kind === 'float'
          ? ({ type: 'float', low, high, log: s.log } as never)
          : ({ type: 'int', low, high, log: s.log } as never)
    }
  }
  if (Object.keys(space).length === 0) return { space, error: 'Enable at least one parameter to search' }
  return { space }
}

// --- Modal ---------------------------------------------------------------------

export function NewTrainingModal({ project, open, onClose }: NewTrainingModalProps) {
  const [mode, setMode] = useState<'manual' | 'hpo'>('manual')
  const [datasetId, setDatasetId] = useState('')
  const [baseModel, setBaseModel] = useState('')
  const [trainingName, setTrainingName] = useState('')
  const [manual, setManual] = useState<ManualFormState>(manualDefaults)
  const [spaceState, setSpaceState] = useState(initialSpaceState)
  const [nTrials, setNTrials] = useState('6')
  const [objectiveMetric, setObjectiveMetric] = useState('eval_loss')
  const [direction, setDirection] = useState<'minimize' | 'maximize'>('minimize')
  const [sampler, setSampler] = useState<'tpe' | 'random'>('tpe')
  const [pruner, setPruner] = useState<'median' | 'none'>('median')
  const [formError, setFormError] = useState<string | null>(null)

  const { data: datasets } = useDatasets(project.id, { limit: 200 })
  const { data: baseModels } = useBaseModels()
  const queryClient = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()

  const usableDatasets = (datasets?.items ?? []).filter((d) => d.num_samples > 0)

  const mutation = useMutation({
    mutationFn: (body: TrainingRequest) => startTraining(body),
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: ['trainings'] })
      toast.success('Training job queued')
      onClose()
      navigate(`/projects/${project.id}/trainings/${res.training_id}`)
    },
    onError: (err) => toast.error(err.message),
  })

  const submit = () => {
    setFormError(null)
    if (!datasetId) {
      setFormError('Pick a dataset to train on.')
      return
    }
    const base = {
      project_id: project.id,
      dataset_id: datasetId,
      base_model: baseModel || null,
      training_name: trainingName.trim() || null,
    }

    if (mode === 'manual') {
      mutation.mutate({ ...base, mode: 'manual', manual_config: buildManualConfig(manual) })
      return
    }

    const { space, error } = buildSearchSpace(spaceState)
    if (error) {
      setFormError(error)
      return
    }
    const trials = Number(nTrials)
    if (!Number.isFinite(trials) || trials < 2 || trials > 20) {
      setFormError('Trials must be between 2 and 20.')
      return
    }
    mutation.mutate({
      ...base,
      mode: 'hpo',
      hpo_config: {
        n_trials: trials,
        objective_metric: objectiveMetric.trim() || 'eval_loss',
        direction,
        sampler,
        pruner,
        search_space: space,
        fixed_config: buildManualConfig(manual),
      },
    })
  }

  const numField = (label: string, key: keyof ManualFormState, hint?: string, step?: number) => (
    <Field key={key} label={label} hint={hint}>
      {(id) => (
        <Input
          id={id}
          type="number"
          step={step ?? 'any'}
          value={manual[key]}
          onChange={(e) => setManual((m) => ({ ...m, [key]: e.target.value }))}
        />
      )}
    </Field>
  )

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New training"
      description="Runs on the GPU worker via Unsloth + QLoRA; tracked in MLflow."
      widthClass="max-w-3xl"
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Dataset" required>
            {(id) => (
              <Select id={id} value={datasetId} onChange={(e) => setDatasetId(e.target.value)} required>
                <option value="" disabled>
                  {usableDatasets.length ? 'Select a dataset…' : 'No datasets with rows yet'}
                </option>
                {usableDatasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.num_samples.toLocaleString()} rows)
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="Base model" hint="Leave default unless you need a specific family.">
            {(id) => (
              <Select id={id} value={baseModel} onChange={(e) => setBaseModel(e.target.value)}>
                <option value="">Platform default</option>
                {(baseModels ?? []).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.display_name} ({m.params_billions}B)
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>

        <Field label="Training name" hint="Shown in MLflow; defaults to project + timestamp.">
          {(id) => <Input id={id} value={trainingName} onChange={(e) => setTrainingName(e.target.value)} />}
        </Field>

        <div role="radiogroup" aria-label="Training mode" className="grid gap-2 sm:grid-cols-2">
          {(
            [
              { value: 'manual', title: 'Manual', desc: 'Train once with the hyperparameters below.' },
              { value: 'hpo', title: 'HPO (Optuna)', desc: 'Search the space below across N trials, then keep the best.' },
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
                name="training_mode"
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

        {mode === 'hpo' && (
          <fieldset className="space-y-3 rounded-lg border border-line/60 p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-body-muted">
              Search space
            </legend>
            <div className="grid gap-4 sm:grid-cols-5">
              <Field label="Trials" hint="2–20">
                {(id) => (
                  <Input id={id} type="number" min={2} max={20} value={nTrials} onChange={(e) => setNTrials(e.target.value)} />
                )}
              </Field>
              <Field label="Objective">
                {(id) => <Input id={id} value={objectiveMetric} onChange={(e) => setObjectiveMetric(e.target.value)} className="font-mono text-xs" />}
              </Field>
              <Field label="Direction">
                {(id) => (
                  <Select id={id} value={direction} onChange={(e) => setDirection(e.target.value as typeof direction)}>
                    <option value="minimize">minimize</option>
                    <option value="maximize">maximize</option>
                  </Select>
                )}
              </Field>
              <Field label="Sampler">
                {(id) => (
                  <Select id={id} value={sampler} onChange={(e) => setSampler(e.target.value as typeof sampler)}>
                    <option value="tpe">tpe</option>
                    <option value="random">random</option>
                  </Select>
                )}
              </Field>
              <Field label="Pruner">
                {(id) => (
                  <Select id={id} value={pruner} onChange={(e) => setPruner(e.target.value as typeof pruner)}>
                    <option value="median">median</option>
                    <option value="none">none</option>
                  </Select>
                )}
              </Field>
            </div>

            <div className="space-y-2">
              {spaceRows.map((row) => {
                const s = spaceState[row.key]
                return (
                  <div
                    key={row.key}
                    className={cn(
                      'flex flex-wrap items-center gap-3 rounded-md border p-2.5 transition-colors',
                      s.enabled ? 'border-accent/30 bg-accent-muted/40' : 'border-line/40 bg-bg',
                    )}
                  >
                    <label className="flex w-44 cursor-pointer items-center gap-2 font-mono text-xs text-body">
                      <input
                        type="checkbox"
                        checked={s.enabled}
                        onChange={(e) =>
                          setSpaceState((prev) => ({ ...prev, [row.key]: { ...s, enabled: e.target.checked } }))
                        }
                        className="h-3.5 w-3.5 accent-[#22C55E]"
                      />
                      {row.label}
                    </label>
                    {s.enabled &&
                      (row.kind === 'categorical' ? (
                        <input
                          aria-label={`${row.label} choices`}
                          value={s.choices}
                          onChange={(e) =>
                            setSpaceState((prev) => ({ ...prev, [row.key]: { ...s, choices: e.target.value } }))
                          }
                          placeholder="choices, comma-separated"
                          className="h-8 flex-1 rounded border border-line bg-bg px-2 font-mono text-xs"
                        />
                      ) : (
                        <span className="flex flex-1 items-center gap-2">
                          <input
                            aria-label={`${row.label} low`}
                            type="number"
                            step="any"
                            value={s.low}
                            onChange={(e) =>
                              setSpaceState((prev) => ({ ...prev, [row.key]: { ...s, low: e.target.value } }))
                            }
                            className="h-8 w-28 rounded border border-line bg-bg px-2 font-mono text-xs"
                          />
                          <span className="text-xs text-body-muted">to</span>
                          <input
                            aria-label={`${row.label} high`}
                            type="number"
                            step="any"
                            value={s.high}
                            onChange={(e) =>
                              setSpaceState((prev) => ({ ...prev, [row.key]: { ...s, high: e.target.value } }))
                            }
                            className="h-8 w-28 rounded border border-line bg-bg px-2 font-mono text-xs"
                          />
                          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-body-muted">
                            <input
                              type="checkbox"
                              checked={s.log}
                              onChange={(e) =>
                                setSpaceState((prev) => ({ ...prev, [row.key]: { ...s, log: e.target.checked } }))
                              }
                              className="h-3 w-3 accent-[#22C55E]"
                            />
                            log
                          </label>
                        </span>
                      ))}
                  </div>
                )
              })}
            </div>
          </fieldset>
        )}

        <fieldset className="rounded-lg border border-line/60 p-4">
          <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-body-muted">
            {mode === 'manual' ? 'Hyperparameters' : 'Fixed values (used when not searched)'}
          </legend>
          <div className="grid gap-4 sm:grid-cols-3">
            {numField('Learning rate', 'learning_rate')}
            {numField('Epochs', 'num_train_epochs', '1–20', 1)}
            {numField('Batch size', 'per_device_train_batch_size', undefined, 1)}
            {numField('Grad accumulation', 'gradient_accumulation_steps', undefined, 1)}
            {numField('Warmup ratio', 'warmup_ratio')}
            {numField('Weight decay', 'weight_decay')}
            <Field label="LR scheduler">
              {(id) => (
                <Select
                  id={id}
                  value={manual.lr_scheduler_type}
                  onChange={(e) =>
                    setManual((m) => ({ ...m, lr_scheduler_type: e.target.value as ManualFormState['lr_scheduler_type'] }))
                  }
                >
                  <option value="linear">linear</option>
                  <option value="cosine">cosine</option>
                  <option value="constant">constant</option>
                </Select>
              )}
            </Field>
            {numField('Max seq length', 'max_seq_length', '128–8192', 1)}
            {numField('Seed', 'seed', undefined, 1)}
            {numField('LoRA r', 'lora_r', undefined, 1)}
            {numField('LoRA alpha', 'lora_alpha', undefined, 1)}
            {numField('LoRA dropout', 'lora_dropout', '0–0.5')}
          </div>
        </fieldset>

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
            Start training
          </Button>
        </div>
      </form>
    </Modal>
  )
}
