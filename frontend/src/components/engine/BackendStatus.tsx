import { useQuery } from '@tanstack/react-query'

import { getHealth, getReadiness } from '@/api/endpoints/system'
import { Badge } from '@/components/ui/badge'

export function BackendStatus() {
  const status = useQuery({
    queryKey: ['system', 'status'],
    queryFn: async () => {
      const [health, readiness] = await Promise.all([getHealth(), getReadiness()])
      return { health, readiness }
    },
    refetchInterval: 30_000,
    retry: 1,
  })

  const label = status.isError
    ? 'Engine offline'
    : status.data?.readiness.status === 'degraded'
      ? 'Engine degraded'
      : status.data?.health.status === 'ok'
        ? 'Engine online'
        : 'Checking Engine'
  const className = status.isError
    ? 'border-destructive/40 text-destructive'
    : status.data?.readiness.status === 'degraded'
      ? 'border-amber-500/40 text-amber-600'
      : 'border-emerald-500/40 text-emerald-600'

  return <Badge variant="outline" className={className}>{label}</Badge>
}

