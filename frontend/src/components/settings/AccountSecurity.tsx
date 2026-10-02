import { useState } from 'react'
import { Loader2, Shield } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useLanguage } from '@/i18n/LanguageContext'
import { manageAccount } from '@/auth/keycloak'

export function AccountSecurity() {
  const { t } = useLanguage()
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const openAccount = async () => {
    if (busy) return
    setBusy(true)
    setFailed(false)
    try { await manageAccount() } catch { setFailed(true); setBusy(false) }
  }
  return <Card>
    <CardHeader><CardTitle className="text-lg flex items-center gap-2"><Shield aria-hidden="true" className="h-5 w-5 text-primary" />{t('security.title')}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      <p className="text-sm text-muted-foreground">{t('oidc.accountDescription')}</p>
      <Button className="relative pl-10" variant="outline" disabled={busy} aria-busy={busy} onClick={() => void openAccount()}>
        {busy && <Loader2 aria-hidden="true" className="absolute left-4 h-4 w-4 animate-spin" />}{t('oidc.manageAccount')}
      </Button>
      {failed && <p role="alert" className="text-sm text-destructive">{t('oidc.unavailable')}</p>}
    </CardContent>
  </Card>
}
