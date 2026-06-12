/** Pull the scalar (chartable) metrics out of an arbitrary metrics_json. */
export function scalarMetrics(metricsJson: Record<string, unknown>): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [key, value] of Object.entries(metricsJson)) {
    if (typeof value === 'number' && Number.isFinite(value)) out[key] = value
  }
  return out
}

/** Detect a number[][] confusion matrix inside metrics_json. */
export function extractConfusionMatrix(
  metricsJson: Record<string, unknown>,
): { matrix: number[][]; labels?: string[] } | null {
  const raw = metricsJson.confusion_matrix
  if (!Array.isArray(raw) || raw.length === 0) return null
  if (!raw.every((row) => Array.isArray(row) && row.every((c) => typeof c === 'number'))) return null
  const labels = Array.isArray(metricsJson.labels)
    ? (metricsJson.labels as unknown[]).filter((l): l is string => typeof l === 'string')
    : undefined
  return { matrix: raw as number[][], labels }
}
