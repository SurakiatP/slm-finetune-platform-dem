import { useState } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { Loader2, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { useLanguage } from '@/i18n/LanguageContext'
import { useAuth } from '@/contexts/AuthContext'
import { manageAccount, register, safeRedirectPath, signIn } from '@/auth/keycloak'

export function AuthEntry({ mode = 'login' }: { mode?: 'login' | 'signup' | 'recovery' }) {
  const { t, language } = useLanguage()
  const { user } = useAuth()
  const location = useLocation()
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const from = (location.state as { from?: { pathname?: string; search?: string; hash?: string } } | null)?.from
  const destination = safeRedirectPath((from?.pathname || '/dashboard') + (from?.search || '') + (from?.hash || ''))
  if (user && mode !== 'recovery') return <Navigate to={destination} replace />
  const submit = async () => {
    if (busy) return
    setBusy(true)
    setFailed(false)
    try {
      if (mode === 'signup') await register(destination, language)
      else if (mode === 'recovery' && user) await manageAccount()
      else await signIn(destination, language)
    } catch { setFailed(true); setBusy(false) }
  }
  return (
    <div className="min-h-screen flex items-center justify-center bg-secondary/20 p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <Link to="/" className="inline-flex items-center justify-center gap-2 mb-4">
            <Zap aria-hidden="true" className="h-6 w-6 text-primary" /><span className="font-semibold text-lg">TuneLab</span>
          </Link>
          <CardTitle>{mode === 'signup' ? t('oidc.signup') : mode === 'recovery' ? t('oidc.recoveryTitle') : t('oidc.login')}</CardTitle>
          <CardDescription>{mode === 'recovery' ? t(user ? 'oidc.accountDescription' : 'oidc.recoveryDescription') : t('oidc.description')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {failed && <p role="alert" className="text-sm text-destructive">{t('oidc.unavailable')}</p>}
          <Button className="w-full relative" disabled={busy} aria-busy={busy} onClick={() => void submit()}>
            {busy && <Loader2 aria-hidden="true" className="absolute left-4 h-4 w-4 animate-spin" />}
            {mode === 'signup' ? t('oidc.signup') : mode === 'recovery' && user ? t('oidc.manageAccount') : t('oidc.continue')}
          </Button>
        </CardContent>
        <CardFooter className="flex flex-wrap justify-center gap-4 text-sm">
          <Link className="text-primary hover:underline" to={mode === 'signup' ? '/login' : '/signup'} state={location.state}>
            {mode === 'signup' ? t('oidc.login') : t('oidc.signup')}
          </Link>
          {mode === 'login' && <Link className="text-muted-foreground hover:underline" to="/forgot-password">{t('oidc.recoveryTitle')}</Link>}
        </CardFooter>
      </Card>
    </div>
  )
}
