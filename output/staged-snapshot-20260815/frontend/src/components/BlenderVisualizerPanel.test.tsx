import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { exportVisualCues } from '../api'
import type { TrackPromptVisualCueSheet } from '../types'
import { BlenderVisualizerPanel } from './BlenderVisualizerPanel'

vi.mock('../api', () => ({ exportVisualCues: vi.fn() }))

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

describe('BlenderVisualizerPanel', () => {
  const createObjectURL = vi.fn(() => 'blob:visual-cues')
  const revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)

  beforeEach(() => {
    vi.mocked(exportVisualCues).mockResolvedValue({
      cueSheet,
      blob: new Blob(['{}'], { type: 'application/json' }),
      filename: 'trackprompt-job-1-visual-cues.json',
    })
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('exports selected options, reports actual counts, and cleans the object URL', async () => {
    const user = userEvent.setup()
    const view = render(<BlenderVisualizerPanel jobId="job-1" />)
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
    expect(createObjectURL).toHaveBeenCalledOnce()
    view.unmount()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:visual-cues')
  })

  it('shows a safe export error', async () => {
    vi.mocked(exportVisualCues).mockRejectedValueOnce(new Error('Continuous curves require reanalysis.'))
    const user = userEvent.setup()
    render(<BlenderVisualizerPanel jobId="job-1" />)
    await user.click(screen.getByRole('button', { name: 'Export cue sheet' }))
    expect(await screen.findByText(/Continuous curves require reanalysis/)).toBeInTheDocument()
  })
})
