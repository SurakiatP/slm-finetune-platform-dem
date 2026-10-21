import { describe, expect, it } from 'vitest'
import { formatUsd } from './format'

describe('formatUsd', () => {
  it('shows a floor instead of a misleading zero for sub-cent amounts', () => {
    expect(formatUsd('0.00002')).toBe('< $0.0001')
  })
  it('keeps true zero and normal amounts', () => {
    expect(formatUsd('0')).toBe('$0.0000')
    expect(formatUsd('1.23456')).toBe('$1.2346')
  })
  it('handles missing values', () => {
    expect(formatUsd(null)).toBe('—')
  })
})
