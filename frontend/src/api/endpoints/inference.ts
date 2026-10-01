import { api } from '@/api/client'
import type {
  ChatCompletionRequest,
  ChatCompletionResponse,
  ChatCompletionUsage,
  CompletionRequest,
  CompletionResponse,
  ModelDescriptorList,
} from '@/api/types'

const BASE = '/api/v1/inference'

export function chatCompletions(body: ChatCompletionRequest): Promise<ChatCompletionResponse> {
  return api.post(`${BASE}/chat/completions`, body)
}

export async function streamChatCompletions(
  body: ChatCompletionRequest,
  onDelta: (text: string) => void,
  signal: AbortSignal,
): Promise<ChatCompletionUsage | null> {
  const response = await api.postStream(`${BASE}/chat/completions`, { ...body, stream: true }, signal)
  if (!response.body) throw new Error('The inference stream is unavailable.')
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  let usage: ChatCompletionUsage | null = null
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) throw new Error('The inference stream ended before completion.')
      buffer = (buffer + value).replace(/\r\n/g, '\n')
      let boundary: number
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const event = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = event.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n')
        if (!data) continue
        if (data === '[DONE]') return usage
        const chunk = JSON.parse(data) as { error?: { message: string }; choices?: Array<{ delta?: { content?: string } }>; usage?: ChatCompletionUsage }
        if (chunk.error) throw new Error(chunk.error.message)
        if (chunk.usage) usage = chunk.usage
        const text = chunk.choices?.[0]?.delta?.content
        if (text) onDelta(text)
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export function completions(body: CompletionRequest): Promise<CompletionResponse> {
  return api.post(`${BASE}/completions`, body)
}

export function listInferenceModels(): Promise<ModelDescriptorList> {
  return api.get(`${BASE}/models`)
}
