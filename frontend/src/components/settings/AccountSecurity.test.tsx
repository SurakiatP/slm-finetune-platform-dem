import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AccountSecurity } from './AccountSecurity'
const mocks = vi.hoisted(() => ({ manageAccount: vi.fn() }))
vi.mock('@/auth/keycloak', () => mocks)
vi.mock('@/i18n/LanguageContext', () => ({ useLanguage: () => ({ t: (key: string) => key }) }))
describe('AccountSecurity', () => {
  beforeEach(() => vi.clearAllMocks())
  it('opens the provider account console and prevents duplicate actions', async () => {
    mocks.manageAccount.mockResolvedValue(undefined)
    render(<AccountSecurity />)
    const button = screen.getByRole('button', { name: 'oidc.manageAccount' })
    fireEvent.click(button)
    fireEvent.click(button)
    await waitFor(() => expect(mocks.manageAccount).toHaveBeenCalledTimes(1))
    expect(button).toBeDisabled()
  })
  it('keeps a recoverable inline error on failure', async () => {
    mocks.manageAccount.mockRejectedValueOnce(new Error('network'))
    render(<AccountSecurity />)
    fireEvent.click(screen.getByRole('button', { name: 'oidc.manageAccount' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('oidc.unavailable')
    expect(screen.getByRole('button', { name: 'oidc.manageAccount' })).toBeEnabled()
  })
})
