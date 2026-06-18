import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { uploadSeedDataset } from '@/api/endpoints/datasets'
import type { Project } from '@/api/types'
import { FileDropzone } from '@/components/data/FileDropzone'
import { JsonlPreview } from '@/components/data/JsonlPreview'
import { TaskTypeBadge } from '@/components/data/TaskTypeBadge'
import { Button } from '@/components/ui/Button'
import { Field } from '@/components/ui/Field'
import { Input } from '@/components/ui/Input'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/toast-context'
import { queryKeys } from '@/hooks/queries'
import { parseJsonlFile } from '@/lib/jsonl'

interface UploadSeedModalProps {
  project: Project
  open: boolean
  onClose: () => void
}

const MAX_JSON_BYTES = 10 * 1024 * 1024 // backend rejects > 10 MiB
const MAX_PDF_BYTES = 25 * 1024 * 1024 // MAX_SEED_PDF_BYTES (QA only)

const isPdf = (f: File) => /\.pdf$/i.test(f.name) || f.type === 'application/pdf'

export function UploadSeedModal({ project, open, onClose }: UploadSeedModalProps) {
  const [file, setFile] = useState<File | null>(null)
  const [rows, setRows] = useState<Record<string, unknown>[] | null>(null)
  const [parseError, setParseError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const queryClient = useQueryClient()
  const toast = useToast()

  // PDF seeds are a QA-only feature — the backend extracts QA pairs from the PDF
  // during SDG (with_seed mode) via a multimodal model.
  const pdfAllowed = project.task_type === 'qa'
  const fileIsPdf = file ? isPdf(file) : false

  const reset = () => {
    setFile(null)
    setRows(null)
    setParseError(null)
    setName('')
  }

  const onFileChange = (f: File | null) => {
    setFile(f)
    setRows(null)
    setParseError(null)
    if (!f) return
    if (isPdf(f)) {
      if (!pdfAllowed) {
        setParseError('PDF seeds are only supported for QA projects.')
      } else if (f.size > MAX_PDF_BYTES) {
        setParseError('PDF exceeds the 25 MiB upload limit.')
      }
      return // PDFs are not row-parsed in the browser
    }
    parseJsonlFile(f)
      .then(setRows)
      .catch((err: Error) => setParseError(err.message))
  }

  const mutation = useMutation({
    mutationFn: () =>
      uploadSeedDataset({
        project_id: project.id,
        task_type: project.task_type,
        file: file!,
        name: name.trim() || undefined,
      }),
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets(project.id) })
      const skipped = res.invalid_rows.length
      toast.success(
        `Seed dataset uploaded — ${res.num_samples} rows${skipped > 0 ? `, ${skipped} invalid skipped` : ''}`,
      )
      reset()
      onClose()
    },
    onError: (err) => toast.error(err.message),
  })

  return (
    <Modal
      open={open}
      onClose={() => {
        reset()
        onClose()
      }}
      title="Upload seed dataset"
      description={
        pdfAllowed
          ? 'Upload .jsonl/.json rows in the QA format, or a .pdf to extract QA pairs from during SDG.'
          : `Rows must match the ${project.task_type} format.`
      }
      widthClass="max-w-2xl"
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (file && !parseError) mutation.mutate()
        }}
      >
        <div className="flex items-center gap-2 text-xs text-body-muted">
          Task type: <TaskTypeBadge taskType={project.task_type} />
        </div>

        <FileDropzone
          file={file}
          onFileChange={onFileChange}
          rowCount={fileIsPdf ? null : rows?.length}
          error={parseError}
          accept={
            pdfAllowed
              ? '.jsonl,.json,application/json,.pdf,application/pdf'
              : '.jsonl,.json,application/json'
          }
          maxSizeBytes={fileIsPdf ? MAX_PDF_BYTES : MAX_JSON_BYTES}
          formatsHint={
            pdfAllowed ? (
              <>
                Drop a <span className="font-mono">.pdf</span> or{' '}
                <span className="font-mono">.jsonl</span> / <span className="font-mono">.json</span>{' '}
                file or click to browse
              </>
            ) : undefined
          }
          sizeHint={pdfAllowed ? 'PDF ≤ 25 MiB · JSON/JSONL ≤ 10 MiB' : undefined}
        />

        {fileIsPdf && !parseError && (
          <p className="rounded-md border border-line/60 bg-surface p-3 text-xs text-body-muted">
            PDF seeds have no row preview. The document is sent to a multimodal model during SDG
            (<span className="font-mono">with_seed</span> mode) to generate QA pairs from its
            content. Max 25 MiB / 100 pages.
          </p>
        )}

        {!fileIsPdf && rows && rows.length > 0 && (
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-body-muted">
              Preview ({Math.min(rows.length, 10)} of {rows.length} rows)
            </h4>
            <JsonlPreview taskType={project.task_type} samples={rows.slice(0, 10)} />
          </div>
        )}

        <Field label="Dataset name" hint="Defaults to the file name.">
          {(id) => (
            <Input
              id={id}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={file?.name.replace(/\.(jsonl|json|pdf)$/i, '') ?? 'seed-v1'}
            />
          )}
        </Field>

        <div className="flex justify-end gap-2 pt-2">
          <Button
            variant="secondary"
            onClick={() => {
              reset()
              onClose()
            }}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={!file || !!parseError} loading={mutation.isPending}>
            Upload
          </Button>
        </div>
      </form>
    </Modal>
  )
}
