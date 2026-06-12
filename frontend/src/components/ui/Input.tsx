import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from 'react'

import { cn } from '@/lib/cn'

const fieldClasses =
  'w-full rounded-md border border-line bg-bg px-3 text-sm text-body placeholder:text-body-muted/60 ' +
  'transition-colors duration-150 hover:border-body-muted focus:border-accent ' +
  'disabled:cursor-not-allowed disabled:opacity-50'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} className={cn(fieldClasses, 'h-10', className)} {...rest} />
  },
)

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return <textarea ref={ref} className={cn(fieldClasses, 'min-h-20 py-2', className)} {...rest} />
  },
)
