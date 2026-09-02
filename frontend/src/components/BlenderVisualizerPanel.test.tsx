import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { exportVisualCues, resolveVisualizerConfig } from '../api'
import {
  DEFAULT_CAPABILITIES,
  type Capabilities,
  type ResolvedVisualizerConfig,
  type TrackPromptVisualCueSheet,
} from '../types'
import { cloneDefaultSpaceJourneyParameters } from '../visualizer'
import { BlenderVisualizerPanel } from './BlenderVisualizerPanel'

vi.mock('../api', () => ({
  exportVisualCues: vi.fn(),
  resolveVisualizerConfig: vi.fn(),
}))

const visualizerCapabilities: Capabilities = {
  ...DEFAULT_CAPABILITIES,
  visualCueExportAvailable: true,
  visualCueSheetSchemaVersion: '1.1.0',
  visualFeatureArtifactSchemaVersion: '1.0.0',
  blenderVisualizerPreset: 'abstract-geometry',
  blenderVisualizerDefaultPreset: 'abstract-geometry',
  blenderVisualizerPresets: ['abstract-geometry', 'space-journey'],
  blenderVisualizerConfigSchemaVersion: '1.0.0',
}

const cueSheet = {
  schemaVersion: '1.1.0',
  source: { analysisSchemaVersion: '1.4.0', analysisVersion: '0.5.0', jobId: 'job-1', requestedMode: 'fast', effectiveMode: 'fast' },
  timeline: { durationSeconds: 12, fps: 30, frameStart: 1, frameEnd: 360, framePolicy: 'nearest-half-up-clamped' },
  musicalGrid: { bpm: { value: 120, confidence: 'medium' }, secondsPerBeat: 0.5, meter: { value: null, confidence: 'unknown' }, downbeatsAvailable: false },
  beats: [{ index: 0, timeSeconds: 0, frame: 1, confidence: 'medium', strength: null, sourcePath: 'rhythm.beatTimestamps' }],
  onsets: [{ index: 0, timeSeconds: 0, frame: 1, confidence: 'medium', strength: null, sourcePath: 'rhythm.onsetTimestamps' }],
  sections: [{ id: 'a', neutralLabel: 'A', inferredLabel: null, startSeconds: 0, endSeconds: 12, startFrame: 1, endFrame: 360, energy: 0.5, loudness: null, confidence: 'medium', boundaryConfidence: 'medium', repetitionGroup: null, vocalActivity: null, instruments: [], stemActivity: {}, stemRelativeRms: {}, sourcePath: 'structure.sections[0]' }],
  transitions: [],
  curves: {
    masterEnergy: {
      pointFormat: ['frame', 'value'], points: [[1, 0], [360, 1]], interpolation: 'linear', sourceSampleRateHz: 20, originalPointCount: 241, exportedPointCount: 2,
      simplification: { method: 'rdp', tolerance: 0.008, maximumError: 0.004, maximumPointCount: 1600 },
      normalization: { method: 'robust-percentile', lowerPercentile: 5, upperPercentile: 95, normalizationGroup: 'master' },
      smoothing: { method: 'asymmetric-exponential', attackSeconds: 0.08, releaseSeconds: 0.35, sourceSampleRateHz: 20, outputSampleRateHz: 20 },
    },
  },
  warnings: [],
} satisfies TrackPromptVisualCueSheet

const abstractConfig: ResolvedVisualizerConfig = {
  schemaVersion: '1.0.0',
  preset: 'abstract-geometry',
  parameters: {},
  seed: 84291,
  defaultedParameters: [],
  warnings: [],
}

const spaceConfig = (overrides = {}): ResolvedVisualizerConfig => ({
  schemaVersion: '1.0.0',
  preset: 'space-journey',
  parameters: { ...cloneDefaultSpaceJourneyParameters(), ...overrides },
  seed: 84291,
  defaultedParameters: [],
  warnings: [],
})

