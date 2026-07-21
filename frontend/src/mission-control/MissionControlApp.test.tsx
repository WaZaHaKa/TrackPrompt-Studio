import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MissionControlApp } from './MissionControlApp'
import { makeClient, makeRenderJob, testCloud, testPaths, testSystem } from './testFixtures'
import type { RenderEventSubscriber } from './types'

const quietSubscriber: RenderEventSubscriber = {
  subscribe: vi.fn(() => ({ close: vi.fn(), getLastSequence: () => 0 })),
}

describe('MissionControlApp', () => {
  it('answers readiness, recommendation, time, storage, and authorization without exposing hashes', async () => {
    const user = userEvent.setup()
    render(<MissionControlApp client={makeClient()} eventSubscriber={quietSubscriber} />)

    expect(await screen.findByRole('heading', { name: 'Trip to Andromeda' })).toBeInTheDocument()
    expect(screen.getByText('720p Hyper Optimized')).toBeInTheDocument()
    expect(screen.getByText(/about 5 hr/i)).toBeInTheDocument()
    expect(screen.getByText('10 GiB')).toBeInTheDocument()
    expect(screen.getByText('Ready to authorize')).toBeInTheDocument()
    expect(screen.queryByText(/225EE7124B62/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/DB27AA9DE293/i)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /start a new render/i }))
    expect(screen.getByRole('heading', { name: /new render/i })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: /new render progress/i })).toBeInTheDocument()
  })

  it('keeps render readiness independent from encode setup', async () => {
    render(<MissionControlApp client={makeClient({
      getSystemStatus: () => Promise.resolve({ ...testSystem, ffmpegReady: false }),
      getSystemPaths: () => Promise.resolve({ ...testPaths, ffmpegPath: null }),
    })} eventSubscriber={quietSubscriber} />)

    expect(await screen.findByText('Ready to authorize')).toBeInTheDocument()
    expect(screen.getByText('FFmpeg is not configured; local rendering is still available.')).toBeInTheDocument()
  })

  it('keeps cloud and encode execution honestly disabled and links the analysis fallback', async () => {
    const user = userEvent.setup()
    render(<MissionControlApp client={makeClient({
      getCloudReadiness: () => Promise.resolve({ ...testCloud, status: 'ready' }),
    })} eventSubscriber={quietSubscriber} />)
    await screen.findByRole('heading', { name: 'Trip to Andromeda' })

    await user.click(screen.getByRole('button', { name: 'Cloud' }))
    expect(screen.getByRole('heading', { name: 'Preparation detected' })).toBeInTheDocument()
    expect(screen.getByText('not connected')).toBeInTheDocument()
    expect(screen.queryByText('Available now')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create sanitized package/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /start cloud render/i })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Encode' }))
    expect(screen.getByText(/encoding is not reported as available/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('link', { name: /open trackprompt analysis workspace/i })).toHaveAttribute('href', '/?workspace=analysis')
  })

  it('refreshes verified encode candidates before leaving a completed render', async () => {
    const user = userEvent.setup()
    const completeJob = makeRenderJob({
      state: 'complete',
      phase: 'final_verify',
      currentFrame: 13029,
      lastCompletedFrame: 13029,
      renderedFrames: 13029,
      inFlightFrames: 0,
      validatedFrames: 13029,
      publishedFrames: 13029,
      canEncode: true,
      rendererActive: false,
      watcherActive: false,
    })
    const listEncodeCandidates = vi.fn()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{
        jobId: completeJob.jobId,
        displayName: 'Trip to Andromeda',
        outputPath: completeJob.outputPath,
        frameCount: completeJob.totalFrames,
        totalFrames: completeJob.totalFrames,
        verified: true,
        audioMuxAvailable: false,
      }])
    const client = makeClient({
      getSystemStatus: () => Promise.resolve({ ...testSystem, activeJobId: completeJob.jobId }),
      listJobs: () => Promise.resolve([completeJob]),
      listEncodeCandidates,
    })
    render(<MissionControlApp client={client} eventSubscriber={quietSubscriber} />)

    expect(await screen.findByRole('heading', { name: 'Render complete' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Encode video' }))

    expect(await screen.findByRole('heading', { name: 'Trip to Andromeda' })).toBeInTheDocument()
    expect(screen.getByText('13,029 / 13,029 frames')).toBeInTheDocument()
    expect(screen.queryByText('No complete frame sequence yet')).not.toBeInTheDocument()
    expect(listEncodeCandidates).toHaveBeenCalledTimes(2)
  })

  it('monitors idle backend health and recovers the connection indicator', async () => {
    let resolveRecovery: (() => void) | undefined
    const checkHealth = vi.fn()
      .mockRejectedValueOnce(new Error('service restarting'))
      .mockImplementationOnce(() => new Promise<void>((resolve) => { resolveRecovery = resolve }))
      .mockResolvedValue(undefined)
    render(<MissionControlApp
      client={makeClient({ checkHealth })}
      eventSubscriber={quietSubscriber}
      idleHealthIntervalMs={5}
    />)

    await screen.findByRole('heading', { name: 'Trip to Andromeda' })
    await waitFor(() => expect(checkHealth).toHaveBeenCalledTimes(1))
    expect(screen.getByText('Reconnecting')).toBeInTheDocument()
    await waitFor(() => expect(checkHealth).toHaveBeenCalledTimes(2))
    resolveRecovery?.()
    await waitFor(() => expect(screen.getByText('Connected')).toBeInTheDocument())
  })
})
