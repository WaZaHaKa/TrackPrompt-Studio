import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VideoGenerationScreen } from './VideoGenerationScreen'
import { parseVideoJob } from '../videoGenerationApi'

const phrase = 'AUTHORIZE the-glitch-is-me VIDEO PLAN aaaaaaaaaaaa UP TO USD 24.00'

const plannedJob = {
  schemaVersion: '1.0.0',
  jobId: '11111111-1111-4111-8111-111111111111',
  analysisJobId: '22222222-2222-4222-8222-222222222222',
  projectId: 'the-glitch-is-me',
  title: 'The Glitch Is Me',
  state: 'planned',
  planDigest: 'a'.repeat(64),
  profile: { profileId: 'final-fast-1080p', modelId: 'veo-3.1-fast-generate-001', resolution: '1080p' },
  cost: {
    baseEstimatedUsd: 12.8,
    conservativeEstimatedUsd: 19.2,
    maxSpendUsd: 24,
    pricingSnapshotDate: '2026-08-13',
    rateUsdPerOutputSecond: 0.1,
  },
  sourceArtifacts: { shotBankSha256: 'b'.repeat(64) },
  authorizationPhrase: phrase,
  authorizationExpiresAt: null,
  audioMasterBound: false,
  audio: {
    selected: false, verified: false, source: null, audioArtifactId: null,
    displayName: null, durationSeconds: null, sampleRateHz: null, channels: null,
    container: null, audioCodec: null, sha256: null, finishingSha256: null,
    analysisJobId: null, boundVideoJobId: null, selectedAt: null, error: null,
  },
  localEditDigest: null,
  shots: [{
    shotId: 'shot-001', chapterId: 'chapter-01', order: 1, title: 'Signal wakes',
    prompt: 'A precise cinematic prompt for the first abstract signal shot.',
    negativePrompt: 'No text, logos, lyrics, watermarks, or embedded metadata.',
    seed: 101, state: 'planned', reviewState: 'pending', reviewNote: null,
    attemptCount: 0, reservedCostUsd: 0, error: null, clipUrl: null,
    variationIndex: 0, continuityGroupIds: ['world-global'], previousShotId: null,
    continuationMode: 'prompt-anchors', referenceAssetId: null,
  }],
  progressPercent: 0,
  verifiedShotCount: 0,
  totalShotCount: 1,
  reservedCostUsd: 0,
  remainingAuthorizedUsd: 24,
  requestPreviewUrl: '/api/mission-control/video/plans/111/requests',
  consistencyNotice: 'Seeds improve repeatability but cannot guarantee a character lock.',
  continuity: {
    masterSeed: 18031000, seedLocked: true, seedDerivation: 'sha256-v1',
    characterProfiles: [], visualAnchors: {}, groups: [],
  },
  artifacts: {
    timelineReady: false, davinciPackageReady: false, previewReady: false,
    fcpxmlUrl: null, fcp7XmlUrl: null, edlUrl: null, editSheetUrl: null,
    markersUrl: null, previewUrl: null,
    relinkMapUrl: null, coverageReportUrl: null, renderManifestUrl: null,
    verificationReportUrl: null,
  },
  error: null,
  createdAt: '2026-08-13T10:00:00Z',
  updatedAt: '2026-08-13T10:00:00Z',
}

const catalog = {
  schemaVersion: '1.0.0',
  analyses: [{
    analysisJobId: plannedJob.analysisJobId,
    displayName: 'Analysis 22222222',
    storyPlanAvailable: true,
    shotPlanAvailable: true,
    retainedAudioAvailable: false,
  }],
  packages: [{
    projectId: 'the-glitch-is-me', title: 'The Glitch Is Me', shotCount: 16,
    defaultMasterSeed: 18031000,
    profiles: [{
      id: 'fast-1080p', displayName: 'Veo 3.1 Fast · 1080p',
      modelId: 'veo-3.1-fast-generate-001', resolution: '1080p', durationSeconds: 8,
      fps: 24, sampleCount: 1, default: true, optional: false,
      baseEstimatedUsd: 12.8, conservativeEstimatedUsd: 19.2, maxSpendUsd: 24,
      available: true, availabilityNote: null,
    }, {
      id: 'quality-1080p', displayName: 'Veo 3.1 Quality · 1080p',
      modelId: 'veo-3.1-generate-001', resolution: '1080p', durationSeconds: 8,
      fps: 24, sampleCount: 1, default: false, optional: true,
      baseEstimatedUsd: 25.6, conservativeEstimatedUsd: 38.4, maxSpendUsd: 45,
      available: true, availabilityNote: null,
    }, {
      id: 'quality-4k', displayName: 'Veo 3.1 Quality · 4K (optional)',
      modelId: 'veo-3.1-generate-001', resolution: '4k', durationSeconds: 8,
      fps: 24, sampleCount: 1, default: false, optional: true,
      baseEstimatedUsd: 51.2, conservativeEstimatedUsd: 76.8, maxSpendUsd: 80,
      available: false, availabilityNote: 'Current GA model does not support 4K.',
    }],
  }],
  pricingSnapshotDate: '2026-08-13',
  providerNetworkContacted: false,
}

