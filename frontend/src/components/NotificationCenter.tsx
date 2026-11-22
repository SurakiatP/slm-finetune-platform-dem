import { useState } from 'react'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { AlertCircle, Bell, CheckCircle2, ChevronRight, Loader2, Rocket, XCircle } from 'lucide-react'
import { listNotifications, markAllNotificationsRead, markNotificationRead, type NotificationType, type UserNotification } from '@/api/endpoints/notifications'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useLanguage } from '@/i18n/LanguageContext'

const queryKey = ['notifications'] as const
const titleKeys: Record<NotificationType, string> = {
  'dataset.completed': 'notif.datasetCompleted', 'dataset.failed': 'notif.datasetFailed',
  'training.completed': 'notif.trainingCompleted', 'training.failed': 'notif.trainingFailed',
  'evaluation.completed': 'notif.evaluationCompleted', 'evaluation.failed': 'notif.evaluationFailed',
  'export.completed': 'notif.exportCompleted', 'export.failed': 'notif.exportFailed',
  'deployment.ready': 'notif.deploymentReady', 'deployment.failed': 'notif.deploymentFailed',
}

function eventIcon(type: NotificationType) {
  if (type.endsWith('.failed')) return XCircle
  if (type === 'deployment.ready') return Rocket
  return CheckCircle2
}

function destination(item: UserNotification): string {
  const projectId = item.payload.project_id
  if ((item.resource_type === 'training' || item.resource_type === 'dataset' || item.resource_type === 'evaluation') && typeof projectId === 'string') {
    return `/projects/${encodeURIComponent(projectId)}${item.resource_type === 'training' ? '/training' : ''}`
  }
  if (item.resource_type === 'deployment') return '/deployment'
  if (item.resource_type === 'model') return '/models'
  if (item.resource_type === 'dataset') return '/datasets'
  if (item.resource_type === 'evaluation') return '/evaluations'
  return '/projects'
}

function relativeTime(iso: string, locale: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const seconds = Math.round((date.getTime() - Date.now()) / 1000)
  const abs = Math.abs(seconds)
  const [unit, amount] = abs < 60 ? ['second', seconds] : abs < 3600 ? ['minute', Math.round(seconds / 60)] : abs < 86400 ? ['hour', Math.round(seconds / 3600)] : ['day', Math.round(seconds / 86400)]
  return new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(amount as number, unit as Intl.RelativeTimeFormatUnit)
}

export function NotificationCenter() {
  const { t, language } = useLanguage()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [failedAction, setFailedAction] = useState<{ kind: 'read'; id: string } | { kind: 'all' } | null>(null)
  const notifications = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) => listNotifications(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    retry: 1,
  })
  const markRead = useMutation({
    mutationFn: markNotificationRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })
  const markAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })

  const handleMarkAll = async () => {
    try {
      await markAll.mutateAsync()
      setFailedAction(null)
    } catch {
      setFailedAction({ kind: 'all' })
    }
  }

  const retryAction = async () => {
    if (!failedAction) return
    try {
      if (failedAction.kind === 'all') await markAll.mutateAsync()
      else await markRead.mutateAsync(failedAction.id)
      setFailedAction(null)
    } catch {
      // Keep the failed action available for another attempt.
    }
  }

  const pages = notifications.data?.pages ?? []
  const items = pages.flatMap((page) => page.items)
  const unreadCount = pages[0]?.unread_count ?? 0
  const locale = language === 'th' ? 'th-TH' : 'en'

  const openNotification = async (item: UserNotification) => {
    try {
      if (!item.read_at) await markRead.mutateAsync(item.id)
      setFailedAction(null)
      setOpen(false)
      navigate(destination(item))
    } catch {
      setFailedAction({ kind: 'read', id: item.id })
    }
  }

  return (
    <Popover open={open} onOpenChange={(nextOpen) => { setOpen(nextOpen); if (nextOpen) void notifications.refetch() }}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="h-8 w-8 relative" aria-label={t('notif.title')}>
          <Bell className="h-4 w-4" aria-hidden="true" />
          {unreadCount > 0 && <span aria-label={`${unreadCount} ${t('notif.unreadCount')}`} className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-bold text-destructive-foreground">{unreadCount > 99 ? '99+' : unreadCount}</span>}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(22rem,calc(100vw-2rem))] p-0">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold">{t('notif.title')}</h2>
          {unreadCount > 0 && <Button variant="link" size="sm" className="h-auto p-0 text-xs" disabled={markAll.isPending} onClick={() => void handleMarkAll()}>{markAll.isPending ? t('notif.markingRead') : t('notif.markAllRead')}</Button>}
        </div>
        {(markAll.isError || markRead.isError) && <div role="alert" className="flex items-center gap-2 border-b px-4 py-2 text-xs text-destructive"><AlertCircle className="h-4 w-4 shrink-0" />{t('notif.actionFailed')}<button className="ml-auto underline" disabled={!failedAction || markAll.isPending || markRead.isPending} onClick={() => void retryAction()}>{t('notif.retry')}</button></div>}
        <div className="max-h-[min(28rem,70vh)] overflow-y-auto" aria-live="polite">
          {notifications.isPending ? <div role="status" className="flex items-center justify-center gap-2 px-4 py-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{t('notif.loading')}</div>
            : notifications.isError ? <div role="alert" className="px-4 py-6 text-center text-sm"><p className="mb-3 text-muted-foreground">{t('notif.loadFailed')}</p><Button variant="outline" size="sm" onClick={() => void notifications.refetch()}>{t('notif.retry')}</Button></div>
              : items.length === 0 ? <p className="px-4 py-8 text-center text-sm text-muted-foreground">{t('notif.empty')}</p>
                : items.map((item) => {
                  const Icon = eventIcon(item.event_type)
                  const title = titleKeys[item.event_type]
                  const detail = typeof item.payload.name === 'string' ? item.payload.name : typeof item.payload.message === 'string' ? item.payload.message : ''
                  return <button key={item.id} onClick={() => void openNotification(item)} disabled={markRead.isPending} className={`flex w-full items-start gap-3 border-b px-4 py-3 text-left transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait ${!item.read_at ? 'bg-accent/20' : ''}`}>
                    <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${item.event_type.endsWith('.failed') ? 'text-destructive' : 'text-muted-foreground'}`} aria-hidden="true" />
                    <span className="min-w-0 flex-1"><span className="block text-sm font-medium leading-tight">{t(title)}</span>{detail && <span className="mt-0.5 block truncate text-xs text-muted-foreground">{detail}</span>}<time dateTime={item.created_at} className="mt-1 block text-[10px] text-muted-foreground">{relativeTime(item.created_at, locale)}</time></span>
                    {!item.read_at && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary" aria-label={t('notif.unread')} />}
                    <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  </button>
                })}
          {notifications.hasNextPage && <div className="p-3 text-center"><Button variant="ghost" size="sm" onClick={() => void notifications.fetchNextPage()} disabled={notifications.isFetchingNextPage}>{notifications.isFetchingNextPage && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{t('notif.loadMore')}</Button></div>}
        </div>
      </PopoverContent>
    </Popover>
  )
}
