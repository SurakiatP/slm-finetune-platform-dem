import { useMutation } from '@tanstack/react-query'
import { ClipboardCheck, GitCompareArrows, Plus } from 'lucide-react'
import { useState } from 'react'

import { compareEvaluations, startEvaluation } from '@/api/endpoints/evaluations'
import type { EvaluationCompareResponse, EvaluationCreate, TaskType } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field } from '@/components/ui/Field'
import { Modal } from '@/components/ui/Modal'
import { Select } from '@/components/ui/Select'
import { useToast } from '@/components/ui/toast-context'
import { EvaluationRow } from '@/features/evaluations/EvaluationRow'
import { useProjectContext } from '@/features/shared/useProjectContext'
import { useDatasets, useModels } from '@/hooks/queries'
import { useEvalRegistry } from '@/hooks/useEvalRegistry'
import { formatNumber, shortId } from '@/lib/format'

export default function EvaluationListPage() {
  const { project } = useProjectContext()
  const { ids, add, remove } = useEvalRegistry(project.id)
  const [createOpen, setCreateOpen] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [compareResult, setCompareResult] = useState<EvaluationCompareResponse | null>(null)
  const toast = useToast()

  const compareMutation = useMutation({
    mutationFn: () => compareEvaluations({ evaluation_ids: selected }),
    onSuccess: setCompareResult,
    onError: (err) => toast.error(err.message),
  })

  const toggleSelect = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-body-muted">
          Task-specific metrics per model + dataset pair, with an optional LLM judge.
          <span className="ml-1 text-body-muted/60">(History is stored in this browser.)</span>
        </p>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={selected.length < 2 || selected.length > 10}
            loading={compareMutation.isPending}
            onClick={() => compareMutation.mutate()}
          >
            <GitCompareArrows className="h-3.5 w-3.5" aria-hidden />
            Compare ({selected.length})
          </Button>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden />
            New evaluation
          </Button>
        </div>
      </div>

      {compareResult && (
        <div className="mb-4">
          <ComparePanel result={compareResult} onClose={() => setCompareResult(null)} />
        </div>
      )}

      {ids.length === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title="No evaluations yet"
          description="Evaluate a trained model against a dataset to get accuracy, F1, ROUGE, or tool-calling metrics."
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" aria-hidden />
              New evaluation
            </Button>
          }
        />
      ) : (
        <ul className="space-y-3">
          {ids.map((id) => (
            <li key={id}>
              <EvaluationRow
                evaluationId={id}
                selected={selected.includes(id)}
                onToggleSelect={() => toggleSelect(id)}
                onForget={() => {
                  remove(id)
                  setSelected((prev) => prev.filter((x) => x !== id))
                }}
              />
            </li>
          ))}
        </ul>
      )}

      <CreateEvaluationModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        projectId={project.id}
        taskType={project.task_type}
        onCreated={add}
      />
    </>
  )
}

