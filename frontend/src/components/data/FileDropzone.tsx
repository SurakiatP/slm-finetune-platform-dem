import { FileJson, Upload, X } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'

import { cn } from '@/lib/cn'
import { formatBytes } from '@/lib/format'

const MAX_SIZE_BYTES = 10 * 1024 * 1024 // backend rejects > 10 MiB with 413

interface FileDropzoneProps {
  file: File | null
  onFileChange: (file: File | null) => void
  /** Parsed row count shown after selection (caller parses the file). */
  rowCount?: number | null
  error?: string | null
}

export function FileDropzone({ file, onFileChange, rowCount, error }: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)

  const pick = (f: File | undefined) => {
    if (!f) return
    onFileChange(f)
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    pick(e.dataTransfer.files[0])
  }

  if (file) {
    const tooBig = file.size > MAX_SIZE_BYTES
    return (
      <div
        className={cn(
          'flex items-center justify-between rounded-lg border p-3',
          tooBig || error ? 'border-danger/40 bg-danger-muted' : 'border-line/60 bg-bg',
        )}
      >
        <div className="flex min-w-0 items-center gap-2.5">
          <FileJson className="h-5 w-5 shrink-0 text-body-muted" aria-hidden />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-body">{file.name}</p>
            <p className="font-mono text-xs text-body-muted">
              {formatBytes(file.size)}
              {rowCount !== null && rowCount !== undefined && ` · ${rowCount} rows`}
            </p>
            {tooBig && <p className="text-xs text-danger">File exceeds the 10 MiB upload limit</p>}
            {error && <p className="text-xs text-danger">{error}</p>}
          </div>
        </div>
        <button
          type="button"
          aria-label="Remove file"
          onClick={() => onFileChange(null)}
          className="cursor-pointer rounded p-1 text-body-muted transition-colors hover:bg-surface-2 hover:text-body"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault()
        setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      className={cn(
        'flex w-full cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center transition-colors',
        dragOver ? 'border-accent bg-accent-muted' : 'border-line/60 hover:border-body-muted',
      )}
    >
      <Upload className="h-6 w-6 text-body-muted" aria-hidden />
      <span className="text-sm text-body">
        Drop a <span className="font-mono">.jsonl</span> / <span className="font-mono">.json</span> file or click to browse
      </span>
      <span className="text-xs text-body-muted">Max 10 MiB · 5–50 rows recommended for seeds</span>
      <input
        ref={inputRef}
        type="file"
        accept=".jsonl,.json,application/json"
        className="sr-only"
        aria-label="Seed dataset file"
        onChange={(e) => pick(e.target.files?.[0])}
      />
    </button>
  )
}
