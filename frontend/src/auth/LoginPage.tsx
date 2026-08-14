import { Cpu } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { supabase } from '@/auth/supabase'
import { Button } from '@/components/ui/Button'
import { Field } from '@/components/ui/Field'
import { Input } from '@/components/ui/Input'

/**
 * Email/password sign-in against the Supabase project configured via
 * VITE_SUPABASE_*. Rendered only when auth is enabled and no session
 * exists — the AuthProvider's onAuthStateChange swaps the app in the
 * moment sign-in succeeds, so this page never navigates anywhere itself.
 */
export function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!supabase || submitting) return
    setSubmitting(true)
    setError(null)
    const { error: signInError } = await supabase.auth.signInWithPassword({ email, password })
    if (signInError) {
      setError(signInError.message)
      setSubmitting(false)
    }
    // On success the AuthProvider unmounts this page; nothing to do here.
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg p-4">
      <div className="w-full max-w-sm rounded-lg border border-line/60 bg-surface p-6">
        <div className="mb-6 flex items-center gap-2">
          <Cpu className="h-5 w-5 text-accent" aria-hidden />
          <span className="font-mono text-sm font-semibold">SLM Platform</span>
        </div>
        <form onSubmit={(e) => void onSubmit(e)} className="space-y-4">
          <Field label="Email" required>
            {(id, describedBy) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            )}
          </Field>
          <Field label="Password" required error={error ?? undefined}>
            {(id, describedBy) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            )}
          </Field>
          <Button type="submit" loading={submitting} className="w-full">
            Sign in
          </Button>
        </form>
      </div>
    </main>
  )
}
