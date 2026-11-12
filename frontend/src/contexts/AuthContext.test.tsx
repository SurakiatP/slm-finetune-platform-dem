import { act, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthState } from '@/auth/keycloak'
import { AuthProvider, useAuth } from './AuthContext'

const mocks = vi.hoisted(() => ({ initAuth: vi.fn(), getAuthState: vi.fn(), subscribeAuth: vi.fn(), signOut: vi.fn() }))
vi.mock('@/auth/keycloak', () => mocks)
vi.mock('@/i18n/LanguageContext', () => ({ useLanguage: () => ({ t: (key: string) => key }) }))
let listener: (state: AuthState) => void
const signedIn: AuthState = { user: { id: 'user-a', email: 'a@example.test' }, profile: null }
function Child() { return <div>{useAuth().user?.id || 'signed out'}</div> }
function show(client = new QueryClient()) {
  return render(<QueryClientProvider client={client}><AuthProvider><Child /></AuthProvider></QueryClientProvider>)
}
beforeEach(() => {
  vi.clearAllMocks()
  mocks.getAuthState.mockReturnValue({ user: null, profile: null })
  mocks.initAuth.mockResolvedValue(false)
  mocks.subscribeAuth.mockImplementation((callback: typeof listener) => { listener = callback; return vi.fn() })
})
describe('AuthProvider', () => {
  it('keeps router children unmounted until the OIDC callback is processed', async () => {
    let resolve!: (value: boolean) => void
    mocks.initAuth.mockReturnValue(new Promise<boolean>(done => { resolve = done }))
    show()
    expect(screen.getByRole('status')).toHaveTextContent('oidc.loading')
    expect(screen.queryByText('signed out')).not.toBeInTheDocument()
    await act(async () => { resolve(false) })
    expect(await screen.findByText('signed out')).toBeInTheDocument()
  })
  it('fails closed with visible recovery when initialization fails', async () => {
    mocks.initAuth.mockRejectedValue(new Error('offline'))
    show()
    expect(await screen.findByRole('alert')).toHaveTextContent('oidc.unavailable')
    expect(screen.getByRole('button', { name: 'security.retry' })).toBeEnabled()
    expect(screen.queryByText('signed out')).not.toBeInTheDocument()
  })
  it('clears cached private data when the identity changes or logs out', async () => {
    const client = new QueryClient()
    show(client)
    await screen.findByText('signed out')
    client.setQueryData(['projects'], ['stale data'])
    act(() => listener(signedIn))
    expect(client.getQueryData(['projects'])).toBeUndefined()
    client.setQueryData(['projects'], ['user-a data'])
    act(() => listener(signedIn))
    expect(client.getQueryData(['projects'])).toEqual(['user-a data'])
    act(() => listener({ user: null, profile: null }))
    await waitFor(() => expect(client.getQueryData(['projects'])).toBeUndefined())
  })
})
