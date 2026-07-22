import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeRenderJob } from '../testFixtures'
import { LiveProgress } from './LiveProgress'

function renderProgress(overrides: Parameters<typeof makeRenderJob>[0] = {}) {
  const callbacks = {
    onRefresh: vi.fn(),
    onStopAfterChunk: vi.fn(),
    onCancelStop: vi.fn(),
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

    await user.click(screen.getByRole('button', { name: /stop after current chunk/i }))
    expect(callbacks.onStopAfterChunk).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: /open logs/i }))
    expect(screen.getByRole('log')).toHaveTextContent('Rendering frame 8110')
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
})
