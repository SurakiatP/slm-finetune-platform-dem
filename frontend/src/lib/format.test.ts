import { afterEach, describe, expect, it } from 'vitest'
import { displayModelTag, formatRelativeTime, formatUsd } from './format'

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

describe('displayModelTag', () => {
  it('drops the owner namespace from Ollama tags', () => {
    expect(displayModelTag('02fda8bd-d111-4e1d-af2e-121ce36d8559/customer-classification')).toBe('customer-classification')
    expect(displayModelTag('slm/16cf12cf')).toBe('16cf12cf')
    expect(displayModelTag('llama3.2:3b')).toBe('llama3.2:3b')
    expect(displayModelTag(null)).toBe('—')
  })
})

describe('formatRelativeTime', () => {
  afterEach(() => {
    document.documentElement.lang = ''
  })

  it('follows the UI language set on <html lang>', () => {
    const fourHoursAgo = new Date(Date.now() - 4 * 3_600_000).toISOString()
    document.documentElement.lang = 'en'
    expect(formatRelativeTime(fourHoursAgo)).toMatch(/4 hr\.? ago/)
    document.documentElement.lang = 'th'
    expect(formatRelativeTime(fourHoursAgo)).toContain('4')
    expect(formatRelativeTime(fourHoursAgo)).not.toMatch(/ago/)
  })
})
