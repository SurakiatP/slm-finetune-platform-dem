import { Link } from 'react-router-dom'
import { Loader2, Server } from 'lucide-react'
import { ConfirmDialog } from '@/components/engine/ConfirmDialog'
import { ErrorDetail } from '@/components/engine/ErrorDetail'
import { StatusBadge } from '@/components/engine/StatusBadge'
import { PageTransition } from '@/components/motion'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useCreateDeployment, useDeployments, useModels, useStopDeployment } from '@/hooks/queries'
import { useState } from 'react'

export default function Deployment() {
  const [stopId, setStopId] = useState<string | null>(null)
  const models = useModels(undefined, { limit: 100 })
  const deployments = useDeployments()
  const create = useCreateDeployment()
  const stop = useStopDeployment()
  const availableModels = models.data?.items.filter(model => Boolean(model.ollama_model_tag)) ?? []
  const active = deployments.data?.items.filter(item => item.status === 'running' || item.status === 'pending') ?? []
  const loading = models.isLoading || deployments.isLoading
  const error = models.error ?? deployments.error

  return <PageTransition><div className="mx-auto max-w-5xl space-y-6">
    <div><h1 className="text-2xl font-bold">Model deployments</h1><p className="text-sm text-muted-foreground">Start a serving slot for an exported model. Running deployments can be called with an API key.</p></div>
    {loading && <div role="status" className="flex justify-center py-16"><Loader2 className="h-5 w-5 animate-spin" /></div>}
    {error && <ErrorDetail error={{ detail: error instanceof Error ? error.message : 'Could not load deployments.' }} />}
    {create.error && <ErrorDetail error={{ detail: create.error instanceof Error ? create.error.message : 'Could not start deployment.' }} />}
    {stop.error && <ErrorDetail error={{ detail: stop.error instanceof Error ? stop.error.message : 'Could not stop deployment.' }} />}
    {!loading && !error && <>
      <Card><CardHeader><CardTitle className="text-base">Serving slots</CardTitle><CardDescription>{deployments.data?.total ?? 0} deployment records</CardDescription></CardHeader><CardContent className="space-y-3">
        {deployments.data?.items.length ? deployments.data.items.map(item => <div key={item.id} className="flex flex-wrap items-center gap-3 rounded-md border p-3"><Server className="h-4 w-4 text-primary" /><div className="min-w-0 flex-1"><p className="font-medium">{item.name}</p><p className="break-all font-mono text-xs text-muted-foreground">{item.model_tag ?? item.model_artifact_id ?? 'Model unavailable'}</p>{item.error_message && <p className="text-sm text-destructive">{item.error_message}</p>}</div><StatusBadge status={item.status} />{(item.status === 'running' || item.status === 'pending') && <Button type="button" size="sm" variant="outline" disabled={stop.isPending} onClick={() => setStopId(item.id)}>Stop</Button>}</div>) : <p className="text-sm text-muted-foreground">No deployments yet.</p>}
      </CardContent></Card>
      <Card><CardHeader><CardTitle className="text-base">Exported models</CardTitle><CardDescription>Export a model to Ollama before starting a deployment.</CardDescription></CardHeader><CardContent className="space-y-3">
        {availableModels.length ? availableModels.map(model => { const isActive = active.some(item => item.model_artifact_id === model.id); return <div key={model.id} className="flex flex-wrap items-center gap-3 rounded-md border p-3"><div className="min-w-0 flex-1"><p className="font-medium">{model.ollama_model_tag}</p><p className="font-mono text-xs text-muted-foreground">{model.id}</p></div><Button type="button" size="sm" disabled={create.isPending || active.length > 0} onClick={() => create.mutate(model.id)}>{isActive ? 'Already deployed' : active.length > 0 ? 'Slot in use' : 'Deploy'}</Button></div> }) : <p className="text-sm text-muted-foreground">No Ollama exports yet. <Link className="text-primary underline" to="/models">View models</Link></p>}
      </CardContent></Card>
    </>}
    <ConfirmDialog open={stopId !== null} onOpenChange={(open) => { if (!open) setStopId(null) }} title="Stop deployment?" description="API key requests to this model will stop working." confirmLabel="Stop deployment" destructive loading={stop.isPending} onConfirm={() => { if (stopId) stop.mutate(stopId, { onSuccess: () => setStopId(null) }) }} />
  </div></PageTransition>
}
