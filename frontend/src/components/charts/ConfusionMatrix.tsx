/**
 * CSS-grid heatmap for a classification confusion matrix.
 * Counts are always rendered as text; cell shading is supplementary.
 */
export function ConfusionMatrix({ matrix, labels }: { matrix: number[][]; labels?: string[] }) {
  const n = matrix.length
  if (n === 0) return null
  const names = labels && labels.length === n ? labels : matrix.map((_, i) => `#${i}`)
  const maxCount = Math.max(1, ...matrix.flat())

  return (
    <div className="overflow-x-auto">
      <table className="font-mono text-xs">
        <caption className="mb-2 text-left text-xs text-body-muted">
          Rows = actual, columns = predicted
        </caption>
        <thead>
          <tr>
            <th scope="col" className="p-1.5" aria-label="Actual label" />
            {names.map((name) => (
              <th key={name} scope="col" className="max-w-24 truncate p-1.5 font-medium text-body-muted" title={name}>
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={i}>
              <th scope="row" className="max-w-24 truncate p-1.5 text-right font-medium text-body-muted" title={names[i]}>
                {names[i]}
              </th>
              {row.map((count, j) => {
                const intensity = count / maxCount
                const onDiagonal = i === j
                return (
                  <td
                    key={j}
                    className="h-10 w-14 rounded-sm text-center"
                    style={{
                      backgroundColor: onDiagonal
                        ? `rgba(34,197,94,${0.08 + intensity * 0.5})`
                        : `rgba(239,68,68,${count === 0 ? 0.04 : 0.08 + intensity * 0.45})`,
                    }}
                  >
                    <span className={count === 0 ? 'text-body-muted/50' : 'text-body'}>{count}</span>
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
