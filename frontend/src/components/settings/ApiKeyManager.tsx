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

export function ApiKeyManager() {
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
    <Card><CardHeader><CardTitle className="flex items-center gap-2"><KeyRound className="h-5 w-5 text-primary" />API keys</CardTitle><CardDescription>Keys for model inference. A new key is shown once when created.</CardDescription></CardHeader><CardContent className="space-y-4">
      <p className="text-sm text-muted-foreground">Keys created by the previous browser-only system cannot access the Engine. Create a new key here.</p>
      <form noValidate onSubmit={handleCreate} className="flex flex-col gap-2 sm:flex-row"><div className="min-w-0 flex-1"><Label htmlFor="api-key-name" className="sr-only">Key name</Label><Input id="api-key-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={200} placeholder="e.g. local evaluation script" disabled={create.isPending} /></div><Button type="submit" disabled={!name.trim() || create.isPending}>{create.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}Create key</Button></form>
      {create.error && <ErrorDetail error={{ detail: create.error instanceof Error ? create.error.message : 'Could not create key.' }} />}
      {keys.isLoading ? <div role="status" className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div> : keys.isError ? <ErrorDetail error={{ detail: keys.error instanceof Error ? keys.error.message : 'Could not load keys.' }} /> : keys.data?.items.length ? <div className="divide-y rounded-md border">{keys.data.items.map((key) => <div key={key.id} className="flex flex-wrap items-center gap-3 p-3"><div className="min-w-0 flex-1"><p className="font-medium">{key.name}</p><p className="font-mono text-xs text-muted-foreground">{key.prefix}••••{key.last4}</p></div><Badge variant="outline" className="capitalize">{key.status}</Badge>{key.status === 'active' && <Button type="button" variant="outline" size="sm" onClick={() => setRevokeId(key.id)}>Revoke</Button>}</div>)}</div> : <p className="text-sm text-muted-foreground">No API keys yet.</p>}
    </CardContent></Card>
    {snippet && <Card><CardHeader><CardTitle className="text-base">Inference example</CardTitle><CardDescription>Replace YOUR_API_KEY with a newly created key. This example uses your running deployment.</CardDescription></CardHeader><CardContent><div className="relative"><pre className="overflow-x-auto rounded-md border bg-muted p-3 pr-12 text-xs">{snippet}</pre><CopyButton value={snippet} label="Copy inference example" className="absolute right-2 top-2" /></div></CardContent></Card>}
    <Dialog open={createdSecret !== null} onOpenChange={(open) => { if (!open) setCreatedSecret(null) }}><DialogContent><DialogHeader><DialogTitle>Save your API key</DialogTitle><DialogDescription>Copy this key now. The full value will not be shown again.</DialogDescription></DialogHeader>{createdSecret && <div className="flex items-center gap-2 rounded-md border bg-muted p-2"><code className="min-w-0 flex-1 break-all text-xs">{createdSecret}</code><CopyButton value={createdSecret} label="Copy new API key" /></div>}<Button type="button" onClick={() => setCreatedSecret(null)}>I saved it</Button></DialogContent></Dialog>
    <ConfirmDialog open={revokeId !== null} onOpenChange={(open) => { if (!open) setRevokeId(null) }} title="Revoke API key?" description="This key will stop working immediately." confirmLabel="Revoke key" destructive loading={revoke.isPending} onConfirm={() => { if (revokeId) revoke.mutate(revokeId, { onSuccess: () => setRevokeId(null) }) }} />
    {revoke.error && <ErrorDetail error={{ detail: revoke.error instanceof Error ? revoke.error.message : 'Could not revoke key.' }} />}
  </div>
}
