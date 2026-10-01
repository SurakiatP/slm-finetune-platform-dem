import { beforeEach, describe, expect, it, vi } from 'vitest'

const postStream = vi.hoisted(() => vi.fn())
vi.mock('@/api/client', () => ({ api: { postStream } }))
import { streamChatCompletions } from './inference'

const response = (events: string) => new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(events)); controller.close() } }), { headers: { 'Content-Type': 'text/event-stream' } })
const request = { model: 'test', messages: [{ role: 'user' as const, content: 'Hi' }] }

describe('streamChatCompletions', () => {
  beforeEach(() => postStream.mockReset())

  it('collects deltas and usage before DONE', async () => {
    postStream.mockResolvedValue(response('data: {"choices":[{"delta":{"content":"Hel"}}]}\n\ndata: {"choices":[{"delta":{"content":"lo"}}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}\n\ndata: [DONE]\n\n'))
    const onDelta = vi.fn()
    const usage = await streamChatCompletions(request, onDelta, new AbortController().signal)
    expect(onDelta.mock.calls).toEqual([['Hel'], ['lo']])
    expect(usage?.completion_tokens).toBe(2)
    expect(postStream).toHaveBeenCalledWith('/api/v1/inference/chat/completions', expect.objectContaining({ stream: true }), expect.any(AbortSignal))
  })

  it('rejects a truncated stream', async () => {
    postStream.mockResolvedValue(response('data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'))
    await expect(streamChatCompletions(request, vi.fn(), new AbortController().signal)).rejects.toThrow('ended before completion')
  })
})
