import type { TaskType } from '@/api/types'

interface JsonlPreviewProps {
  taskType: TaskType
  samples: Record<string, unknown>[]
}

function prettyToolCall(answer: unknown): string {
  if (typeof answer !== 'string') return String(answer)
  try {
    return JSON.stringify(JSON.parse(answer), null, 2)
  } catch {
    return answer
  }
}

const columnsByTask: Record<TaskType, { key: string; header: string; mono?: boolean }[]> = {
  classification: [
    { key: 'text', header: 'text' },
    { key: 'label', header: 'label', mono: true },
  ],
  qa: [
    { key: 'question', header: 'question' },
    { key: 'answer', header: 'answer' },
  ],
  tool_calling: [
    { key: 'question', header: 'question' },
    { key: 'answer', header: 'tool call', mono: true },
  ],
}

/** Task-aware table of dataset rows (preview endpoint or parsed seed file). */
export function JsonlPreview({ taskType, samples }: JsonlPreviewProps) {
  const columns = columnsByTask[taskType]

  return (
    <div className="scrollbar-thin max-h-[28rem] overflow-auto rounded-lg border border-line/60">
      <table className="w-full text-left text-sm">
        <thead className="sticky top-0">
          <tr className="border-b border-line/60 bg-surface">
            <th scope="col" className="w-10 px-3 py-2 font-mono text-xs font-medium text-body-muted">#</th>
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-body-muted"
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {samples.map((row, i) => (
            <tr key={i} className="border-b border-line/40 align-top odd:bg-surface/40 last:border-0">
              <td className="px-3 py-2 font-mono text-xs text-body-muted/60">{i + 1}</td>
              {columns.map((col) => {
                const value = row[col.key]
                const isToolCall = taskType === 'tool_calling' && col.key === 'answer'
                return (
                  <td key={col.key} className="px-3 py-2">
                    {isToolCall ? (
                      <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-info">
                        {prettyToolCall(value)}
                      </pre>
                    ) : (
                      <span className={col.mono ? 'font-mono text-xs text-accent' : 'text-body-muted'}>
                        {typeof value === 'string' ? value : JSON.stringify(value)}
                      </span>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
