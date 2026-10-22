import { useState, type FormEvent } from 'react'
import { KeyRound, Loader2, Plus } from 'lucide-react'
import { ConfirmDialog } from '@/components/engine/ConfirmDialog'
import { CopyButton } from '@/components/engine/CopyButton'
import { ErrorDetail } from '@/components/engine/ErrorDetail'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useApiKeys, useCreateApiKey, useDeployments, useRevokeApiKey } from '@/hooks/queries'
import { useLanguage } from '@/i18n/LanguageContext'

export function ApiKeyManager() {
  const { t } = useLanguage()
  const [name, setName] = useState('')
  const [createdSecret, setCreatedSecret] = useState<string | null>(null)
  const [revokeId, setRevokeId] = useState<string | null>(null)
  const keys = useApiKeys()
  const deployments = useDeployments()
  const create = useCreateApiKey()
  const revoke = useRevokeApiKey()
  const activeDeployment = deployments.data?.items.find((item) => item.status === 'running')
  const inferenceUrl = `${window.location.origin}/api/v1/inference`

  const handleCreate = (event: FormEvent) => {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed || create.isPending) return
    create.mutate(trimmed, { onSuccess: (result) => { setCreatedSecret(result.key); setName('') } })
  }

  const snippet = activeDeployment ? [
    `curl -X POST ${inferenceUrl}/chat/completions \\`,
    '  -H "Content-Type: application/json" \\',
    '  -H "Authorization: Bearer YOUR_API_KEY" \\',
    `  -d '{"model":"${activeDeployment.id}","messages":[{"role":"user","content":"Hello!"}],"stream":false}'`,
  ].join('\n') : null

  return <div className="space-y-5">
    <Card><CardHeader><CardTitle className="flex items-center gap-2"><KeyRound className="h-5 w-5 text-primary" />{t('apiKeysPage.cardTitle')}</CardTitle><CardDescription>{t('apiKeysPage.cardDesc')}</CardDescription></CardHeader><CardContent className="space-y-4">
      <p className="text-sm text-muted-foreground">{t('apiKeysPage.legacyNote')}</p>
      <form noValidate onSubmit={handleCreate} className="flex flex-col gap-2 sm:flex-row"><div className="min-w-0 flex-1"><Label htmlFor="api-key-name" className="sr-only">{t('apiKeysPage.keyName')}</Label><Input id="api-key-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={200} placeholder={t('apiKeysPage.placeholder')} disabled={create.isPending} /></div><Button type="submit" disabled={!name.trim() || create.isPending}>{create.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}{t('apiKeysPage.create')}</Button></form>
      {create.error && <ErrorDetail error={{ detail: create.error instanceof Error ? create.error.message : t('apiKeysPage.createError') }} />}
      {keys.isLoading ? <div role="status" className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div> : keys.isError ? <ErrorDetail error={{ detail: keys.error instanceof Error ? keys.error.message : t('apiKeysPage.loadError') }} /> : keys.data?.items.length ? <div className="divide-y rounded-md border">{keys.data.items.map((key) => <div key={key.id} className="flex flex-wrap items-center gap-3 p-3"><div className="min-w-0 flex-1"><p className="font-medium">{key.name}</p><p className="font-mono text-xs text-muted-foreground">{key.prefix}••••{key.last4}</p></div><Badge variant="outline" className="capitalize">{key.status === 'active' ? t('apiKeysPage.statusActive') : key.status === 'revoked' ? t('apiKeysPage.statusRevoked') : key.status}</Badge>{key.status === 'active' && <Button type="button" variant="outline" size="sm" onClick={() => setRevokeId(key.id)}>{t('apiKeysPage.revoke')}</Button>}</div>)}</div> : <p className="text-sm text-muted-foreground">{t('apiKeysPage.empty')}</p>}
    </CardContent></Card>
    {snippet && <Card><CardHeader><CardTitle className="text-base">{t('apiKeysPage.exampleTitle')}</CardTitle><CardDescription>{t('apiKeysPage.exampleDesc')}</CardDescription></CardHeader><CardContent><div className="relative"><pre className="overflow-x-auto rounded-md border bg-muted p-3 pr-12 text-xs">{snippet}</pre><CopyButton value={snippet} label={t('apiKeysPage.copyExample')} className="absolute right-2 top-2" /></div></CardContent></Card>}
    <Dialog open={createdSecret !== null} onOpenChange={(open) => { if (!open) setCreatedSecret(null) }}><DialogContent><DialogHeader><DialogTitle>{t('apiKeysPage.saveTitle')}</DialogTitle><DialogDescription>{t('apiKeysPage.saveDesc')}</DialogDescription></DialogHeader>{createdSecret && <div className="flex items-center gap-2 rounded-md border bg-muted p-2"><code className="min-w-0 flex-1 break-all text-xs">{createdSecret}</code><CopyButton value={createdSecret} label={t('apiKeysPage.copyNew')} /></div>}<Button type="button" onClick={() => setCreatedSecret(null)}>{t('apiKeysPage.saved')}</Button></DialogContent></Dialog>
    <ConfirmDialog open={revokeId !== null} onOpenChange={(open) => { if (!open) setRevokeId(null) }} title={t('apiKeysPage.revokeTitle')} description={t('apiKeysPage.revokeDesc')} confirmLabel={t('apiKeysPage.revokeConfirm')} destructive loading={revoke.isPending} onConfirm={() => { if (revokeId) revoke.mutate(revokeId, { onSuccess: () => setRevokeId(null) }) }} />
    {revoke.error && <ErrorDetail error={{ detail: revoke.error instanceof Error ? revoke.error.message : t('apiKeysPage.revokeError') }} />}
  </div>
}
