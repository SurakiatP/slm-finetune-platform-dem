import { Link } from 'react-router-dom'
import { Loader2, Server } from 'lucide-react'
import { ConfirmDialog } from '@/components/engine/ConfirmDialog'
import { ErrorDetail } from '@/components/engine/ErrorDetail'
import { StatusBadge } from '@/components/engine/StatusBadge'
import { PageTransition } from '@/components/motion'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useCreateDeployment, useDeployments, useModels, useStopDeployment } from '@/hooks/queries'
import { useLanguage } from '@/i18n/LanguageContext'
import { displayModelTag, shortId } from '@/lib/format'
import { useState } from 'react'

export default function Deployment() {
  const { t } = useLanguage()
  const [stopId, setStopId] = useState<string | null>(null)
  const models = useModels(undefined, { limit: 100 })
  const deployments = useDeployments()
  const create = useCreateDeployment()
  const stop = useStopDeployment()
  const availableModels = models.data?.items.filter(model => Boolean(model.ollama_model_tag)) ?? []
  const modelNames = new Map((models.data?.items ?? []).map(model => [model.id, model.name]))
  const active = deployments.data?.items.filter(item => item.status === 'running' || item.status === 'pending') ?? []
  const loading = models.isLoading || deployments.isLoading
  const error = models.error ?? deployments.error

  return <PageTransition><div className="mx-auto max-w-5xl space-y-6">
    <div><h1 className="text-2xl font-bold">{t('deploymentPage.title')}</h1><p className="text-sm text-muted-foreground">{t('deploymentPage.subtitle')}</p></div>
    {loading && <div role="status" className="flex justify-center py-16"><Loader2 className="h-5 w-5 animate-spin" /></div>}
    {error && <ErrorDetail error={{ detail: error instanceof Error ? error.message : t('deploymentPage.loadError') }} />}
    {create.error && <ErrorDetail error={{ detail: create.error instanceof Error ? create.error.message : t('deploymentPage.createError') }} />}
    {stop.error && <ErrorDetail error={{ detail: stop.error instanceof Error ? stop.error.message : t('deploymentPage.stopError') }} />}
    {!loading && !error && <>
      <Card><CardHeader><CardTitle className="text-base">{t('deploymentPage.slots')}</CardTitle><CardDescription>{t('deploymentPage.recordCount').replace('{n}', String(deployments.data?.total ?? 0))}</CardDescription></CardHeader><CardContent className="space-y-3">
        {deployments.data?.items.length ? deployments.data.items.map(item => <div key={item.id} className="flex flex-wrap items-center gap-3 rounded-md border p-3"><Server className="h-4 w-4 text-primary" /><div className="min-w-0 flex-1"><p className="font-medium">{item.name}</p><p className="break-all font-mono text-xs text-muted-foreground" title={item.model_tag ?? undefined}>{(item.model_artifact_id && modelNames.get(item.model_artifact_id)) ?? (item.model_tag ? displayModelTag(item.model_tag) : t('deploymentPage.modelUnavailable'))}</p>{item.error_message && <p className="text-sm text-destructive">{item.error_message}</p>}</div><StatusBadge status={item.status} />{(item.status === 'running' || item.status === 'pending') && <Button type="button" size="sm" variant="outline" disabled={stop.isPending} onClick={() => setStopId(item.id)}>{t('deploymentPage.stop')}</Button>}</div>) : <p className="text-sm text-muted-foreground">{t('deploymentPage.empty')}</p>}
      </CardContent></Card>
      <Card><CardHeader><CardTitle className="text-base">{t('deploymentPage.exports')}</CardTitle><CardDescription>{t('deploymentPage.exportHint')}</CardDescription></CardHeader><CardContent className="space-y-3">
        {availableModels.length ? availableModels.map(model => { const isActive = active.some(item => item.model_artifact_id === model.id); return <div key={model.id} className="flex flex-wrap items-center gap-3 rounded-md border p-3"><div className="min-w-0 flex-1"><p className="font-medium">{model.name}</p><p className="font-mono text-xs text-muted-foreground" title={model.ollama_model_tag ?? undefined}>{displayModelTag(model.ollama_model_tag)} · {shortId(model.id)}</p></div><Button type="button" size="sm" disabled={create.isPending || active.length > 0} onClick={() => create.mutate(model.id)}>{isActive ? t('deploymentPage.alreadyDeployed') : active.length > 0 ? t('deploymentPage.slotInUse') : t('deploymentPage.deploy')}</Button></div> }) : <p className="text-sm text-muted-foreground">{t('deploymentPage.noExports')} <Link className="text-primary underline" to="/models">{t('deploymentPage.viewModels')}</Link></p>}
      </CardContent></Card>
    </>}
    <ConfirmDialog open={stopId !== null} onOpenChange={(open) => { if (!open) setStopId(null) }} title={t('deploymentPage.stopTitle')} description={t('deploymentPage.stopWarning')} confirmLabel={t('deploymentPage.stopConfirm')} destructive loading={stop.isPending} onConfirm={() => { if (stopId) stop.mutate(stopId, { onSuccess: () => setStopId(null) }) }} />
  </div></PageTransition>
}
