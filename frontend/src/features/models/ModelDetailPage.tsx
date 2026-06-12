import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, Download, FlaskConical, MessagesSquare, PackageOpen } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { exportModel, modelDownloadUrl } from '@/api/endpoints/models'
import type { ArtifactFormat, ModelArtifact } from '@/api/types'
import { CopyButton } from '@/components/data/CopyButton'
import { JobProgressPanel } from '@/components/jobs/JobProgressPanel'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Field } from '@/components/ui/Field'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { useToast } from '@/components/ui/toast-context'
import { FormatBadges } from '@/features/models/ModelListPage'
import { useModel, queryKeys } from '@/hooks/queries'
import { useJobProgress, jobRefetchInterval } from '@/hooks/useJobProgress'
import { formatDateTime } from '@/lib/format'

export default function ModelDetailPage() {
  const { modelId } = useParams<{ modelId: string }>()
  const [exportJobId, setExportJobId] = useState<string | null>(null)
  // Poll the artifact only while an export job is in flight; the cadence lives
  // in a ref so the lazily-evaluated refetchInterval callback can read it.
  const exportPollRef = useRef<number | false>(false)
  const { data: model, isLoading } = useModel(modelId!, {
    refetchInterval: () => exportPollRef.current,
  })
  const queryClient = useQueryClient()

  const progress = useJobProgress(exportJobId, {
    onTerminal: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.model(modelId!) })
      void queryClient.invalidateQueries({ queryKey: ['models'] })
      void queryClient.invalidateQueries({ queryKey: queryKeys.inferenceModels })
    },
  })
  const exportTerminal = progress.completed !== null || progress.failed !== null

  useEffect(() => {
    exportPollRef.current = exportJobId ? jobRefetchInterval(exportTerminal, progress.socketOpen) : false
  }, [exportJobId, exportTerminal, progress.socketOpen])

  if (isLoading || !model) return <LoadingBlock label="Loading model" />

  return (
    <div className="space-y-4">
      <div>
        <Link
          to="/models"
          className="mb-1 inline-flex items-center gap-1 text-xs text-body-muted transition-colors hover:text-body"
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
          Models
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold text-body">
            {model.name}
            <FormatBadges model={model} />
          </h1>
          <div className="flex gap-2">
            {model.ollama_model_tag && (
              <Link
                to={`/playground?model=${encodeURIComponent(model.ollama_model_tag)}`}
                className="inline-flex h-8 items-center gap-1.5 rounded-md bg-accent px-3 text-xs font-semibold text-bg transition-colors hover:bg-accent-hover"
              >
                <MessagesSquare className="h-3.5 w-3.5" aria-hidden />
                Open in playground
              </Link>
            )}
          </div>
        </div>
      </div>

      <Card>
        <CardBody>
          <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs text-body-muted">Base model</dt>
              <dd className="mt-0.5 truncate font-mono text-xs" title={model.base_model}>
                {model.base_model.replace(/^unsloth\//, '')}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-body-muted">Size</dt>
              <dd className="mt-0.5 font-mono text-xs">
                {model.size_mb !== null ? `${model.size_mb.toFixed(0)} MB` : '—'}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-body-muted">Created</dt>
              <dd className="mt-0.5 font-mono text-xs">{formatDateTime(model.created_at)}</dd>
            </div>
            <div>
              <dt className="text-xs text-body-muted">Training</dt>
              <dd className="mt-0.5 flex items-center gap-1">
                <FlaskConical className="h-3 w-3 text-body-muted" aria-hidden />
                <span className="font-mono text-xs text-body-muted">{model.training_job_id.slice(0, 8)}…</span>
              </dd>
            </div>
            {model.ollama_model_tag && (
              <div className="col-span-2">
                <dt className="text-xs text-body-muted">Ollama tag</dt>
                <dd className="mt-0.5 flex items-center gap-1 font-mono text-xs text-accent">
                  {model.ollama_model_tag}
                  <CopyButton value={model.ollama_model_tag} label="Copy Ollama tag" />
                </dd>
              </div>
            )}
          </dl>
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <ExportPanel model={model} onJobStarted={setExportJobId} />
        <DownloadPanel model={model} />
      </div>

      {exportJobId && (
        <JobProgressPanel
          title="Export job"
          progress={progress}
          status={progress.failed ? 'failed' : progress.completed ? 'completed' : 'running'}
        />
      )}
    </div>
  )
}

function ExportPanel({
  model,
  onJobStarted,
}: {
  model: ModelArtifact
  onJobStarted: (jobId: string) => void
}) {
  const [format, setFormat] = useState<ArtifactFormat>('gguf')
  const [quantization, setQuantization] = useState('q4_k_m')
  const toast = useToast()

  const mutation = useMutation({
    mutationFn: () =>
      exportModel(model.id, {
        format,
        quantization: format === 'gguf' ? quantization.trim() || null : null,
      }),
    onSuccess: (res) => {
      toast.success(`Export to ${res.format} queued`)
      onJobStarted(res.job_id)
    },
    onError: (err) => toast.error(err.message),
  })

  return (
    <Card>
      <CardHeader
        title="Export"
        description="GGUF registers the model with Ollama for serving; safetensors merges full weights."
      />
      <CardBody>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            mutation.mutate()
          }}
        >
          <Field label="Format" className="w-40">
            {(id) => (
              <Select id={id} value={format} onChange={(e) => setFormat(e.target.value as ArtifactFormat)}>
                <option value="gguf">gguf</option>
                <option value="safetensors">safetensors</option>
              </Select>
            )}
          </Field>
          {format === 'gguf' && (
            <Field label="Quantization" className="w-36" hint="e.g. q4_k_m, q8_0">
              {(id) => (
                <Input
                  id={id}
                  value={quantization}
                  onChange={(e) => setQuantization(e.target.value)}
                  className="font-mono text-xs"
                />
              )}
            </Field>
          )}
          <Button type="submit" loading={mutation.isPending}>
            <PackageOpen className="h-4 w-4" aria-hidden />
            Export
          </Button>
        </form>
      </CardBody>
    </Card>
  )
}

function DownloadPanel({ model }: { model: ModelArtifact }) {
  const downloads: { format: ArtifactFormat; uri: string | null; label: string }[] = [
    { format: 'gguf', uri: model.gguf_uri, label: 'GGUF (Ollama / llama.cpp)' },
    { format: 'safetensors', uri: model.safetensors_uri, label: 'SafeTensors (merged weights)' },
  ]
  const available = downloads.filter((d) => d.uri)

  return (
    <Card>
      <CardHeader title="Downloads" description="Stream exported artifacts from MinIO." />
      <CardBody>
        {available.length === 0 ? (
          <p className="py-4 text-center text-xs text-body-muted">Run an export to enable downloads.</p>
        ) : (
          <ul className="space-y-2">
            {available.map((d) => (
              <li key={d.format}>
                <a
                  href={modelDownloadUrl(model.id, d.format)}
                  download
                  className="flex cursor-pointer items-center justify-between rounded-md border border-line/60 bg-bg p-3 text-sm transition-colors hover:border-body-muted"
                >
                  <span className="text-body">{d.label}</span>
                  <Download className="h-4 w-4 text-body-muted" aria-hidden />
                </a>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  )
}