function CreateEvaluationModal({
  open,
  onClose,
  projectId,
  taskType,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  projectId: string
  taskType: TaskType
  onCreated: (evaluationId: string) => void
}) {
  const [modelId, setModelId] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [useJudge, setUseJudge] = useState(false)
  const { data: models } = useModels(projectId, { limit: 200 })
  const { data: datasets } = useDatasets(projectId, { limit: 200 })
  const toast = useToast()

  // The LLM judge only applies to free-form tasks (qa / tool_calling).
  // Classification is scored with closed-set rule-based metrics, so the backend
  // skips the judge — hide the toggle entirely to avoid confusion.
  const judgeApplies = taskType !== 'classification'

  const usableDatasets = (datasets?.items ?? []).filter((d) => d.num_samples > 0)

  const mutation = useMutation({
    mutationFn: (body: EvaluationCreate) => startEvaluation(body),
    onSuccess: (res) => {
      toast.success('Evaluation queued')
      onCreated(res.evaluation_id)
      onClose()
    },
    onError: (err) => toast.error(err.message),
  })

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New evaluation"
      description="Runs the dataset through the model and scores predictions with task-specific metrics."
      widthClass="max-w-xl"
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (!modelId || !datasetId) return
          mutation.mutate({
            model_artifact_id: modelId,
            dataset_id: datasetId,
            use_llm_judge: judgeApplies && useJudge,
            // Judge model is platform-controlled (see settings.llm_judge_model);
            // never sent from the UI so the backend default always applies.
            judge_model: null,
          })
        }}
      >
        <Field
          label="Model artifact"
          required
          hint="Only models exported to GGUF (registered with Ollama) can be evaluated."
        >
          {(id) => (
            <Select id={id} value={modelId} onChange={(e) => setModelId(e.target.value)} required>
              <option value="" disabled>
                {models?.items.length ? 'Select a model…' : 'No model artifacts in this project'}
              </option>
              {(models?.items ?? []).map((m) => (
                <option key={m.id} value={m.id} disabled={!m.ollama_model_tag}>
                  {m.name}
                  {m.ollama_model_tag ? '' : ' — export to GGUF first'}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <Field label="Dataset" required>
          {(id) => (
            <Select id={id} value={datasetId} onChange={(e) => setDatasetId(e.target.value)} required>
              <option value="" disabled>
                {usableDatasets.length ? 'Select a dataset…' : 'No datasets with rows'}
              </option>
              {usableDatasets.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.num_samples.toLocaleString()} rows)
                </option>
              ))}
            </Select>
          )}
        </Field>

        {judgeApplies ? (
          <>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-body">
              <input
                type="checkbox"
                checked={useJudge}
                onChange={(e) => setUseJudge(e.target.checked)}
                className="h-4 w-4 accent-[#22C55E]"
              />
              Score responses with an LLM judge
            </label>

            {useJudge && (
              <p className="text-xs text-body-muted">
                The judge model is set by the platform —{' '}
                <span className="font-mono">qwen/qwen3-235b-a22b-2507</span>.
              </p>
            )}
          </>
        ) : (
          <p className="text-xs text-body-muted">
            Classification is scored with rule-based metrics (accuracy, macro-F1) —
            the LLM judge does not apply.
          </p>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={!modelId || !datasetId} loading={mutation.isPending}>
            Start evaluation
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function ComparePanel({ result, onClose }: { result: EvaluationCompareResponse; onClose: () => void }) {
  const metricNames = Object.keys(result.metrics)
  const hasJudge = Object.values(result.judge_scores).some((v) => v !== null)

  return (
    <Card>
      <CardHeader
        title="Comparison"
        description={`${result.evaluation_ids.length} evaluations side by side`}
        actions={
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        }
      />
      <CardBody>
        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="border-b border-line/60 text-body-muted">
                <th scope="col" className="px-3 py-2 font-medium">metric</th>
                {result.evaluation_ids.map((id) => (
                  <th key={id} scope="col" className="px-3 py-2 font-medium" title={id}>
                    {shortId(id)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metricNames.map((metric) => {
                const row = result.metrics[metric]
                const values = result.evaluation_ids.map((id) => row[id] ?? null)
                const best = Math.max(...values.filter((v): v is number => v !== null))
                return (
                  <tr key={metric} className="border-b border-line/40 last:border-0">
                    <th scope="row" className="px-3 py-2 font-medium text-body-muted">{metric}</th>
                    {values.map((v, i) => (
                      <td
                        key={result.evaluation_ids[i]}
                        className={
                          v !== null && v === best ? 'px-3 py-2 font-semibold text-accent' : 'px-3 py-2 text-body'
                        }
                      >
                        {formatNumber(v)}
                      </td>
                    ))}
                  </tr>
                )
              })}
              {hasJudge && (
                <tr>
                  <th scope="row" className="px-3 py-2 font-medium text-body-muted">llm_judge</th>
                  {result.evaluation_ids.map((id) => (
                    <td key={id} className="px-3 py-2 text-body">
                      {formatNumber(result.judge_scores[id] ?? null)}
                    </td>
                  ))}
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  )
}
