import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  }
  return { ApiError, api: { get: vi.fn(), getBlob: vi.fn() } }
})

vi.mock('@/api/client', () => ({
  ApiError: mocks.ApiError,
  api: mocks.api,
  pageQuery: () => '',
}))

import { downloadDataset } from '@/api/endpoints/datasets'

describe('downloadDataset', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('open', vi.fn())
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })

  it('opens the presigned URL when available', async () => {
    mocks.api.get.mockResolvedValue({ url: 'https://storage/x' })
    await downloadDataset('d1')
    expect(window.open).toHaveBeenCalledWith('https://storage/x', '_blank', 'noopener,noreferrer')
    expect(mocks.api.getBlob).not.toHaveBeenCalled()
  })

  it('falls back to the API-streamed download on 503', async () => {
    mocks.api.get.mockRejectedValue(new mocks.ApiError(503, 'no storage host'))
    mocks.api.getBlob.mockResolvedValue({ blob: new Blob(['{}']), filename: 'd1.jsonl' })
    await downloadDataset('d1')
    expect(mocks.api.getBlob).toHaveBeenCalledWith('/api/v1/datasets/d1/download')
    expect(window.open).not.toHaveBeenCalled()
  })

  it('rethrows other errors', async () => {
    mocks.api.get.mockRejectedValue(new mocks.ApiError(409, 'nothing stored'))
    await expect(downloadDataset('d1')).rejects.toThrow('nothing stored')
    expect(mocks.api.getBlob).not.toHaveBeenCalled()
  })
})
