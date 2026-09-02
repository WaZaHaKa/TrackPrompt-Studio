import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeRenderJob } from '../testFixtures'
import type { OutputVariantProgress } from '../types'
import { LiveProgress } from './LiveProgress'

function makeVariant(overrides: Partial<OutputVariantProgress> = {}): OutputVariantProgress {
  return {
    id: 'horizontal-16x9-1080p',
    displayName: 'Horizontal 16:9',
    enabled: true,
    required: true,
    width: 1920,
    height: 1080,
    fps: 30,
    aspectRatio: '16:9',
    deliverableRole: 'primary-master',
    compositionMode: 'authored',
    compositionProfileId: 'andromeda-horizontal',
    compositionProfileSha256: 'C'.repeat(64),
    profileId: 'andromeda-horizontal-final',
    profileSha256: 'D'.repeat(64),
    outputVariantSha256: 'E'.repeat(64),
    state: 'running',
    phase: 'render_frame',
    frameStart: 1,
    frameEnd: 13029,
    currentFrame: 101,
    currentFrameStartedAt: new Date(Date.now() - 2_000).toISOString(),
    lastOutputAt: new Date(Date.now() - 500).toISOString(),
    latestRenderedFrame: 100,
    latestSafeFrame: 90,
    renderedFrames: 100,
    inFlightFrames: 10,
    validatedFrames: 90,
    publishedFrames: 90,
    totalFrames: 13029,
    activeChunkId: '000001-000600',
    chunkStart: 1,
    chunkEnd: 600,
    currentChunkProgress: 0.16,
    chunksCompleted: 0,
    chunksTotal: 22,
    previewUrl: '/api/mission-control/render/job-test/variants/horizontal-16x9-1080p/preview',
    fullFrameUrl: '/api/mission-control/render/job-test/variants/horizontal-16x9-1080p/frames/100',
    previewFrame: 100,
    latestPreviewAt: '2026-07-23T09:00:00Z',
    workers: [{
      id: 'worker-horizontal',
      status: 'active',
      active: true,
      currentTaskId: 'task-horizontal',
      currentFrame: 101,
      retryCount: 1,
      failureCount: 0,
      lastHeartbeatAt: '2026-07-23T09:00:01Z',
    }],
    retryCount: 1,
    failureCount: 0,
    stages: [{
      id: 'render',
      label: 'Image sequence render',
      state: 'running',
      completedUnits: 100,
      totalUnits: 13029,
      progress: 100 / 13029,
      throughput: 0.25,
      throughputUnit: 'frames',
      elapsedSeconds: 400,
      startedAt: '2026-07-23T08:53:20Z',
      updatedAt: '2026-07-23T09:00:00Z',
      eta: {
        state: 'stable',
        p50Seconds: 3600,
        p90Seconds: 5400,
        p50CompletionAt: null,
        p90CompletionAt: null,
        confidence: 'high',
        freshness: 'fresh',
        lastEstimateAt: '2026-07-23T09:00:00Z',
        sampleCount: 84,
      },
    }],
    eta: {
      state: 'stable',
      p50Seconds: 3600,
      p90Seconds: 5400,
      p50CompletionAt: null,
      p90CompletionAt: null,
      confidence: 'high',
      freshness: 'fresh',
      lastEstimateAt: '2026-07-23T09:00:00Z',
      sampleCount: 84,
    },
    ...overrides,
  }
}

function renderProgress(overrides: Parameters<typeof makeRenderJob>[0] = {}) {
  const callbacks = {
    onRefresh: vi.fn(),
    onStopAfterChunk: vi.fn(),
    onCancelStop: vi.fn(),
    onCancelRender: vi.fn(),
    onRetryCurrentChunk: vi.fn(),
    onRetryFailedRender: vi.fn(),
    onResume: vi.fn(),
    onOpenOutput: vi.fn(),
    onEncode: vi.fn(),
    onDismissError: vi.fn(),
  }
  render(
    <LiveProgress
      job={makeRenderJob({ previewFrame: null, lastCompletedFrame: null, ...overrides })}
      connection="connected"
      logs={[{ sequence: 1, timestamp: '2026-07-21T10:00:00Z', level: 'info', message: 'Rendering frame 8110', technicalDetails: null }]}
      busyAction={null}
      advanced
      fallbackFps={30}
      {...callbacks}
    />,
  )
  return callbacks
}

