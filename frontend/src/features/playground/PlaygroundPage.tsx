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
import { useInferenceModels, useModels } from '@/hooks/queries'
import { cn } from '@/lib/cn'

interface ServedModel {
  id: string
}

export default function PlaygroundPage() {
  const [searchParams] = useSearchParams()
  const { data: models, isLoading: modelsLoading } = useInferenceModels()
  // Artifacts across all projects, to map opaque Ollama tags (slm/<uuid8>) back
  // to the human-readable training/model name shown to the user.
  const { data: artifacts } = useModels(undefined, { limit: 200 })

  // Shared conversation input + generation params (sent to BOTH columns).
  const [systemPrompt, setSystemPrompt] = useState('')
  const [input, setInput] = useState('')
  const [paramsOpen, setParamsOpen] = useState(false)
  const [temperature, setTemperature] = useState('0.7')
  const [topP, setTopP] = useState('1')
  const [maxTokens, setMaxTokens] = useState('')
  const [seed, setSeed] = useState('')

  // Per-column model selection + independent threads.
  const [modelA, setModelA] = useState(searchParams.get('model') ?? '')
  const [modelB, setModelB] = useState('')
  const [messagesA, setMessagesA] = useState<ChatMessage[]>([])
  const [messagesB, setMessagesB] = useState<ChatMessage[]>([])
  const [usageA, setUsageA] = useState<ChatCompletionUsage | null>(null)
  const [usageB, setUsageB] = useState<ChatCompletionUsage | null>(null)
  const [errorA, setErrorA] = useState<string | null>(null)
  const [errorB, setErrorB] = useState<string | null>(null)

  const served = (models?.data ?? []) as ServedModel[]

  // Map Ollama tag -> friendly artifact name / base counterpart. Ollama reports
  // tags as "slm/<uuid8>:latest" while the artifact stores "slm/<uuid8>", so
  // match on both the raw id and the id with a trailing ":latest" stripped.
  const tagToName = new Map<string, string>()
  const tagToBase = new Map<string, string | null>()
  for (const a of artifacts?.items ?? []) {
    if (a.ollama_model_tag) {
      tagToName.set(a.ollama_model_tag, a.name)
      tagToBase.set(a.ollama_model_tag, a.base_ollama_tag)
    }
  }
  const isFineTuned = (id: string) =>
    tagToName.has(id) || tagToName.has(id.replace(/:latest$/, ''))
  const labelForModel = (id: string) => {
    const name = tagToName.get(id) ?? tagToName.get(id.replace(/:latest$/, ''))
    return name ? `${name} (${id})` : id
  }
  const baseTagFor = (id: string) =>
    tagToBase.get(id) ?? tagToBase.get(id.replace(/:latest$/, '')) ?? null

  // Defaults steer the comparison toward "fine-tuned (left) vs its actual
  // Ollama-Hub base (right)" — the worker auto-pulls that exact base after
  // export, so prefer it over an arbitrary served non-fine-tuned model
  // (which could be a different family entirely if multiple bases are served).
  const firstFineTuned = served.find((m) => isFineTuned(m.id))?.id
  const effectiveA = modelA || firstFineTuned || served[0]?.id || ''
  const expectedBaseTag = baseTagFor(effectiveA)
  const matchedBase = expectedBaseTag
    ? served.find((m) => m.id === expectedBaseTag || m.id.replace(/:latest$/, '') === expectedBaseTag)?.id
    : undefined
  const firstBase = served.find((m) => !isFineTuned(m.id))?.id
  const effectiveB = modelB || matchedBase || firstBase || served[1]?.id || served[0]?.id || ''

  const buildPayload = (model: string, thread: ChatMessage[]) => ({
    model,
    messages: systemPrompt.trim()
      ? [{ role: 'system' as const, content: systemPrompt.trim() }, ...thread]
      : thread,
    temperature: Number(temperature),
    top_p: Number(topP),
    max_tokens: maxTokens ? Number(maxTokens) : null,
    seed: seed ? Number(seed) : null,
    stream: false as const, // backend rejects stream=true
  })

  const mutationA = useMutation({
    mutationFn: (thread: ChatMessage[]) => chatCompletions(buildPayload(effectiveA, thread)),
    onSuccess: (res) => {
      const reply = res.choices[0]?.message
      if (reply) setMessagesA((prev) => [...prev, reply])
      setUsageA(res.usage)
    },
    onError: (err) => setErrorA(err.message),
  })

  const mutationB = useMutation({
    mutationFn: (thread: ChatMessage[]) => chatCompletions(buildPayload(effectiveB, thread)),
    onSuccess: (res) => {
      const reply = res.choices[0]?.message
      if (reply) setMessagesB((prev) => [...prev, reply])
      setUsageB(res.usage)
    },
    onError: (err) => setErrorB(err.message),
  })

  const busy = mutationA.isPending || mutationB.isPending
  const noModels = !modelsLoading && served.length === 0
  const hasHistory = messagesA.length > 0 || messagesB.length > 0

  const send = () => {
    const content = input.trim()
    if (!content || busy || (!effectiveA && !effectiveB)) return
    setErrorA(null)
    setErrorB(null)
    const userMsg = { role: 'user' as const, content }
    const threadA = [...messagesA, userMsg]
    const threadB = [...messagesB, userMsg]
    setMessagesA(threadA)
    setMessagesB(threadB)
    setInput('')
    if (effectiveA) mutationA.mutate(threadA)
    if (effectiveB) mutationB.mutate(threadB)
  }

  const clearAll = () => {
    setMessagesA([])
    setMessagesB([])
    setUsageA(null)
    setUsageB(null)
    setErrorA(null)
    setErrorB(null)
  }

  return (
    <div className="flex h-[calc(100dvh-3rem)] flex-col">
      <PageHeader
        title="Playground"
        description="Compare two models side by side — e.g. your fine-tuned model vs the base model. One prompt is sent to both."
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setParamsOpen((v) => !v)}
              aria-expanded={paramsOpen}
            >
              <Settings2 className="h-3.5 w-3.5" aria-hidden />
              Params
            </Button>
            <Button variant="ghost" size="sm" onClick={clearAll} disabled={!hasHistory}>
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
                  placeholder="Optional system instructions sent before the conversation (both models)."
                  className="min-h-16"
                />
              )}
            </Field>
          </div>
        </div>
      )}

      <div className="grid min-h-0 flex-1 gap-3 md:grid-cols-2">
        <ChatColumn
          label="A"
          model={effectiveA}
          onModelChange={setModelA}
          served={served}
          labelForModel={labelForModel}
          messages={messagesA}
          pending={mutationA.isPending}
          usage={usageA}
          error={errorA}
          noModels={noModels}
        />
        <ChatColumn
          label="B"
          model={effectiveB}
          onModelChange={setModelB}
          served={served}
          labelForModel={labelForModel}
          messages={messagesB}
          pending={mutationB.isPending}
          usage={usageB}
          error={errorB}
          noModels={noModels}
        />
      </div>

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
          placeholder={noModels ? 'No served models available' : 'Message both models… (Enter to send, Shift+Enter for newline)'}
          aria-label="Message"
          disabled={noModels}
          className="min-h-12 flex-1"
        />
        <Button
          type="submit"
          disabled={!input.trim() || (!effectiveA && !effectiveB)}
          loading={busy}
          aria-label="Send message to both models"
        >
          <SendHorizonal className="h-4 w-4" aria-hidden />
        </Button>
      </form>
    </div>
  )
}

