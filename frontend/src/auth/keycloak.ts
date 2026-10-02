import Keycloak from 'keycloak-js'

export interface AuthState {
  user: { id: string; email: string | null } | null
  profile: { id: string; user_id: string; display_name: string | null; avatar_url: string | null } | null
}

// Credentials and profiles live in Keycloak's PostgreSQL database. The browser
// uses only a public OIDC client; tokens stay in the adapter's memory.
const keycloak = new Keycloak({
  url: import.meta.env.VITE_AUTH_URL || '/auth',
  realm: import.meta.env.VITE_AUTH_REALM || 'tunelab',
  clientId: import.meta.env.VITE_AUTH_CLIENT_ID || 'tunelab-web',
})
const listeners = new Set<(state: AuthState) => void>()
let initialization: Promise<boolean> | undefined

export function getAuthState(): AuthState {
  const claims = keycloak.tokenParsed
  if (!keycloak.authenticated || !claims?.sub) return { user: null, profile: null }
  const text = (value: unknown) => typeof value === 'string' ? value : null
  return {
    user: { id: claims.sub, email: text(claims.email) },
    profile: {
      id: claims.sub,
      user_id: claims.sub,
      display_name: text(claims.name) || text(claims.preferred_username),
      avatar_url: text(claims.picture),
    },
  }
}

function emit() {
  const state = getAuthState()
  listeners.forEach(listener => listener(state))
}

export function clearLocalAuth() {
  keycloak.clearToken()
  emit()
}

keycloak.onAuthSuccess = emit
keycloak.onAuthRefreshSuccess = emit
keycloak.onAuthLogout = emit
keycloak.onAuthRefreshError = clearLocalAuth
keycloak.onTokenExpired = () => { void getAccessToken() }

export function initAuth(): Promise<boolean> {
  // Keycloak can only be initialized once. After an initialization failure the
  // UI offers a page reload, rather than retrying init on a used instance.
  initialization ??= keycloak.init({
    onLoad: 'check-sso',
    pkceMethod: 'S256',
    flow: 'standard',
    checkLoginIframe: false,
  })
  return initialization
}

export function subscribeAuth(listener: (state: AuthState) => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export async function getAccessToken(): Promise<string | null> {
  try {
    await initAuth()
    if (!keycloak.authenticated) return null
    await keycloak.updateToken(30)
    return keycloak.token ?? null
  } catch {
    clearLocalAuth()
    return null
  }
}

export function safeRedirectPath(path: string = '/dashboard'): string {
  try {
    const url = new URL(path, window.location.origin)
    if (url.origin !== window.location.origin || !path.startsWith('/') || path.startsWith('//')) return '/dashboard'
    // Auth routes should not send signed-in users back into the sign-in flow.
    if (/^\/(login|signup|mfa|forgot-password|reset-password)(\/|$)/.test(url.pathname)) return '/dashboard'
    return url.pathname + url.search + url.hash
  } catch { return '/dashboard' }
}

export async function signIn(path = '/dashboard', locale = 'en') {
  await initAuth()
  await keycloak.login({ redirectUri: window.location.origin + safeRedirectPath(path), locale })
}

export async function register(path = '/dashboard', locale = 'en') {
  await initAuth()
  await keycloak.register({ redirectUri: window.location.origin + safeRedirectPath(path), locale })
}

export async function signOut() {
  await initAuth()
  // Construct the provider logout URL before clearing the ID token hint.
  const url = keycloak.createLogoutUrl({ redirectUri: window.location.origin + '/' })
  clearLocalAuth()
  window.location.assign(url)
}

export async function manageAccount() {
  await initAuth()
  await keycloak.accountManagement()
}
