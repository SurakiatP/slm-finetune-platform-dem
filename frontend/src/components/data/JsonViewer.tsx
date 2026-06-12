import { ChevronDown, ChevronRight } from 'lucide-react'
import { useState } from 'react'

import { CopyButton } from '@/components/data/CopyButton'

interface JsonViewerProps {
  data: unknown
  title?: string
  defaultOpen?: boolean
}

/** Collapsible pretty-printed JSON block (config_json, best_params, metadata…). */
export function JsonViewer({ data, title = 'JSON', defaultOpen = false }: JsonViewerProps) {
  const [open, setOpen] = useState(defaultOpen)
  const text = JSON.stringify(data, null, 2)

  return (
    <div className="rounded-md border border-line/60 bg-bg">
      <div className="flex items-center justify-between px-3 py-1.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-body-muted transition-colors hover:text-body"
        >
          {open ? <ChevronDown className="h-3.5 w-3.5" aria-hidden /> : <ChevronRight className="h-3.5 w-3.5" aria-hidden />}
          {title}
        </button>
        <CopyButton value={text} label={`Copy ${title}`} />
      </div>
      {open && (
        <pre className="scrollbar-thin max-h-80 overflow-auto border-t border-line/60 px-3 py-2 font-mono text-xs leading-relaxed text-body-muted">
          {text}
        </pre>
      )}
    </div>
  )
}