describe('BlenderVisualizerPanel', () => {
  const createObjectURL = vi.fn(() => 'blob:visualizer-download')
  const revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)

  beforeEach(() => {
    vi.mocked(exportVisualCues).mockResolvedValue({
      cueSheet,
      blob: new Blob(['{}'], { type: 'application/json' }),
      filename: 'trackprompt-job-1-visual-cues.json',
    })
    vi.mocked(resolveVisualizerConfig).mockResolvedValue(abstractConfig)
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('exports selected cue options, reports actual counts, and cleans the cue object URL', async () => {
    const user = userEvent.setup()
    const view = render(<BlenderVisualizerPanel jobId="job-1" capabilities={visualizerCapabilities} />)
    await user.selectOptions(screen.getByLabelText('Frames per second'), '60')
    await user.selectOptions(screen.getByLabelText('Curve detail'), 'detailed')
    await user.click(screen.getByLabelText('Onsets'))
    await user.click(screen.getByRole('button', { name: 'Export cue sheet' }))

    await waitFor(() => expect(exportVisualCues).toHaveBeenCalledWith('job-1', expect.objectContaining({
      fps: 60,
      curveDetail: 'detailed',
      includeOnsets: false,
      includeBeats: true,
      includeCurves: true,
    })))
    expect(await screen.findByText('Exported 1 beats, 1 onsets, 1 sections, 0 transitions, and 1 curves.')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Frames per second'), '30')
    expect(screen.queryByText(/Exported 1 beats/)).not.toBeInTheDocument()
    expect(createObjectURL).toHaveBeenCalledOnce()
    view.unmount()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:visualizer-download')
  })

  it('keeps Abstract Geometry as the default and conditionally shows resettable Space Journey controls', async () => {
    const user = userEvent.setup()
    render(<BlenderVisualizerPanel jobId="job-1" capabilities={visualizerCapabilities} />)

    const selector = screen.getByLabelText('Visualizer preset')
    expect(selector).toHaveValue('abstract-geometry')
    expect(screen.getByRole('option', { name: 'Abstract Geometry' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Space Journey' })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Camera' })).not.toBeInTheDocument()

    await user.selectOptions(selector, 'space-journey')
    expect(screen.getByRole('group', { name: 'Camera' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Orbital structures' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Environment' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Audio response' })).toBeInTheDocument()
    expect(screen.getByLabelText('Camera distance')).toHaveValue('18')
    expect(screen.getByLabelText('Palette')).toHaveValue('andromeda')

    fireEvent.change(screen.getByLabelText('Camera distance'), { target: { value: '30' } })
    expect(screen.getByLabelText('Camera distance')).toHaveValue('30')
    await user.click(screen.getByRole('button', { name: 'Reset Space Journey defaults' }))
    expect(screen.getByLabelText('Camera distance')).toHaveValue('18')

    await user.selectOptions(selector, 'abstract-geometry')
    expect(screen.queryByRole('group', { name: 'Camera' })).not.toBeInTheDocument()
  })

  it('validates and downloads a fully typed Space Journey configuration', async () => {
    const user = userEvent.setup()
    vi.mocked(resolveVisualizerConfig).mockResolvedValueOnce(spaceConfig({ cameraDistance: 22 }))
    const view = render(<BlenderVisualizerPanel jobId="job-1" capabilities={visualizerCapabilities} />)

    await user.selectOptions(screen.getByLabelText('Visualizer preset'), 'space-journey')
    fireEvent.change(screen.getByLabelText('Camera distance'), { target: { value: '22' } })
    await user.click(screen.getByRole('button', { name: 'Download visualizer config' }))

    await waitFor(() => expect(resolveVisualizerConfig).toHaveBeenCalledWith({
      schemaVersion: '1.0.0',
      preset: 'space-journey',
      parameters: {
        ...cloneDefaultSpaceJourneyParameters(),
        cameraDistance: 22,
      },
      seed: 84291,
    }))
    expect(await screen.findByText('Space Journey configuration validated and downloaded.')).toBeInTheDocument()
    expect(createObjectURL).toHaveBeenCalledOnce()
    view.unmount()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:visualizer-download')
  })

  it('omits parameters from the default Abstract Geometry request', async () => {
    const user = userEvent.setup()
    render(<BlenderVisualizerPanel jobId="job-1" capabilities={visualizerCapabilities} />)

    await user.click(screen.getByRole('button', { name: 'Download visualizer config' }))

    await waitFor(() => expect(resolveVisualizerConfig).toHaveBeenCalledOnce())
    expect(vi.mocked(resolveVisualizerConfig).mock.calls[0]?.[0]).toEqual({
      schemaVersion: '1.0.0',
      preset: 'abstract-geometry',
      seed: 84291,
    })
  })

  it('disables both export paths and preset controls while configuration validation is pending', async () => {
    const user = userEvent.setup()
    let finishRequest: ((value: ResolvedVisualizerConfig) => void) | undefined
    vi.mocked(resolveVisualizerConfig).mockReturnValueOnce(new Promise((resolve) => {
      finishRequest = resolve
    }))
    render(<BlenderVisualizerPanel jobId="job-1" capabilities={visualizerCapabilities} />)

    await user.click(screen.getByRole('button', { name: 'Download visualizer config' }))
    expect(screen.getByRole('button', { name: 'Validating configuration...' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Export cue sheet' })).toBeDisabled()
    expect(screen.getByLabelText('Visualizer preset')).toBeDisabled()

    act(() => finishRequest?.(abstractConfig))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Download visualizer config' })).toBeEnabled())
  })

  it('shows safe cue and configuration errors', async () => {
    vi.mocked(exportVisualCues).mockRejectedValueOnce(new Error('Continuous curves require reanalysis.'))
    vi.mocked(resolveVisualizerConfig).mockRejectedValueOnce(new Error('The visualizer parameters are invalid.'))
    const user = userEvent.setup()
    render(<BlenderVisualizerPanel jobId="job-1" capabilities={visualizerCapabilities} />)

    await user.click(screen.getByRole('button', { name: 'Export cue sheet' }))
    expect(await screen.findByText(/Continuous curves require reanalysis/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Download visualizer config' }))
    expect(await screen.findByText(/visualizer parameters are invalid/)).toBeInTheDocument()
  })
})
