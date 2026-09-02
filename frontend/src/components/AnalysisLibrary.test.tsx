import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AnalysisLibrary } from './AnalysisLibrary'
import { deleteAnalysis, listAnalyses, reconcileAnalysis } from '../api'

vi.mock('../api', () => ({
  deleteAnalysis: vi.fn(),
  listAnalyses: vi.fn(),
  reconcileAnalysis: vi.fn(),
}))

const archived = {
  analysisId: '11111111-1111-4111-8111-111111111111',
  displayName: 'Static Into Signal.wav',
  status: 'completed',
  retentionPolicy: 'persistent' as const,
  createdAt: '2026-08-17T20:00:00Z',
  updatedAt: '2026-08-17T21:00:00Z',
  archivedAt: '2026-08-17T21:00:00Z',
  durationSeconds: 218.32,
  analysisSchemaVersion: '1.4.0',
  archiveHealth: 'healthy',
  retainedAudioAvailable: true,
  analysisAvailable: true,
  storyPlanAvailable: true,
  shotPlanAvailable: true,
  dependentVideoJobCount: 1,
  explicitDeleteEligible: true,
  legacyMissing: false,
  deletedAt: null,
}

describe('AnalysisLibrary', () => {
  beforeEach(() => {
    vi.mocked(listAnalyses).mockResolvedValue({ items: [archived], total: 1, offset: 0, limit: 100 })
    vi.mocked(deleteAnalysis).mockResolvedValue(undefined)
    vi.mocked(reconcileAnalysis).mockResolvedValue(archived)
  })

  it('shows archived analyses after reload without expiry language and opens one', async () => {
    const onOpen = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    const { unmount } = render(<AnalysisLibrary onOpen={onOpen} />)

    expect(await screen.findByRole('heading', { name: 'Static Into Signal.wav' })).toBeInTheDocument()
    expect(screen.getByText('Explicit delete only')).toBeInTheDocument()
    expect(screen.getByText('3:38')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/expires|countdown/i)
    await user.click(screen.getByRole('button', { name: 'Open analysis' }))
    expect(onOpen).toHaveBeenCalledWith(archived.analysisId)

    unmount()
    render(<AnalysisLibrary onOpen={onOpen} />)
    expect(await screen.findByRole('heading', { name: 'Static Into Signal.wav' })).toBeInTheDocument()
    expect(listAnalyses).toHaveBeenCalledTimes(2)
  })

  it('requires a second explicit delete action', async () => {
    const user = userEvent.setup()
    render(<AnalysisLibrary onOpen={vi.fn()} />)
    await screen.findByRole('heading', { name: 'Static Into Signal.wav' })
    await user.click(screen.getByRole('button', { name: 'Delete…' }))
    expect(deleteAnalysis).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
    await waitFor(() => expect(deleteAnalysis).toHaveBeenCalledWith(archived.analysisId))
  })
})
