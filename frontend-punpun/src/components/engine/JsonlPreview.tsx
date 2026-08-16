interface JsonlPreviewProps {
  rows: unknown[];
  className?: string;
}

function formatRow(row: unknown): string {
  if (typeof row === "string") return row;
  try {
    return JSON.stringify(row, null, 2);
  } catch {
    return String(row);
  }
}

/** Generic monospace, scrollable preview of dataset rows (seed upload parse,
 *  SDG generation preview, merged dataset sample). Deliberately dumb about
 *  row shape — callers that need task-aware columns should format `rows`
 *  before passing them in; this just lays out an indexed, pre-formatted
 *  JSON/string table. Ported layout semantics (sticky-ish scroll container,
 *  row index column, monospace body) from
 *  frontend/src/components/data/JsonlPreview.tsx. */
export function JsonlPreview({ rows, className }: JsonlPreviewProps) {
  return (
    <div className={`max-h-[28rem] overflow-auto rounded-lg border border-border ${className ?? ""}`}>
      <table className="w-full text-left text-sm">
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-border/60 align-top odd:bg-muted/30 last:border-0">
              <td className="w-10 px-3 py-2 font-mono text-xs text-muted-foreground/60">{i + 1}</td>
              <td className="px-3 py-2">
                <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-foreground">
                  {formatRow(row)}
                </pre>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
