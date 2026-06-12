export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes
  let unit = 'B'
  for (const u of units) {
    if (value < 1024) break
    value /= 1024
    unit = u
  }
  return `${value.toFixed(1)} ${unit}`
}

export function formatNumber(n: number | null | undefined, digits = 4): string {
  if (n === null || n === undefined) return '—'
  if (Number.isInteger(n)) return n.toLocaleString()
  if (Math.abs(n) < 0.001 && n !== 0) return n.toExponential(2)
  return n.toFixed(digits)
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const diffMs = Date.now() - new Date(iso).getTime()
  const sec = Math.round(diffMs / 1000)
  if (sec < 60) return 'just now'
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.round(hr / 24)
  if (day < 30) return `${day}d ago`
  return formatDateTime(iso)
}

export function formatDuration(startIso: string | null, endIso: string | null): string {
  if (!startIso) return '—'
  const end = endIso ? new Date(endIso).getTime() : Date.now()
  const sec = Math.max(0, Math.round((end - new Date(startIso).getTime()) / 1000))
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ${sec % 60}s`
  return `${Math.floor(min / 60)}h ${min % 60}m`
}

/** Truncate a UUID or hash for display; full value should go in a tooltip/copy. */
export function shortId(id: string | null | undefined, length = 8): string {
  if (!id) return '—'
  return id.length > length ? `${id.slice(0, length)}…` : id
}
