import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import DatasetInsights from './DatasetInsights'
import { LanguageProvider } from '@/i18n/LanguageContext'

const state = vi.hoisted(() => ({ listError: false, retry: vi.fn() }))
vi.mock('@/hooks/queries', () => ({
  useProject: () => ({ data: { id: 'p', name: 'Project' } }),
  useDatasets: () => ({ data: { items: [{ id: 'a', name: 'Dataset A' }, { id: 'b', name: 'Dataset B' }] }, isError: state.listError, error: new Error('List failed'), refetch: state.retry }),
  useDatasetInsights: () => ({ isError: true, error: new Error('Insights failed'), refetch: vi.fn() }),
}))
vi.mock('@/components/dataset/DatasetList', () => ({ DatasetList: () => null }))
vi.mock('@/components/motion', () => ({ PageTransition: ({ children }: { children: React.ReactNode }) => <>{children}</>, FadeIn: ({ children }: { children: React.ReactNode }) => <>{children}</> }))
function mount() {
  localStorage.setItem('app-language', 'en')
  render(<LanguageProvider><MemoryRouter initialEntries={['/projects/p/insights']}><Routes><Route path="/projects/:id/insights" element={<DatasetInsights />} /></Routes></MemoryRouter></LanguageProvider>)
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'Dataset Insights' }), { button: 0, ctrlKey: false })
}
it('keeps the dataset selector available when insights fail', () => {
  state.listError = false
  mount()
  expect(screen.getByText('Insights failed')).toBeInTheDocument()
  expect(screen.getByRole('combobox', { name: 'Select dataset' })).toBeEnabled()
})
it('allows retrying a failed dataset list', () => {
  state.listError = true
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  expect(state.retry).toHaveBeenCalled()
})
