import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthEntry } from './AuthEntry'

const mocks = vi.hoisted(() => ({ signIn: vi.fn(), register: vi.fn(), manageAccount: vi.fn(), user: null as null | { id: string } }))
vi.mock('@/auth/keycloak', () => ({ signIn: mocks.signIn, register: mocks.register, manageAccount: mocks.manageAccount, safeRedirectPath: (path: string) => path }))
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: mocks.user }) }))
vi.mock('@/i18n/LanguageContext', () => ({ useLanguage: () => ({ language: 'th', t: (key: string) => key }) }))

function show(mode: 'login' | 'signup' | 'recovery' = 'login') {
  return render(<MemoryRouter initialEntries={[{ pathname: '/login', state: { from: { pathname: '/projects', search: '?page=2', hash: '#recent' } } }]}>
    <Routes><Route path="/login" element={<AuthEntry mode={mode} />} /><Route path="/projects" element={<div>projects destination</div>} /></Routes>
  </MemoryRouter>)
}
describe('OIDC account entry', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.user = null; mocks.signIn.mockResolvedValue(undefined); mocks.register.mockResolvedValue(undefined) })
  it('preserves destination and locale and prevents repeated login', async () => {
    show()
    const button = screen.getByRole('button', { name: 'oidc.continue' })
    fireEvent.click(button)
    fireEvent.click(button)
    await waitFor(() => expect(mocks.signIn).toHaveBeenCalledExactlyOnceWith('/projects?page=2#recent', 'th'))
    expect(button).toBeDisabled()
  })
  it('delegates registration to the provider', async () => {
    show('signup')
    fireEvent.click(screen.getByRole('button', { name: 'oidc.signup' }))
    await waitFor(() => expect(mocks.register).toHaveBeenCalledWith('/projects?page=2#recent', 'th'))
  })
  it('shows an inline error and permits retry when redirect fails', async () => {
    mocks.signIn.mockRejectedValueOnce(new Error('network'))
    show()
    fireEvent.click(screen.getByRole('button', { name: 'oidc.continue' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('oidc.unavailable')
    fireEvent.click(screen.getByRole('button', { name: 'oidc.continue' }))
    await waitFor(() => expect(mocks.signIn).toHaveBeenCalledTimes(2))
  })
  it('returns an already authenticated user to the requested destination', () => {
    mocks.user = { id: 'user' }
    show()
    expect(screen.getByText('projects destination')).toBeInTheDocument()
    expect(mocks.signIn).not.toHaveBeenCalled()
  })
  it('explains password recovery without pretending an email was sent', () => {
    show('recovery')
    expect(screen.getByText('oidc.recoveryDescription')).toBeInTheDocument()
    expect(mocks.signIn).not.toHaveBeenCalled()
  })
  it('opens account settings for a signed-in user requesting password recovery', async () => {
    mocks.user = { id: 'user' }
    mocks.manageAccount.mockResolvedValue(undefined)
    show('recovery')
    fireEvent.click(screen.getByRole('button', { name: 'oidc.manageAccount' }))
    await waitFor(() => expect(mocks.manageAccount).toHaveBeenCalledTimes(1))
    expect(mocks.signIn).not.toHaveBeenCalled()
  })

})
