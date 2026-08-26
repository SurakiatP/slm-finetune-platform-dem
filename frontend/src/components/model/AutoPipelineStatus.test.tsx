import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AutoPipelineStatus } from '@/components/model/AutoPipelineStatus'

vi.mock('@/i18n/LanguageContext', () => ({
  useLanguage: () => ({ t: (key: string) => key }),
}))

describe('AutoPipelineStatus', () => {
  it('renders failed export and skipped evaluation details', () => {
    render(<AutoPipelineStatus pipeline={{
      export: {
        status: 'failed',
        artifact_id: null,
        error: 'Export worker failed',
      },
      evaluate: {
        status: 'skipped',
        evaluation_id: null,
        skip_reason: 'No holdout dataset was generated',
        error: null,
      },
    }} />)

    expect(screen.getByText('failed')).toBeInTheDocument()
    expect(screen.getByText('skipped')).toBeInTheDocument()
    expect(screen.getByText('Export worker failed')).toBeInTheDocument()
    expect(screen.getByText(/No holdout dataset was generated/)).toBeInTheDocument()
  })
})
