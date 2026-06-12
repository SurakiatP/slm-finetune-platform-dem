/** Parse a .jsonl or .json (array) payload into rows. Throws with a row-indexed message. */
export function parseJsonlText(text: string): Record<string, unknown>[] {
  const trimmed = text.trim()
  if (!trimmed) return []

  if (trimmed.startsWith('[')) {
    const arr = JSON.parse(trimmed) as unknown
    if (!Array.isArray(arr)) throw new Error('JSON payload must be an array of objects')
    return arr.map((row, i) => {
      if (typeof row !== 'object' || row === null || Array.isArray(row)) {
        throw new Error(`Row ${i + 1} is not an object`)
      }
      return row as Record<string, unknown>
    })
  }

  return trimmed.split('\n').map((line, i) => {
    try {
      const row = JSON.parse(line) as unknown
      if (typeof row !== 'object' || row === null || Array.isArray(row)) {
        throw new Error('not an object')
      }
      return row as Record<string, unknown>
    } catch {
      throw new Error(`Line ${i + 1} is not valid JSON`)
    }
  })
}

export function parseJsonlFile(file: File): Promise<Record<string, unknown>[]> {
  return file.text().then(parseJsonlText)
}