class QuietEventSource {
  onopen: ((this: EventSource, event: Event) => unknown) | null = null
  onerror: ((this: EventSource, event: Event) => unknown) | null = null
  addEventListener = vi.fn()
  close = vi.fn()
}

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

describe('VideoGenerationScreen', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal('EventSource', QuietEventSource)
  })

  afterEach(() => vi.unstubAllGlobals())

  it('shows exact cost before authorization and never starts generation implicitly', async () => {
    const calls: Array<{ path: string; method: string; body?: unknown }> = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const path = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
      const method = init?.method ?? 'GET'
      calls.push({ path, method, body: typeof init?.body === 'string' ? JSON.parse(init.body) as unknown : undefined })
      if (path.endsWith('/video/catalog')) return Promise.resolve(json(catalog))
      if (path.endsWith('/video/jobs')) return Promise.resolve(json([]))
      if (path.endsWith('/video/plans') && method === 'POST') return Promise.resolve(json(plannedJob))
      if (path.endsWith('/requests')) return Promise.resolve(json({ schemaVersion: '1.0.0', jobId: plannedJob.jobId, planDigest: plannedJob.planDigest, requests: [{ parameters: { resolution: '1080p' } }] }))
      if (path.endsWith('/authorize')) return Promise.resolve(json({ ...plannedJob, state: 'authorized', authorizationExpiresAt: '2026-08-14T10:00:00Z' }))
      throw new Error(`Unexpected request: ${method} ${path}`)
    }))

    const user = userEvent.setup()
    render(<VideoGenerationScreen />)

    expect(await screen.findByRole('heading', { name: 'Compile the exact plan' })).toBeInTheDocument()
    expect(screen.getByText('$12.80')).toBeInTheDocument()
    expect(screen.getByText('$24.00')).toBeInTheDocument()
    expect(screen.getByText(/no paid request occurs/i)).toBeInTheDocument()

    await user.type(screen.getByLabelText('GCP project ID'), 'my-gcp-project')
    await user.type(screen.getByLabelText('GCS bucket'), 'my-private-video-bucket')
    await user.click(screen.getByRole('button', { name: 'Compile exact video plan' }))

    expect(await screen.findByRole('heading', { name: 'Review prompts and maximum spend' })).toBeInTheDocument()
    const compileCall = calls.find((call) => call.path.endsWith('/video/plans') && call.method === 'POST')
    expect(compileCall?.body).toMatchObject({
      profileId: 'fast-1080p',
      gcpProjectId: 'my-gcp-project',
      gcsBucket: 'my-private-video-bucket',
      masterSeed: 18031000,
      seedLocked: true,
    })
    expect(calls.some((call) => call.path.endsWith('/start'))).toBe(false)

    const authorize = screen.getByRole('button', { name: 'Authorize this complete exact batch once' })
    expect(authorize).toBeDisabled()
    await user.type(screen.getByLabelText('One-time confirmation phrase'), phrase)
    expect(authorize).toBeEnabled()
    await user.click(authorize)

    expect(await screen.findByText('Exact batch authorized')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start smoke shot and complete batch' })).toBeInTheDocument()
    expect(calls.some((call) => call.path.endsWith('/start'))).toBe(false)
  })

  it('rejects malformed job payloads instead of silently inventing status', () => {
    expect(() => parseVideoJob({ ...plannedJob, state: 'mystery' })).toThrow(/job state is unsupported/i)
    expect(() => parseVideoJob({ ...plannedJob, cost: { maxSpendUsd: 24 } })).toThrow(/baseEstimatedUsd/i)
  })

  it('syncs the visible planner profile to a selected saved Quality job', async () => {
    const qualityJob = {
      ...plannedJob,
      jobId: '33333333-3333-4333-8333-333333333333',
      profile: { profileId: 'final-quality-1080p', modelId: 'veo-3.1-generate-001', resolution: '1080p' },
      cost: { ...plannedJob.cost, baseEstimatedUsd: 25.6, conservativeEstimatedUsd: 38.4, maxSpendUsd: 45 },
      authorizationPhrase: 'AUTHORIZE the-glitch-is-me VIDEO PLAN aaaaaaaaaaaa UP TO USD 45.00',
      remainingAuthorizedUsd: 45,
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL): Promise<Response> => {
      const path = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
      if (path.endsWith('/video/catalog')) return Promise.resolve(json(catalog))
      if (path.endsWith('/video/jobs')) return Promise.resolve(json([qualityJob]))
      if (path.endsWith('/requests')) return Promise.resolve(json({ schemaVersion: '1.0.0', jobId: qualityJob.jobId, planDigest: qualityJob.planDigest, requests: [] }))
      throw new Error(`Unexpected request: ${path}`)
    }))

    render(<VideoGenerationScreen />)

    expect(await screen.findByLabelText('Delivery profile')).toHaveValue('quality-1080p')
    expect(screen.getAllByText('$45.00').length).toBeGreaterThan(0)
  })

  it('binds a browsed master through the typed saved-job contract', async () => {
    const bound = {
      ...plannedJob,
      audioMasterBound: true,
      audio: {
        selected: true, verified: true, source: 'local-selection',
        audioArtifactId: 'audio-' + 'c'.repeat(20), displayName: 'Glitch master.wav',
        durationSeconds: 297.68, sampleRateHz: 48000, channels: 2,
        container: 'wav', audioCodec: 'pcm_s16le', sha256: 'c'.repeat(64),
        finishingSha256: 'c'.repeat(64), analysisJobId: plannedJob.analysisJobId,
        boundVideoJobId: plannedJob.jobId, selectedAt: '2026-08-14T00:00:00Z', error: null,
      },
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const path = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
      if (path.endsWith('/video/catalog')) return Promise.resolve(json(catalog))
      if (path.endsWith('/video/jobs')) return Promise.resolve(json([plannedJob]))
      if (path.endsWith('/requests')) return Promise.resolve(json({ schemaVersion: '1.0.0', jobId: plannedJob.jobId, planDigest: plannedJob.planDigest, requests: [] }))
      if (path.endsWith('/audio/select') && init?.method === 'POST') return Promise.resolve(json(bound.audio))
      if (path.endsWith(`/video/plans/${plannedJob.jobId}`)) return Promise.resolve(json(bound))
      throw new Error(`Unexpected request: ${init?.method ?? 'GET'} ${path}`)
    }))

    const user = userEvent.setup()
    render(<VideoGenerationScreen />)
    await user.click(await screen.findByRole('button', { name: 'Browse for audio…' }))
    expect(await screen.findByText(/Glitch master\.wav · 297\.680 seconds/)).toBeInTheDocument()
    expect(screen.queryByText(/audio selected must be a boolean/i)).not.toBeInTheDocument()
  })

  it('shows safe provider status and diagnostic identity without a raw response body', async () => {
    const failed = {
      ...plannedJob,
      state: 'failed',
      error: {
        code: 'provider_request_failed',
        summary: 'Google Veo returned HTTP 400 (provider_request_failed). INVALID_ARGUMENT: Unsupported parameter. Diagnostic ID veo-safe-123.',
        retryable: false,
        httpStatus: 400,
        providerStatus: 'INVALID_ARGUMENT',
        providerErrorCode: '400',
        diagnosticId: 'veo-safe-123',
      },
    }
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL): Promise<Response> => {
      const path = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
      if (path.endsWith('/video/catalog')) return Promise.resolve(json(catalog))
      if (path.endsWith('/video/jobs')) return Promise.resolve(json([failed]))
      if (path.endsWith('/requests')) return Promise.resolve(json({ schemaVersion: '1.0.0', jobId: failed.jobId, planDigest: failed.planDigest, requests: [] }))
      throw new Error(`Unexpected request: ${path}`)
    }))
    render(<VideoGenerationScreen />)
    expect(await screen.findByText(/unsupported parameter/i)).toBeInTheDocument()
    expect(screen.getByText(/HTTP 400 · INVALID_ARGUMENT · diagnostic veo-safe-123/i)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/responseBody|must-not-survive/i)
  })
})
