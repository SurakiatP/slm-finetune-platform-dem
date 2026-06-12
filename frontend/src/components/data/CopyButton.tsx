import { Check, Copy } from 'lucide-react'
import { useState } from 'react'

export function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)

  return (
    <button
      type="button"
      aria-label={copied ? 'Copied' : label}
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1500)
        })
      }}
      className="cursor-pointer rounded p-1 text-body-muted transition-colors hover:bg-surface-2 hover:text-body"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-accent" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
    </button>
  )
}
