import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiKeyManager } from './ApiKeyManager'

const mocks = vi.hoisted(() => ({ create: vi.fn(), revoke: vi.fn(), keys: { items: [{ id: 'k1', name: 'Production', prefix: 'sk-slm-ab', last4: '1234', status: 'active', created_at: '2026-09-01', last_used_at: null }], total: 1, limit: 100, offset: 0 } }))
vi.mock('@/hooks/queries', () => ({
  useApiKeys: () => ({ data: mocks.keys, isLoading: false, isError: false }),
  useDeployments: () => ({ data: { items: [], total: 0 }, isLoading: false }),
  useCreateApiKey: () => ({ mutate: mocks.create, isPending: false, error: null }),
  useRevokeApiKey: () => ({ mutate: mocks.revoke, isPending: false, error: null }),
}))

describe('ApiKeyManager', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows only the masked backend key', () => {
    render(<ApiKeyManager />)
    expect(screen.getByText('Production')).toBeInTheDocument()
    expect(screen.getByText('sk-slm-ab••••1234')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /show real key/i })).not.toBeInTheDocument()
  })

  it('shows a newly created secret once and clears it when dismissed', () => {
    mocks.create.mockImplementation((_name, options) => options.onSuccess({ key: 'sk-slm-secret' }))
    render(<ApiKeyManager />)
    fireEvent.change(screen.getByPlaceholderText('e.g. local evaluation script'), { target: { value: 'Local test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create key' }))
    expect(mocks.create).toHaveBeenCalledWith('Local test', expect.any(Object))
    expect(screen.getByText('sk-slm-secret')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'I saved it' }))
    expect(screen.queryByText('sk-slm-secret')).not.toBeInTheDocument()
  })
})
