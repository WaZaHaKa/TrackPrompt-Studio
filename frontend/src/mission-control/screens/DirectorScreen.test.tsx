import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeClient } from '../testFixtures'
import type { DirectorWorkspace } from '../types'
import { DirectorScreen } from './DirectorScreen'

const acts = ['Signal', 'Awakening', 'Departure', 'Gates', 'Rupture', 'Transformation', 'Arrival']

function workspace(): DirectorWorkspace {
  return {
    analysisJobId: '12345678-1234-4234-8234-123456789abc',
    updatedAt: '2026-07-22T08:00:00Z',
    storyPlan: {
      schemaVersion: '1.0.0',
      acts: acts.map((name, index) => ({
        id: name.toLowerCase(),
        name,
        frameStart: index * 100 + 1,
        frameEnd: (index + 1) * 100,
        narrativePurpose: `${name} advances the protagonist's journey.`,
        protagonistState: index === 0 ? 'signalled' : 'travelling',
      })),
    },
    shotPlan: {
      schemaVersion: '1.0.0',
      shots: acts.map((name, index) => ({
        id: `${name.toLowerCase()}-shot`,
        name: `${name} shot`,
        actId: name.toLowerCase(),
        frameStart: index * 100 + 1,
        frameEnd: (index + 1) * 100,
        storyPurpose: `Show the ${name.toLowerCase()} beat clearly.`,
        protagonistState: index === 0 ? 'signalled' : 'travelling',
        reviewFrames: [index * 100 + 50],
      })),
    },
    reviews: [],
  }
}

describe('DirectorScreen', () => {
  it('has an accessible empty state and exposes reconnecting status', async () => {
    render(<DirectorScreen client={makeClient()} connection="reconnecting" />)

    expect(await screen.findByRole('heading', { name: 'No cinematic plan yet' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Director' })).toBeInTheDocument()
    expect(screen.getByText(/reconnecting to the local service/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled()
  })

  it('presents local loading failures as a retryable error', async () => {
    const client = makeClient({ getDirectorWorkspace: () => Promise.reject(new Error('local plan unreadable')) })
    render(<DirectorScreen client={client} connection="connected" />)

    expect(await screen.findByRole('alert')).toHaveTextContent('local plan unreadable')
    expect(screen.getByRole('button', { name: /try again/i })).toBeEnabled()
  })

  it('reviews a representative frame and saves a typed local decision', async () => {
    const user = userEvent.setup()
    const initial = workspace()
    const putDirectorReview = vi.fn().mockResolvedValue(initial)
    render(<DirectorScreen client={makeClient({ getDirectorWorkspace: () => Promise.resolve(initial), putDirectorReview })} connection="connected" />)

    expect(await screen.findByRole('heading', { name: 'Signal' })).toBeInTheDocument()
    expect(screen.getByLabelText('Representative review frame 50')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Review shot' })[0]!)
    expect(screen.getByRole('heading', { name: 'Review Signal shot' })).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Story clarity'), 'needs-revision')
    await user.type(screen.getByLabelText(/Findings/i), 'Strengthen the silhouette.')
    await user.click(screen.getByRole('radio', { name: 'Request revision' }))
    await user.click(screen.getByRole('button', { name: 'Save local review' }))

    await waitFor(() => expect(putDirectorReview).toHaveBeenCalledTimes(1))
    expect(putDirectorReview).toHaveBeenCalledWith(initial.analysisJobId, 'signal-shot', expect.objectContaining({
      shotId: 'signal-shot',
      reviewFrame: 50,
      storyClarity: 'needs-revision',
      decision: 'revise',
      findings: ['Strengthen the silhouette.'],
    }))
  })
})
