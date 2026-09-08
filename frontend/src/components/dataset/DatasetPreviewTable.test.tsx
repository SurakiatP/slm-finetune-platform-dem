import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  useDatasetPreview: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
}))

vi.mock('@/hooks/queries', () => ({
  useDatasetPreview: mocks.useDatasetPreview,
  useDatasetDownloadUrl: () => ({ mutate: mocks.mutate, isPending: false }),
}))
vi.mock('@/i18n/LanguageContext', () => ({
  useLanguage: () => ({ t: (key: string) => key }),
}))
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))

import { DatasetPreviewTable } from '@/components/dataset/DatasetPreviewTable'

describe('DatasetPreviewTable', () => {
  it('skips row preview and mints an inline URL for a zero-row PDF seed', () => {
    render(<DatasetPreviewTable datasetId="pdf-dataset" numSamples={0} />)

    expect(mocks.useDatasetPreview).toHaveBeenCalledWith('pdf-dataset', 20, false)
    fireEvent.click(screen.getByRole('button', { name: 'dataset.previewOpenFile' }))
    expect(mocks.mutate).toHaveBeenCalledWith(
      { id: 'pdf-dataset', disposition: 'inline' },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    )
  })
})
