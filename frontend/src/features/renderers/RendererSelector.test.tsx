import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { capabilities } from '../../test/factories'
import { RendererSelector } from './RendererSelector'

vi.mock('../../components/BlenderVisualizerPanel', () => ({
  BlenderVisualizerPanel: () => <div>Existing Blender renderer panel</div>,
}))

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function parseRequestBody(value: unknown): Record<string, unknown> {
  if (typeof value !== 'string') throw new Error('Expected a JSON request body.')
  const parsed: unknown = JSON.parse(value)
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('Expected a JSON object request body.')
  }
  return parsed as Record<string, unknown>
}

const descriptor = {
  rendererId: 'wzhk-spectrum',
  displayName: 'WZHK Spectrum',
  description: 'Prepares a private branded workspace; video capture is not automated.',
  platform: 'windows',
  capabilities: ['deterministic-workspace'],
  availability: 'MISSING_RAINMETER',
  available: false,
  preparationAvailable: true,
  previewAvailability: 'MISSING_RAINMETER',
  captureAvailability: 'MISSING_RAINMETER',
  previewAvailable: false,
  captureAvailable: false,
  warnings: ['Rainmeter was not found; no download was attempted.'],
  geometryCapability: {
    state: 'READY',
    webgl2: true,
    gpuRenderer: 'ANGLE (NVIDIA GeForce RTX 3060)',
    shaderCompiled: true,
    performanceMeasured: true,
    performanceSufficient: true,
    rendererFps: 58.7,
    averageFrameTimeMs: 17.04,
    pointCount: 4096,
    detail: 'Hardware WebGL2 and the pinned shaders passed local preflight.',
  },
  requirements: [
    { id: 'vendor-snapshot', label: 'Vendor source', available: true, requiredForPreparation: true, detail: 'Valid.' },
    { id: 'wzhk-logo', label: 'WZHK logo', available: true, requiredForPreparation: true, detail: 'Ready.' },
    { id: 'master-audio', label: 'Scattered master', available: true, requiredForPreparation: true, detail: 'Ready.' },
    { id: 'rainmeter', label: 'Rainmeter', available: false, requiredForPreparation: false, detail: 'Missing.' },
    { id: 'ffmpeg', label: 'FFmpeg', available: true, requiredForPreparation: false, detail: 'Ready.' },
  ],
  contractSummary: {
    artist: 'DJ WaZaHaKa',
    title: 'Scattered',
    bpm: 120,
    meter: '4/4',
    totalBars: 96,
    gridDurationSeconds: 192,
    masterDurationSeconds: 196.62,
    tailDurationSeconds: 4.62,
    width: 1920,
    height: 1080,
    fps: 60,
  },
  designPreset: {
    presetId: 'scattered',
    displayName: 'Scattered — Controlled Techno',
    previewTimingSource: 'external-media-player-position',
    productionTimingSource: 'trackprompt-production-clock',
    previewTimingAccuracy: 'preview-level',
    productionTimingAccuracy: 'host-monotonic-process-boundary',
    progressVisible: false,
    backgroundMode: 'generative-geometry',
    generativeGeometry: {
      enabled: true,
      subsystemId: 'wzhk-generative-geometry',
      renderMode: 'neopixel-points',
      seed: 84291,
      pointCount: 4096,
      performanceProfile: 'production',
      shapeFamilies: [
        'sparse-field',
        'lissajous',
        'matrix-field',
        'wave-surface',
        'torus',
        'twisted-torus',
        'trefoil-knot',
        'superformula',
        'spherical-lattice',
        'dispersed-field',
      ],
    },
    sections: [
      { id: 'intro', label: 'Intro', startSeconds: 0, endSeconds: 64, spectrumColor: '#668FB7' },
      { id: 'main', label: 'Main', startSeconds: 64, endSeconds: 176, spectrumColor: '#78D8FF' },
      { id: 'outro', label: 'Outro', startSeconds: 176, endSeconds: 192, spectrumColor: '#7A8DAA' },
      { id: 'post-grid-tail', label: 'Post-grid tail', startSeconds: 192, endSeconds: 196.62, spectrumColor: '#71849C' },
    ],
  },
}

