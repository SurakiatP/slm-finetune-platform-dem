import { api } from '@/api/client'
import type {
  ChatCompletionRequest,
  ChatCompletionResponse,
  ModelDescriptorList,
} from '@/api/types'

const BASE = '/api/v1/inference'

export function chatCompletions(body: ChatCompletionRequest): Promise<ChatCompletionResponse> {
  return api.post(`${BASE}/chat/completions`, body)
}

export function listInferenceModels(): Promise<ModelDescriptorList> {
  return api.get(`${BASE}/models`)
}
