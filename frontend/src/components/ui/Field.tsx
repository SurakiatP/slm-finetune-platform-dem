import { useId, type ReactNode } from 'react'

import { cn } from '@/lib/cn'

interface FieldProps {
  label: string
  required?: boolean
  error?: string
  hint?: string
  className?: string
  /** Render prop receives the generated input id so label/aria wiring is automatic. */
  children: (id: string, describedBy: string | undefined) => ReactNode
}

/** Labelled form field with hint + inline error below the control. */
export function Field({ label, required, error, hint, className, children }: FieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined

  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={id} className="block text-xs font-medium text-body-muted">
        {label}
        {required && <span className="ml-0.5 text-danger" aria-hidden>*</span>}
      </label>
      {children(id, errorId ?? hintId)}
      {hint && !error && (
        <p id={hintId} className="text-xs text-body-muted/80">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  )
}