const preparedJob = {
  schemaVersion: '4.0.0',
  jobId: '11111111-1111-4111-8111-111111111111',
  rendererId: 'wzhk-spectrum',
  state: 'PREVIEW_READY',
  workspaceRelativePath: 'wzhk-spectrum/jobs/11111111-1111-4111-8111-111111111111',
  contractValid: true,
  brandingApplied: true,
  vendorUnchanged: true,
  generatedWorkspaceHash: 'a'.repeat(64),
  vendorSourceHash: 'b'.repeat(64),
  vendorCommit: '553aa755ef0cc394259fb1a55560f1b31864d2e0',
  logoResolved: true,
  masterAudioResolved: true,
  warnings: ['Rainmeter is still required for manual capture.'],
  contractSummary: descriptor.contractSummary,
  mode: 'preview',
  backgroundMode: 'generative-geometry',
  presetId: 'scattered',
  presetName: 'Scattered — Controlled Techno',
  previewSection: 'main',
  generativePreviewOverride: null,
  designHash: 'c'.repeat(64),
  timingSource: 'external-media-player-position',
  timingAccuracy: 'preview-level',
  timelineControllerVersion: '2.0.0',
  visualQaRequired: true,
  masterTiming: {
    gridDurationSeconds: 192,
    masterDurationSeconds: 196.62,
    tailDurationSeconds: 4.62,
    configuredFinalFadeSeconds: 4,
    finalFadeStartSeconds: 192.62,
  },
  productionAvailability: 'MISSING_RAINMETER',
  capturePreflight: null,
  artifacts: [],
  synchronization: null,
  validationReport: null,
  captureProvider: null,
  encoder: null,
  capturedFrames: null,
  droppedFrames: null,
  captureDurationSeconds: null,
  errorMessage: null,
  geometryCapability: descriptor.geometryCapability,
  geometryTelemetry: {
    actualFps: 58.42,
    averageFrameTimeMs: 17.12,
    pointCount: 4096,
    droppedRendererFrames: 2,
    gpuRenderer: 'ANGLE (NVIDIA GeForce RTX 3060)',
  },
}

const productionJob = {
  ...preparedJob,
  jobId: '22222222-2222-4222-8222-222222222222',
  state: 'WORKSPACE_READY',
  mode: 'production',
  previewSection: null,
  timingSource: 'trackprompt-production-clock',
  timingAccuracy: 'host-monotonic-process-boundary',
  productionAvailability: 'READY_FOR_CAPTURE',
}

const capturePreflight = {
  availability: 'READY_FOR_CAPTURE',
  ready: true,
  provider: {
    providerId: 'ffmpeg-gfxcapture',
    displayName: 'FFmpeg Windows Graphics Capture',
    available: true,
    supportsWindowCapture: true,
    supportsConstantFrameRate: true,
    crashResilientContainer: 'matroska',
    encoder: 'h264_nvenc',
    hardwareAccelerationVerified: true,
    detail: 'FFmpeg Graphics Capture is available with h264_nvenc.',
  },
  timing: productionJob.masterTiming,
  rainmeterPathResolved: true,
  ffmpegPathResolved: true,
  ffprobePathResolved: true,
  playbackPathResolved: true,
  workspaceValid: true,
  masterValid: true,
  operatorNotice: 'Rainmeter will load, the approved master will play, and GPU capture will start.',
  warnings: [],
}

