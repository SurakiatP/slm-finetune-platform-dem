import { useMutation } from '@tanstack/react-query'
import { Bot, Eraser, SendHorizonal, Settings2, User } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { chatCompletions } from '@/api/endpoints/inference'
import type { ChatCompletionUsage, ChatMessage } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Field } from '@/components/ui/Field'
import { Input, Textarea } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { useInferenceModels } from '@/hooks/queries'
import { cn } from '@/lib/cn'

export default function PlaygroundPage() {
  const [searchParams] = useSearchParams()
  const { data: models, isLoading: modelsLoading } = useInferenceModels()
  const [model, setModel] = useState(searchParams.get('model') ?? '')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [usage, setUsage] = useState<ChatCompletionUsage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [paramsOpen, setParamsOpen] = useState(false)
  const [temperature, setTemperature] = useState('0.7')
  const [topP, setTopP] = useState('1')
  const [maxTokens, setMaxTokens] = useState('')
  const [seed, setSeed] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  // Fall back to the first served model until the user picks one explicitly.
  const effectiveModel = model || models?.data[0]?.id || ''

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  const mutation = useMutation({
    mutationFn: (thread: ChatMessage[]) =>
      chatCompletions({
        model: effectiveModel,
        messages: systemPrompt.trim()
          ? [{ role: 'system', content: systemPrompt.trim() }, ...thread]
          : thread,
        temperature: Number(temperature),
        top_p: Number(topP),
        max_tokens: maxTokens ? Number(maxTokens) : null,
        seed: seed ? Number(seed) : null,
        stream: false, // backend rejects stream=true
      }),
    onSuccess: (res) => {
      const reply = res.choices[0]?.message
      if (reply) setMessages((prev) => [...prev, reply])
      setUsage(res.usage)
    },
    onError: (err) => setError(err.message),
  })

  const send = () => {
    const content = input.trim()
    if (!content || !effectiveModel || mutation.isPending) return
    setError(null)
    const thread = [...messages, { role: 'user' as const, content }]
    setMessages(thread)
    setInput('')
    mutation.mutate(thread)
  }

  const noModels = !modelsLoading && (models?.data.length ?? 0) === 0

  return (
    <div className="flex h-[calc(100dvh-3rem)] flex-col">
      <PageHeader
        title="Playground"
        description="Chat with a fine-tuned model served by Ollama. Responses are non-streaming."
        actions={
          <div className="flex items-center gap-2">
            <Select
              value={effectiveModel}
              onChange={(e) => setModel(e.target.value)}
              className="w-64"
              aria-label="Model"
            >
              {!effectiveModel && <option value="">Select model…</option>}
              {(models?.data ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id}
                </option>
              ))}
            </Select>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setParamsOpen((v) => !v)}
              aria-expanded={paramsOpen}
            >
              <Settings2 className="h-3.5 w-3.5" aria-hidden />
              Params
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setMessages([])
                setUsage(null)
                setError(null)
              }}
              disabled={messages.length === 0}
            >
              <Eraser className="h-3.5 w-3.5" aria-hidden />
              Clear
            </Button>
          </div>
        }
      />

      {paramsOpen && (
        <div className="mb-4 grid gap-4 rounded-lg border border-line/60 bg-surface p-4 sm:grid-cols-4">
          <Field label="Temperature" hint="0–2">
            {(id) => (
              <Input id={id} type="number" min={0} max={2} step={0.1} value={temperature} onChange={(e) => setTemperature(e.target.value)} />
            )}
          </Field>
          <Field label="Top-p" hint="0–1">
            {(id) => (
              <Input id={id} type="number" min={0} max={1} step={0.05} value={topP} onChange={(e) => setTopP(e.target.value)} />
            )}
          </Field>
          <Field label="Max tokens" hint="blank = model default">
            {(id) => (
              <Input id={id} type="number" min={1} max={8192} value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} />
            )}
          </Field>
          <Field label="Seed" hint="for reproducibility">
            {(id) => <Input id={id} type="number" value={seed} onChange={(e) => setSeed(e.target.value)} />}
          </Field>
          <div className="sm:col-span-4">
            <Field label="System prompt">
              {(id) => (
                <Textarea
                  id={id}
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  placeholder="Optional system instructions sent before the conversation."
                  className="min-h-16"
                />
              )}
            </Field>
          </div>
        </div>
      )}

      <div
        ref={scrollRef}
        className="scrollbar-thin flex-1 space-y-3 overflow-y-auto rounded-lg border border-line/60 bg-surface/50 p-4"
        aria-live="polite"
      >
        {messages.length === 0 && (
          <p className="py-12 text-center text-sm text-body-muted">
            {noModels
              ? 'No models are served yet — export a model to GGUF first.'
              : 'Send a message to test the fine-tuned model.'}
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={cn('flex gap-2.5', m.role === 'user' && 'flex-row-reverse')}>
            <span
              className={cn(
                'mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full',
                m.role === 'user' ? 'bg-accent-muted text-accent' : 'bg-surface-2 text-body-muted',
              )}
              aria-hidden
            >
              {m.role === 'user' ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
            </span>
            <div
              className={cn(
                'max-w-[75%] whitespace-pre-wrap rounded-lg border px-3 py-2 text-sm leading-relaxed',
                m.role === 'user'
                  ? 'border-accent/20 bg-accent-muted text-body'
                  : 'border-line/60 bg-surface text-body',
              )}
            >
              {m.content}
            </div>
          </div>
        ))}
        {mutation.isPending && (
          <div className="flex gap-2.5">
            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-2 text-body-muted" aria-hidden>
              <Bot className="h-3.5 w-3.5" />
            </span>
            <div className="rounded-lg border border-line/60 bg-surface px-3 py-2">
              <span className="inline-flex gap-1" role="status" aria-label="Model is responding">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-body-muted [animation-delay:0ms]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-body-muted [animation-delay:120ms]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-body-muted [animation-delay:240ms]" />
              </span>
            </div>
          </div>
        )}
      </div>

      {error && (
        <p role="alert" className="mt-2 text-xs text-danger">
          {error}
        </p>
      )}

      <form
        className="mt-3 flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
      >
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          placeholder={noModels ? 'No served models available' : 'Message… (Enter to send, Shift+Enter for newline)'}
          aria-label="Message"
          disabled={noModels}
          className="min-h-12 flex-1"
        />
        <Button type="submit" disabled={!input.trim() || !effectiveModel} loading={mutation.isPending} aria-label="Send message">
          <SendHorizonal className="h-4 w-4" aria-hidden />
        </Button>
      </form>

      {usage && (
        <p className="mt-2 text-right font-mono text-[11px] text-body-muted">
          prompt {usage.prompt_tokens} · completion {usage.completion_tokens} · total {usage.total_tokens} tokens
        </p>
      )}
    </div>
  )
}
