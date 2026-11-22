import { beforeEach, describe, expect, it, vi } from 'vitest'
const client = vi.hoisted(() => ({
  init: vi.fn(), updateToken: vi.fn(), login: vi.fn(), register: vi.fn(), accountManagement: vi.fn(), clearToken: vi.fn(),
  authenticated: false, token: undefined as string | undefined,
  tokenParsed: undefined as Record<string, unknown> | undefined,
}))
vi.mock('keycloak-js', () => ({ default: vi.fn(function () { return client }) }))
beforeEach(() => {
  vi.resetModules(); vi.clearAllMocks()
  client.authenticated = false; client.token = undefined; client.tokenParsed = undefined
  client.init.mockResolvedValue(false); client.updateToken.mockResolvedValue(false)
  client.clearToken.mockImplementation(() => { client.authenticated = false; client.token = undefined; client.tokenParsed = undefined })
})
describe('OIDC session boundary', () => {
  it('initializes once with PKCE and does not issue password grants', async () => {
    const auth = await import('./keycloak')
    await Promise.all([auth.initAuth(), auth.initAuth()])
    expect(client.init).toHaveBeenCalledTimes(1)
    expect(client.init).toHaveBeenCalledWith(expect.objectContaining({ pkceMethod: 'S256', flow: 'standard', onLoad: 'check-sso' }))
  })
  it('refreshes before returning a token and maps provider profile claims', async () => {
    const auth = await import('./keycloak')
    client.authenticated = true; client.token = 'fresh-jwt'; client.tokenParsed = { sub: 'id', email: 'a@example.com', name: 'Alice' }
    expect(await auth.getAccessToken()).toBe('fresh-jwt')
    expect(client.updateToken).toHaveBeenCalledWith(30)
    expect(auth.getAuthState()).toMatchObject({ user: { id: 'id' }, profile: { display_name: 'Alice' } })
  })
  it('clears stale identity and informs subscribers when refresh fails', async () => {
    const auth = await import('./keycloak')
    client.authenticated = true; client.token = 'expired'; client.tokenParsed = { sub: 'id' }
    client.updateToken.mockRejectedValueOnce(new Error('expired refresh token'))
    const listener = vi.fn(); const unsubscribe = auth.subscribeAuth(listener)
    expect(await auth.getAccessToken()).toBeNull()
    expect(client.clearToken).toHaveBeenCalled()
    expect(listener).toHaveBeenCalledWith({ user: null, profile: null })
    unsubscribe(); listener.mockClear(); auth.clearLocalAuth(); expect(listener).not.toHaveBeenCalled()
  })
  it('does not allow an external callback or a callback loop', async () => {
    const { safeRedirectPath } = await import('./keycloak')
    for (const path of ['https://evil.test/', '//evil.test/', '/\\evil.test/', '/login', '/mfa']) {
      expect(safeRedirectPath(path)).toBe('/dashboard')
    }
    expect(safeRedirectPath('/projects?page=2#recent')).toBe('/projects?page=2#recent')
  })
  it('keeps unauthenticated requests tokenless', async () => {
    const auth = await import('./keycloak')
    expect(await auth.getAccessToken()).toBeNull()
    expect(client.updateToken).not.toHaveBeenCalled()
  })
  it('propagates initialization failure rather than bypassing authentication', async () => {
    client.init.mockRejectedValue(new Error('offline'))
    const auth = await import('./keycloak')
    await expect(auth.signIn()).rejects.toThrow('offline')
    expect(client.login).not.toHaveBeenCalled()
    expect(await auth.getAccessToken()).toBeNull()
  })
})