describe('RendererSelector', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('keeps Blender as the default and prepares a truthful Spectrum workspace', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(descriptor))
      .mockResolvedValueOnce(jsonResponse(preparedJob, 201))
    vi.stubGlobal('fetch', fetchMock)

    render(<RendererSelector jobId="analysis-job" capabilities={capabilities} />)
    expect(screen.getByText('Existing Blender renderer panel')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()

    await user.click(screen.getByRole('radio', { name: /WZHK Spectrum/ }))
    expect(await screen.findByText('Rainmeter missing')).toBeInTheDocument()
    expect(screen.getByText('DJ WaZaHaKa — Scattered')).toBeInTheDocument()
    expect(screen.getByText('120 BPM · 96 bars · grid 03:12.000')).toBeInTheDocument()
    expect(screen.getByText('Master 03:16.620 · intentional tail 00:04.620')).toBeInTheDocument()
    expect(screen.getByText('1920×1080')).toBeInTheDocument()
    expect(screen.getByText('60 FPS capture target')).toBeInTheDocument()
    expect(screen.getByText('Scattered — Controlled Techno')).toBeInTheDocument()
    expect(screen.getByText('Canonical background: Generative Geometry')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Milestone 3.7 geometry-first/ })).toBeChecked()
    expect(screen.getByText(/Production contains geometry, logo and artist\/title only/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Milestone 3.5 fallback/ })).not.toBeChecked()
    expect(screen.getByText(/Measured renderer 58.7 FPS/)).toBeInTheDocument()
    expect(screen.getByText('00:00–01:04')).toBeInTheDocument()
    expect(screen.getByText('01:04–02:56')).toBeInTheDocument()
    expect(screen.getByText('02:56–03:12')).toBeInTheDocument()
    expect(screen.getByText('03:12–03:17')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prepare Full Preview' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Preview Intro' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Preview Outro' })).toBeEnabled()
    expect(screen.getByText('Rainmeter was not found; no download was attempted.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Render Video/ })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Preview Main' }))
    expect(await screen.findByText('Workspace prepared')).toBeInTheDocument()
    expect(screen.getByText(preparedJob.jobId)).toBeInTheDocument()
    expect(screen.getByText('Fixed main preview')).toBeInTheDocument()
    expect(screen.getByText('Operator required')).toBeInTheDocument()
    expect(screen.getAllByText('Yes')).toHaveLength(3)
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/renderers/wzhk-spectrum', expect.any(Object))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/renderers/wzhk-spectrum/jobs', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        contractId: 'scattered',
        presetId: 'scattered',
        mode: 'preview',
        backgroundMode: 'generative-geometry',
        previewSection: 'main',
        visualOverrides: {
          spectrumScale: 0.9,
          sensitivity: 34,
          logoScale: 0.92,
          accentColor: '#9D7CFF',
          backgroundIntensity: 0.45,
        },
      }),
    }))
  })

  it('sends bounded trusted-shape overrides only for a selected generative preview', async () => {
    const user = userEvent.setup()
    const morphOverride = {
      mode: 'morph',
      shapeA: { shapeId: 'lissajous', seed: 12345 },
      shapeB: { shapeId: 'torus', seed: 12345 },
      morphProgress: 0.25,
      pointCount: 2048,
      rotationDegrees: 0,
      scale: 1,
      seed: 12345,
      audioMode: 'simulated',
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(descriptor))
      .mockResolvedValueOnce(jsonResponse({
        ...preparedJob,
        previewSection: null,
        generativePreviewOverride: morphOverride,
      }, 201))
    vi.stubGlobal('fetch', fetchMock)

    render(<RendererSelector jobId="analysis-job" capabilities={capabilities} />)
    await user.click(screen.getByRole('radio', { name: /WZHK Spectrum/ }))
    await screen.findByText('Rainmeter missing')
    await user.selectOptions(screen.getByLabelText('Preview mode'), 'morph')
    await user.selectOptions(screen.getByLabelText('Shape A'), 'lissajous')
    await user.selectOptions(screen.getByLabelText('Shape B'), 'torus')
    await user.selectOptions(screen.getByLabelText('Audio response'), 'simulated')
    const pointCountInput = screen.getByRole('spinbutton', { name: 'Point count' })
    fireEvent.change(pointCountInput, { target: { value: '20000' } })
    expect(pointCountInput).toHaveValue(16384)
    fireEvent.change(pointCountInput, { target: { value: '2048' } })
    await user.clear(screen.getByRole('spinbutton', { name: 'Deterministic seed' }))
    await user.type(screen.getByRole('spinbutton', { name: 'Deterministic seed' }), '12345')
    fireEvent.change(screen.getByRole('slider', { name: 'Morph progress' }), { target: { value: '0.25' } })
    await user.click(screen.getByRole('button', { name: 'Prepare Geometry Preview' }))

    expect(await screen.findByText('Lissajous curve → Torus morph')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/renderers/wzhk-spectrum/jobs', expect.objectContaining({ method: 'POST' }))
    const requestBody = parseRequestBody((fetchMock.mock.calls[1]?.[1] as RequestInit).body)
    expect(requestBody).toEqual({
      contractId: 'scattered',
      presetId: 'scattered',
      mode: 'preview',
      backgroundMode: 'generative-geometry',
      previewSection: null,
      visualOverrides: {
        spectrumScale: 0.9,
        sensitivity: 34,
        logoScale: 0.92,
        accentColor: '#9D7CFF',
        backgroundIntensity: 0.45,
      },
      generativePreview: morphOverride,
    })
  })

  it('keeps the static Milestone 3.5 fallback selectable when geometry is unavailable', async () => {
    const user = userEvent.setup()
    const unavailableDescriptor = {
      ...descriptor,
      geometryCapability: {
        state: 'WEBGL2_UNAVAILABLE',
        webgl2: false,
        gpuRenderer: null,
        shaderCompiled: null,
        performanceMeasured: false,
        performanceSufficient: null,
        rendererFps: null,
        averageFrameTimeMs: null,
        pointCount: null,
        detail: 'A hardware WebGL2 context could not be created.',
      },
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(unavailableDescriptor))
      .mockResolvedValueOnce(jsonResponse({
        ...preparedJob,
        backgroundMode: 'static-structured',
        geometryCapability: unavailableDescriptor.geometryCapability,
        geometryTelemetry: null,
      }, 201))
    vi.stubGlobal('fetch', fetchMock)

    render(<RendererSelector jobId="analysis-job" capabilities={capabilities} />)
    await user.click(screen.getByRole('radio', { name: /WZHK Spectrum/ }))
    expect(await screen.findByText(/WebGL2 unavailable/)).toBeInTheDocument()
    expect(screen.getByText(/can still use the selectable Milestone 3.5 static fallback/)).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: /Milestone 3.5 fallback/ }))
    expect(screen.getByRole('button', { name: 'Prepare Geometry Preview' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Prepare Full Preview' }))

    expect(await screen.findByText('Static structured · 3.5 fallback')).toBeInTheDocument()
    const requestBody = parseRequestBody((fetchMock.mock.calls[1]?.[1] as RequestInit).body)
    expect(requestBody).toMatchObject({
      mode: 'preview',
      backgroundMode: 'static-structured',
      previewSection: null,
    })
    expect(requestBody).not.toHaveProperty('generativePreview')
  })

  it('blocks workspace preparation when a required Spectrum gate fails', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      ...descriptor,
      availability: 'MISSING_ASSETS',
      preparationAvailable: false,
      requirements: descriptor.requirements.map((requirement) => requirement.id === 'wzhk-logo'
        ? { ...requirement, available: false }
        : requirement),
      warnings: ['Add one supported logo to the private Spectrum logo folder.'],
    })))

    render(<RendererSelector jobId="analysis-job" capabilities={capabilities} />)
    await user.click(screen.getByRole('radio', { name: /WZHK Spectrum/ }))
    const action = await screen.findByRole('button', { name: 'Prepare Full Preview' })
    expect(action).toBeDisabled()
    expect(screen.getByText('Assets missing')).toBeInTheDocument()
    expect(screen.getByText('Resolve the required Spectrum preparation gates before creating a workspace.')).toBeInTheDocument()
    expect(screen.getAllByText('Needs attention')).toHaveLength(2)
  })

  it('keeps production canonical and requires confirmation before real capture', async () => {
    const user = userEvent.setup()
    const readyJob = { ...productionJob, state: 'CAPTURE_READY', capturePreflight }
    const capturingJob = { ...readyJob, state: 'CAPTURING' }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        ...descriptor,
        availability: 'READY_FOR_CAPTURE',
        available: true,
        previewAvailability: 'READY_FOR_PREVIEW',
        captureAvailability: 'READY_FOR_CAPTURE',
        previewAvailable: true,
        captureAvailable: true,
        warnings: [],
      }))
      .mockResolvedValueOnce(jsonResponse(productionJob, 201))
      .mockResolvedValueOnce(jsonResponse(readyJob))
      .mockResolvedValueOnce(jsonResponse(capturingJob, 202))
      .mockResolvedValue(jsonResponse(capturingJob))
    const confirmMock = vi.fn().mockReturnValue(true)
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('confirm', confirmMock)

    render(<RendererSelector jobId="analysis-job" capabilities={capabilities} />)
    await user.click(screen.getByRole('radio', { name: /WZHK Spectrum/ }))
    await screen.findByText('Ready for capture')
    await user.click(screen.getByRole('button', { name: 'Prepare Production Workspace' }))
    expect(await screen.findByText('Production · canonical timeline')).toBeInTheDocument()
    expect(screen.queryByText(/Fixed .* preview/)).not.toBeInTheDocument()
    const preparationBody = parseRequestBody((fetchMock.mock.calls[1]?.[1] as RequestInit).body)
    expect(preparationBody).toMatchObject({
      mode: 'production',
      backgroundMode: 'generative-geometry',
      previewSection: null,
    })
    expect(preparationBody).not.toHaveProperty('generativePreview')

    await user.click(screen.getByRole('button', { name: 'Run Capture Preflight' }))
    expect(await screen.findAllByText('Capture ready')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: 'Create Final Visualizer' }))
    expect(confirmMock).toHaveBeenCalledWith(expect.stringContaining('The full master will play for about 03:16.620'))
    expect(await screen.findAllByText('Capturing')).toHaveLength(2)
    expect(fetchMock).toHaveBeenNthCalledWith(4, `/api/renderers/wzhk-spectrum/jobs/${productionJob.jobId}/production`, expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        operatorConfirmed: true,
        confirmationPhrase: 'START WZHK SCATTERED CAPTURE',
      }),
    }))
  })
})
