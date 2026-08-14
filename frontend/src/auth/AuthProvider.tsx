import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

import { authEnabled, supabase, type Session } from '@/auth/supabase'

interface AuthState {
  /** False when VITE_SUPABASE_* is unset — the app runs auth-less. */
  enabled: boolean
  /** Null while signed out (or when auth is disabled). */
  session: Session | null
  /** True until the persisted session (if any) has been restored. */
  loading: boolean
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState>({
  enabled: false,
  session: null,
  loading: false,
  signOut: async () => {},
})

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  return useContext(AuthContext)
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(authEnabled)

  useEffect(() => {
    if (!supabase) return
    let cancelled = false

    void supabase.auth.getSession().then(({ data }) => {
      if (cancelled) return
      setSession(data.session)
      setLoading(false)
    })

    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next)
      setLoading(false)
    })

    return () => {
      cancelled = true
      sub.subscription.unsubscribe()
    }
  }, [])

  const signOut = async () => {
    await supabase?.auth.signOut()
  }

  return (
    <AuthContext.Provider value={{ enabled: authEnabled, session, loading, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}
