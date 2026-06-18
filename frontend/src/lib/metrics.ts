/** Pull the scalar (chartable) metrics out of an arbitrary metrics_json. */
export function scalarMetrics(metricsJson: Record<string, unknown>): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [key, value] of Object.entries(metricsJson)) {
    if (typeof value === 'number' && Number.isFinite(value)) out[key] = value
  }
  return out
}

/**
 * Per-metric display metadata used to render bars with the correct scale and a
 * human-readable value range. Covers every scalar metric across the three task
 * types (classification / qa / tool_calling) plus the LLM judge score.
 *  - ratio  : 0–1, higher is better (accuracy, F1, ROUGE, BLEU, *_accuracy…)
 *  - score5 : 1–5 mean (LLM judge)
 *  - count  : an absolute total, not a proportion (n, skipped/out-of-set rows)
 */
export type MetricKind = 'ratio' | 'score5' | 'count'

export interface MetricMeta {
  kind: MetricKind
  range: string
  desc: string
}

const METRIC_META: Record<string, MetricMeta> = {
  // shared
  exact_match: { kind: 'ratio', range: '0–1', desc: 'Fraction of predictions matching the reference exactly. Higher is better.' },
  n: { kind: 'count', range: 'count', desc: 'Number of rows evaluated.' },
  llm_judge_score: { kind: 'score5', range: '1–5', desc: 'Mean LLM-judge rating: 1 (wrong) – 5 (fully correct). Higher is better.' },
  llm_judge_skipped_rows: { kind: 'count', range: 'count', desc: 'Rows where the judge response could not be parsed (lower is better).' },
  // classification
  accuracy: { kind: 'ratio', range: '0–1', desc: 'Fraction of correctly classified rows. Higher is better.' },
  f1_macro: { kind: 'ratio', range: '0–1', desc: 'Macro-averaged F1 across all labels (treats labels equally). Higher is better.' },
  out_of_set_predictions: { kind: 'count', range: 'count', desc: 'Times the model predicted a label outside the allowed set (lower is better).' },
  // qa
  rouge1: { kind: 'ratio', range: '0–1', desc: 'ROUGE-1 unigram overlap with the reference. Higher is better.' },
  rouge2: { kind: 'ratio', range: '0–1', desc: 'ROUGE-2 bigram overlap with the reference. Higher is better.' },
  rougeL: { kind: 'ratio', range: '0–1', desc: 'ROUGE-L longest-common-subsequence overlap. Higher is better.' },
  bleu: { kind: 'ratio', range: '0–1', desc: 'BLEU n-gram precision (0–1 normalised). Higher is better.' },
  // tool_calling
  json_validity: { kind: 'ratio', range: '0–1', desc: 'Fraction of outputs that parse as valid {name, parameters} JSON. Higher is better.' },
  name_accuracy: { kind: 'ratio', range: '0–1', desc: 'Fraction with the correct tool name. Higher is better.' },
  arg_accuracy: { kind: 'ratio', range: '0–1', desc: 'Fraction with correct parameters (among name-correct rows). Higher is better.' },
}

/** Resolve display metadata for a metric, inferring a sensible default. */
export function metricMeta(name: string, value: number): MetricMeta {
  const known = METRIC_META[name]
  if (known) return known
  // Unknown metric: a value in [0,1] is almost certainly a ratio; otherwise a count.
  return value >= 0 && value <= 1
    ? { kind: 'ratio', range: '0–1', desc: name }
    : { kind: 'count', range: 'count', desc: name }
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