// --- One comparison column ---------------------------------------------------

function ChatColumn({
  label,
  model,
  onModelChange,
  served,
  labelForModel,
  messages,
  pending,
  usage,
  error,
  noModels,
}: {
  label: string
  model: string
  onModelChange: (id: string) => void
  served: ServedModel[]
  labelForModel: (id: string) => string
  messages: ChatMessage[]
  pending: boolean
  usage: ChatCompletionUsage | null
  error: string | null
  noModels: boolean
}) {
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, pending])

  return (
    <div className="flex min-h-0 flex-col rounded-lg border border-line/60 bg-surface/50">
      <div className="flex items-center gap-2 border-b border-line/60 p-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-2 text-xs font-semibold text-body-muted">
          {label}
        </span>
        <Select
          value={model}
          onChange={(e) => onModelChange(e.target.value)}
          className="flex-1 text-xs"
          aria-label={`Model ${label}`}
        >
          {!model && <option value="">Select model…</option>}
          {served.map((m) => (
            <option key={m.id} value={m.id}>
              {labelForModel(m.id)}
            </option>
          ))}
        </Select>
      </div>

      <div ref={scrollRef} className="scrollbar-thin flex-1 space-y-3 overflow-y-auto p-4" aria-live="polite">
        {messages.length === 0 && (
          <p className="py-12 text-center text-sm text-body-muted">
            {noModels ? 'No models served — export a model to GGUF first.' : 'Send a message to compare.'}
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
                'max-w-[80%] whitespace-pre-wrap rounded-lg border px-3 py-2 text-sm leading-relaxed',
                m.role === 'user'
                  ? 'border-accent/20 bg-accent-muted text-body'
                  : 'border-line/60 bg-surface text-body',
              )}
            >
              {m.content}
            </div>
          </div>
        ))}
        {pending && (
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
        <p role="alert" className="border-t border-line/60 px-3 py-2 text-xs text-danger">
          {error}
        </p>
      )}
      {usage && (
        <p className="border-t border-line/60 px-3 py-1.5 text-right font-mono text-[11px] text-body-muted">
          prompt {usage.prompt_tokens} · completion {usage.completion_tokens} · total {usage.total_tokens}
        </p>
      )}
    </div>
  )
}
