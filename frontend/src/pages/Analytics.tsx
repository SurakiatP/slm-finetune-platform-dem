import { useState } from 'react'
import { Activity, CircleDollarSign, Loader2, Timer, Workflow } from 'lucide-react'
import { ErrorDetail } from '@/components/engine/ErrorDetail'
import { PageTransition } from '@/components/motion'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useAnalytics, useProjects } from '@/hooks/queries'

const seconds = (value: number | null) => value === null ? '—' : value < 60 ? `${Math.round(value)}s` : `${(value / 60).toFixed(1)} min`
const cost = (value: string | null) => value === null ? 'Not priced' : `$${Number(value).toFixed(4)}`

export default function Analytics() {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [projectId, setProjectId] = useState('')
  const projects = useProjects({ limit: 100 })
  const invalidRange = Boolean(from && to && from > to)
  const analytics = useAnalytics({ from: from || undefined, to: to || undefined, project_id: projectId || undefined }, !invalidRange)
  const data = invalidRange ? undefined : analytics.data
  const summary = data ? [
    { label: 'Jobs', value: data.totals.jobs_total.toLocaleString(), icon: Workflow },
    { label: 'Prompt tokens', value: data.totals.prompt_tokens.toLocaleString(), icon: Activity },
    { label: 'Completion tokens', value: data.totals.completion_tokens.toLocaleString(), icon: Activity },
    { label: 'Usage cost', value: cost(data.totals.cost_usd), icon: CircleDollarSign },
  ] : []

  return <PageTransition><div className="mx-auto max-w-6xl space-y-6">
    <div><h1 className="text-2xl font-bold">Analytics</h1><p className="text-sm text-muted-foreground">Jobs and usage calculated by the Engine for your account.</p></div>
    <div className="grid gap-3 sm:grid-cols-3"><div><Label htmlFor="analytics-from">From (UTC)</Label><Input id="analytics-from" type="date" value={from} onChange={event => setFrom(event.target.value)} /></div><div><Label htmlFor="analytics-to">To (UTC)</Label><Input id="analytics-to" type="date" value={to} onChange={event => setTo(event.target.value)} /></div><div><Label id="analytics-project-label">Project</Label><Select value={projectId || 'all'} onValueChange={value => setProjectId(value === 'all' ? '' : value)}><SelectTrigger aria-labelledby="analytics-project-label"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All projects</SelectItem>{projects.data?.items.map(project => <SelectItem key={project.id} value={project.id}>{project.name}</SelectItem>)}</SelectContent></Select></div></div>
    {invalidRange && <p role="alert" className="text-sm text-destructive">The start date must be on or before the end date.</p>}
    {!invalidRange && analytics.isLoading && <div role="status" className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>}
    {!invalidRange && analytics.isError && <ErrorDetail error={{ detail: analytics.error instanceof Error ? analytics.error.message : 'Analytics could not be loaded.' }} />}
    {data && <>
      <p className="text-sm text-muted-foreground">{data.period_from} to {data.period_to}</p>
      {data.totals.has_unpriced_usage && <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">Some usage is not priced yet; displayed cost is a lower bound.</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{summary.map(({ label, value, icon: Icon }) => <Card key={label}><CardContent className="flex items-center gap-3 p-5"><Icon className="h-5 w-5 text-primary" /><div><p className="text-xs text-muted-foreground">{label}</p><p className="text-lg font-semibold">{value}</p></div></CardContent></Card>)}</div>
      <Card><CardHeader><CardTitle className="text-lg">Pipeline stages</CardTitle><CardDescription>Job state and average timing per stage.</CardDescription></CardHeader><CardContent className="overflow-x-auto"><table className="w-full text-sm"><thead className="border-b text-left text-muted-foreground"><tr><th className="pb-3 font-medium">Stage</th><th className="pb-3 font-medium">Jobs</th><th className="pb-3 font-medium">Completed</th><th className="pb-3 font-medium">Failed</th><th className="pb-3 font-medium">Average duration</th><th className="pb-3 font-medium">Queue wait</th></tr></thead><tbody>{data.stages.map(stage => <tr key={stage.stage} className="border-b last:border-0"><td className="py-3 font-medium capitalize">{stage.stage}</td><td className="py-3">{stage.total}</td><td className="py-3 text-success">{stage.by_status.completed}</td><td className="py-3 text-destructive">{stage.by_status.failed}</td><td className="py-3">{seconds(stage.avg_duration_seconds)}</td><td className="py-3">{seconds(stage.avg_queue_wait_seconds)}</td></tr>)}</tbody></table></CardContent></Card>
      <Card><CardHeader><CardTitle className="flex items-center gap-2 text-lg"><Timer className="h-5 w-5 text-primary" />Current status</CardTitle></CardHeader><CardContent className="flex flex-wrap gap-2">{Object.entries(data.totals.by_status).map(([status, count]) => <Badge key={status} variant="outline" className="capitalize">{status}: {count}</Badge>)}</CardContent></Card>
    </>}
  </div></PageTransition>
}