describe('LiveProgress', () => {
  it('distinguishes in-flight and safe frames and stays visibly alive during a long frame', async () => {
    const user = userEvent.setup()
    const callbacks = renderProgress()

    expect(screen.getByRole('heading', { name: /rendering frame 8,110/i })).toBeInTheDocument()
    expect(screen.getByText('Rendering is still active')).toBeInTheDocument()
    expect(screen.getByText('110 frames')).toBeInTheDocument()
    expect(screen.getByText('8,000 frames')).toBeInTheDocument()
    expect(screen.getByText('Rendered, not yet safe')).toBeInTheDocument()
    expect(screen.getByText('Safe, preserved on resume')).toBeInTheDocument()
    expect(screen.getByText('Rupture · The impossible fall')).toBeInTheDocument()
    expect(screen.getByText(/written inside the active chunk, but not yet validated/i)).toBeInTheDocument()
    expect(screen.getByText(/these frames will not need to be rendered again/i)).toBeInTheDocument()
    expect(screen.getByText('Song timestamp').closest('.mc-metric')).toHaveTextContent('04:30.3')
    expect(screen.getByText('Song timestamp').closest('.mc-metric')).toHaveTextContent('07:14.3')
    expect(screen.getByText('Current shot ETA').closest('.mc-metric')).toHaveTextContent('Indeterminate')
    expect(screen.getByText('Current chunk ETA').closest('.mc-metric')).toHaveTextContent('P50 7 minutes')
    expect(screen.getByText('Current chunk ETA').closest('.mc-metric')).toHaveTextContent('P90 8 minutes')

    const cancelRender = screen.getByRole('button', { name: 'Cancel render' })
    expect(cancelRender).toBeEnabled()
    await user.click(cancelRender)
    expect(screen.getByRole('dialog', { name: /cancel this render at the safe chunk boundary/i })).toBeInTheDocument()
    expect(screen.getByText(/does not delete output files or job history/i)).toBeInTheDocument()
    const confirmCancel = screen.getByRole('button', { name: 'Confirm cancel' })
    expect(confirmCancel).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /cancel this exact render job/i }))
    expect(confirmCancel).toBeEnabled()
    await user.click(confirmCancel)
    expect(callbacks.onCancelRender).toHaveBeenCalledTimes(1)

    const retryChunk = screen.getByRole('button', { name: 'Retry current chunk' })
    expect(retryChunk).toBeEnabled()
    await user.click(retryChunk)
    expect(screen.getByRole('dialog', { name: /retry the active current chunk/i })).toBeInTheDocument()
    expect(screen.getByText(/does not delete published output or job history/i)).toBeInTheDocument()
    const confirmRetryCurrent = screen.getByRole('button', { name: 'Retry exact current chunk' })
    expect(confirmRetryCurrent).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /retry this exact current chunk/i }))
    await user.click(confirmRetryCurrent)
    expect(callbacks.onRetryCurrentChunk).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /stop after current chunk/i }))
    expect(callbacks.onStopAfterChunk).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: /open logs/i }))
    expect(screen.getByRole('log')).toHaveTextContent('Rendering frame 8110')
  })

  it('confirms an exact-identity retry only for a retryable failed render', async () => {
    const user = userEvent.setup()
    const callbacks = renderProgress({
      state: 'failed',
      rendererActive: false,
      watcherActive: false,
      error: {
        code: 'render_process_failed',
        title: 'Render process failed',
        summary: 'The renderer stopped in the active chunk.',
        likelyCause: 'The renderer process exited.',
        recommendedAction: 'Retry the deterministic missing work.',
        retryable: true,
        context: {},
        technicalDetails: 'exit code 1',
        relatedPath: null,
        timestamp: '2026-07-21T10:00:00Z',
        jobId: 'job-test',
      },
    })

    const retryChunk = screen.getByRole('button', { name: 'Retry failed chunk' })
    expect(retryChunk).toBeEnabled()
    await user.click(retryChunk)

    expect(screen.getByRole('dialog', { name: /retry the failed render work/i })).toBeInTheDocument()
    expect(screen.getByText(/fill only deterministic missing or invalid work/i)).toBeInTheDocument()
    const confirmRetry = screen.getByRole('button', { name: 'Retry exact failed work' })
    expect(confirmRetry).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /retry the missing or invalid work/i }))
    expect(confirmRetry).toBeEnabled()
    await user.click(confirmRetry)

    expect(callbacks.onRetryFailedRender).toHaveBeenCalledTimes(1)
  })

  it('retries a failed current chunk through the exact current-chunk control', async () => {
    const user = userEvent.setup()
    const callbacks = renderProgress({
      state: 'failed',
      rendererActive: false,
      watcherActive: false,
      error: {
        code: 'render_process_failed',
        title: 'Render process failed',
        summary: 'The renderer stopped in the active chunk.',
        likelyCause: 'The renderer process exited.',
        recommendedAction: 'Retry the deterministic missing work.',
        retryable: true,
        context: {},
        technicalDetails: 'exit code 1',
        relatedPath: null,
        timestamp: '2026-07-21T10:00:00Z',
        jobId: 'job-test',
      },
    })

    await user.click(screen.getByRole('button', { name: 'Retry current chunk' }))
    expect(screen.getByRole('dialog', { name: /retry the failed current chunk/i })).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: /retry this exact current chunk/i }))
    await user.click(screen.getByRole('button', { name: 'Retry exact current chunk' }))

    expect(callbacks.onRetryCurrentChunk).toHaveBeenCalledTimes(1)
  })

  it('does not enable retry when the backend marks the failed render non-retryable', () => {
    renderProgress({
      state: 'failed',
      rendererActive: false,
      error: {
        code: 'render_identity_invalid',
        title: 'Render identity is invalid',
        summary: 'The saved identity no longer matches.',
        likelyCause: 'A saved file changed.',
        recommendedAction: 'Restore the exact identity.',
        retryable: false,
        context: {},
        technicalDetails: null,
        relatedPath: null,
        timestamp: '2026-07-21T10:00:00Z',
        jobId: 'job-test',
      },
    })

    expect(screen.getByRole('button', { name: 'Retry failed chunk' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Retry current chunk' })).toBeDisabled()
    expect(screen.getAllByText(/backend marked this failure as non-retryable/i)).toHaveLength(2)
  })

  it('shows safe-boundary cancellation as pending and does not offer to undo it', () => {
    renderProgress({
      state: 'cancel_requested',
      safeStopStatus: 'requested',
      warning: 'Cancellation is pending at the current chunk boundary.',
    })

    expect(screen.getByRole('button', { name: 'Cancellation requested' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Cancel stop request' })).not.toBeInTheDocument()
    expect(screen.getByText(/saved output is preserved/i)).toBeInTheDocument()
  })

  it('shows a truthful disabled state while current-chunk retry is pending', () => {
    renderProgress({
      state: 'retry_requested',
      warning: 'Retry requested for exact chunk 7801-8400.',
    })

    expect(screen.getByRole('button', { name: 'Retry current chunk requested' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Cancel render' })).not.toBeInTheDocument()
    expect(screen.getByText(/validated prior chunks remain authoritative/i)).toBeInTheDocument()
  })

  it('cancels only a pending stop request before the chunk boundary is committed', async () => {
    const user = userEvent.setup()
    const callbacks = renderProgress({
      state: 'stop_requested',
      safeStopStatus: 'requested',
    })

    await user.click(screen.getByRole('button', { name: 'Cancel stop request' }))

    expect(callbacks.onCancelStop).toHaveBeenCalledTimes(1)
  })

  it('does not offer cancel-stop after the renderer commits to finishing the chunk', () => {
    renderProgress({
      state: 'finishing_current_chunk',
      safeStopStatus: 'finishing_chunk',
    })

    expect(screen.queryByRole('button', { name: 'Cancel stop request' })).not.toBeInTheDocument()
  })

  it('keeps a structured renderer error visible with a safe resume action', async () => {
    const user = userEvent.setup()
    const callbacks = renderProgress({
      state: 'resumable',
      rendererActive: false,
      canResume: true,
      error: {
        code: 'render_process_stopped',
        title: 'Render process stopped',
        summary: 'The render ended before the current chunk was published.',
        likelyCause: 'The renderer process exited.',
        recommendedAction: 'Inspect logs or resume the exact render.',
        retryable: true,
        context: {},
        technicalDetails: 'exit code 1',
        relatedPath: null,
        timestamp: '2026-07-21T10:00:00Z',
        jobId: 'job-test',
      },
    })

    expect(screen.getByRole('alert')).toHaveTextContent('Render process stopped')
    await user.click(screen.getByRole('button', { name: /resume safely/i }))
    expect(callbacks.onResume).toHaveBeenCalledTimes(1)
  })

  it('does not present the last published frame as the exact active production frame', () => {
    renderProgress({
      currentFrame: 600,
      publishedFrames: 600,
      renderedFrames: 600,
      validatedFrames: 600,
      chunksCompleted: 1,
      chunksTotal: 22,
      chunkStart: 601,
      chunkEnd: 1200,
      currentFrameStartedAt: null,
      phase: 'render_frame',
    })

    expect(screen.getByRole('heading', { name: /rendering chunk 2 of 22/i })).toBeInTheDocument()
    expect(screen.getByText(/600 of 13,029 safely published/i)).toBeInTheDocument()
    expect(screen.getByText(/exact frame activity is not reported/i)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /rendering frame 600/i })).not.toBeInTheDocument()
  })

  it('switches independently authored variant streams and exposes the exact full-resolution frame', async () => {
    const user = userEvent.setup()
    const horizontal = makeVariant()
    const vertical = makeVariant({
      id: 'vertical-9x16-1080p',
      displayName: 'Vertical 9:16',
      required: false,
      width: 1080,
      height: 1920,
      aspectRatio: '9:16',
      compositionProfileId: 'andromeda-vertical',
      currentFrame: 51,
      latestRenderedFrame: 50,
      latestSafeFrame: 40,
      renderedFrames: 50,
      inFlightFrames: 10,
      validatedFrames: 40,
      publishedFrames: 40,
      previewFrame: 50,
      previewUrl: '/api/mission-control/render/job-test/variants/vertical-9x16-1080p/preview',
      fullFrameUrl: '/api/mission-control/render/job-test/variants/vertical-9x16-1080p/frames/50',
    })
    renderProgress({
      activeVariantId: horizontal.id,
      outputVariants: [horizontal, vertical],
      aggregateEta: {
        state: 'stable',
        p50Seconds: 7000,
        p90Seconds: 9500,
        p50CompletionAt: null,
        p90CompletionAt: null,
        confidence: 'medium',
        freshness: 'fresh',
        lastEstimateAt: '2026-07-23T09:00:00Z',
        sampleCount: 140,
      },
    })

    const selector = screen.getByRole('combobox', { name: 'Output variant' })
    expect(selector).toHaveValue(horizontal.id)
    expect(screen.getByAltText(/horizontal 16:9 frame 100/i)).toHaveAttribute('src', expect.stringContaining(horizontal.previewUrl ?? ''))
    expect(screen.getByText('Aggregate job ETA')).toBeInTheDocument()
    expect(screen.getByText('Aggregate ETA P90')).toBeInTheDocument()

    await user.selectOptions(selector, vertical.id)

    const preview = screen.getByAltText(/vertical 9:16 frame 50/i)
    expect(preview).toHaveAttribute('src', expect.stringContaining(vertical.previewUrl ?? ''))
    expect(preview.parentElement).toHaveStyle({ aspectRatio: '1080 / 1920' })
    expect(screen.getByText('Latest rendered frame 50')).toBeInTheDocument()
    expect(screen.getByText('Latest safe frame 40')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open exact full-resolution frame/i })).toHaveAttribute('href', vertical.fullFrameUrl)
  })

  it('does not expose a selector or phantom workload for a disabled optional variant', () => {
    const horizontal = makeVariant()
    renderProgress({
      activeVariantId: horizontal.id,
      outputVariants: [
        horizontal,
        makeVariant({
          id: 'vertical-9x16-1080p',
          displayName: 'Vertical 9:16',
          enabled: false,
          required: false,
          width: 1080,
          height: 1920,
        }),
      ],
    })

    expect(screen.queryByRole('combobox', { name: 'Output variant' })).not.toBeInTheDocument()
    expect(screen.getAllByText('Horizontal 16:9')).not.toHaveLength(0)
    expect(screen.queryByText('Vertical 9:16')).not.toBeInTheDocument()
    expect(screen.queryByText(/across 2 variants/i)).not.toBeInTheDocument()
  })
})
