import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { analysis, capabilities, completedJob, fullCapabilities, promptPackage, queuedJob } from './test/factories'

const api = vi.hoisted(() => ({
  getCapabilities: vi.fn(),
  createAnalysis: vi.fn(),
  getAnalysis: vi.fn(),
  cancelAnalysis: vi.fn(),
  patchAnalysis: vi.fn(),
  patchGenre: vi.fn(),
  getLyrics: vi.fn(),
  patchLyrics: vi.fn(),
  deleteLyrics: vi.fn(),
  generatePrompt: vi.fn(),
  deleteAnalysis: vi.fn(),
  subscribeToAnalysisEvents: vi.fn(),
  callbacks: undefined as undefined | {
    onOpen?: () => void
    onEvent: (event: Record<string, unknown>) => void
    onTerminal: (status: 'completed' | 'failed' | 'cancelled' | 'expired') => void
    onConnectionError: () => void
  },
}))

const wave = vi.hoisted(() => ({
  load: vi.fn().mockResolvedValue(undefined),
  loadBlob: vi.fn().mockResolvedValue(undefined),
  on: vi.fn().mockReturnValue(vi.fn()),
  destroy: vi.fn(),
  setTime: vi.fn(),
  playPause: vi.fn().mockResolvedValue(undefined),
  setMuted: vi.fn(),
}))

vi.mock('./api', () => ({
  ApiError: class ApiError extends Error {},
  getCapabilities: api.getCapabilities,
  createAnalysis: api.createAnalysis,
  getAnalysis: api.getAnalysis,
  cancelAnalysis: api.cancelAnalysis,
  patchAnalysis: api.patchAnalysis,
  patchGenre: api.patchGenre,
  getLyrics: api.getLyrics,
  patchLyrics: api.patchLyrics,
  deleteLyrics: api.deleteLyrics,
  generatePrompt: api.generatePrompt,
  deleteAnalysis: api.deleteAnalysis,
  subscribeToAnalysisEvents: api.subscribeToAnalysisEvents,
  exportUrl: (_jobId: string, format: string) => `/api/export.${format}`,
  audioUrl: (jobId: string) => `/api/analyses/${jobId}/audio`,
  lyricsExportUrl: (jobId: string) => `/api/analyses/${jobId}/lyrics/export`,
}))

vi.mock('wavesurfer.js', () => ({ default: { create: () => wave } }))

async function selectValidFile(user: ReturnType<typeof userEvent.setup>): Promise<File> {
  const file = new File(['synthetic audio'], 'click.wav', { type: 'audio/wav' })
  await user.upload(screen.getByLabelText('Audio file'), file)
  await user.click(screen.getByRole('checkbox', { name: /I have permission/i }))
  return file
}

async function startCompleted(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  api.createAnalysis.mockResolvedValueOnce(completedJob())
  await selectValidFile(user)
  await user.click(screen.getByRole('button', { name: 'Analyze track' }))
  await screen.findByRole('heading', { name: 'synthetic-click.wav' })
}

describe('TrackPrompt Studio primary flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getCapabilities.mockResolvedValue(capabilities)
    api.createAnalysis.mockResolvedValue(queuedJob())
    api.getAnalysis.mockResolvedValue(completedJob())
    api.cancelAnalysis.mockResolvedValue({ ...queuedJob(), status: 'cancelled', stage: 'cancelled', message: 'Cancelled.' })
    api.patchAnalysis.mockResolvedValue(completedJob())
    api.patchGenre.mockResolvedValue({ ...analysis.genreAnalysis, broadCandidates: analysis.genreAnalysis?.broadCandidates.map((item) => ({ ...item, accepted: true })) })
    api.getLyrics.mockResolvedValue({
      schemaVersion: '1.0.0', jobId: 'job-1', language: 'en', modelId: 'Systran/faster-whisper-small', selectedDevice: 'cuda', warnings: [], userEdited: false, createdAt: '2026-07-16T00:00:00Z',
      segments: [{ id: 'segment-1', startSeconds: 1, endSeconds: 2.5, text: 'synthetic local phrase', confidence: 'medium', noSpeechScore: 0.1, qualityFlags: [], userEdited: false }],
    })
    api.patchLyrics.mockImplementation(async () => api.getLyrics())
    api.deleteLyrics.mockResolvedValue(undefined)
    api.generatePrompt.mockResolvedValue(promptPackage)
    api.deleteAnalysis.mockResolvedValue(undefined)
    api.subscribeToAnalysisEvents.mockImplementation((_jobId: string, callbacks: typeof api.callbacks) => {
      api.callbacks = callbacks
      callbacks?.onOpen?.()
      return vi.fn()
    })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
  })

  it('selects a file, requires permission, and starts Fast analysis', async () => {
    const user = userEvent.setup()
    render(<App />)
    const file = new File(['synthetic audio'], 'click.wav', { type: 'audio/wav' })
    await user.upload(screen.getByLabelText('Audio file'), file)
    expect(screen.getByRole('button', { name: 'Analyze track' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /I have permission/i }))
    await user.click(screen.getByRole('button', { name: 'Analyze track' }))
    expect(api.createAnalysis).toHaveBeenCalledWith(file, 'fast', {
      enableGenreAnalysis: false,
      enableLyricsAnalysis: false,
      lyricsConsentConfirmed: false,
      deriveLyricalThemes: false,
      allowFeatureFallback: false,
    })
    expect(await screen.findByRole('heading', { name: 'Queued' })).toBeInTheDocument()
  })

  it('requires fresh permission confirmation when the selected file changes', async () => {
    const user = userEvent.setup()
    render(<App />)
    const first = new File(['first synthetic audio'], 'first.wav', { type: 'audio/wav' })
    const second = new File(['second synthetic audio'], 'second.wav', { type: 'audio/wav' })
    const input = screen.getByLabelText('Audio file')
    await user.upload(input, first)
    await user.click(screen.getByRole('checkbox', { name: /I have permission/i }))
    expect(screen.getByRole('button', { name: 'Analyze track' })).toBeEnabled()

    await user.upload(input, second)
    expect(screen.getByRole('checkbox', { name: /I have permission/i })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Analyze track' })).toBeDisabled()

    await user.click(screen.getByRole('checkbox', { name: /I have permission/i }))
    await user.click(screen.getByRole('button', { name: 'Analyze track' }))
    expect(api.createAnalysis).toHaveBeenCalledWith(second, 'fast', {
      enableGenreAnalysis: false,
      enableLyricsAnalysis: false,
      lyricsConsentConfirmed: false,
      deriveLyricalThemes: false,
      allowFeatureFallback: false,
    })
  })

  it('reports local file errors before upload', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.upload(screen.getByLabelText('Audio file'), new File([], 'empty.wav', { type: 'audio/wav' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That file is empty')
    expect(api.createAnalysis).not.toHaveBeenCalled()
  })

  it('shows safe server upload errors', async () => {
    const user = userEvent.setup()
    api.createAnalysis.mockRejectedValueOnce(new Error('The media stream is malformed or unsupported.'))
    render(<App />)
    await selectValidFile(user)
    await user.click(screen.getByRole('button', { name: 'Analyze track' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('malformed or unsupported')
  })

  it('blocks analysis when required local media tools are unavailable', async () => {
    const user = userEvent.setup()
    api.getCapabilities.mockResolvedValueOnce({ ...capabilities, ffmpeg: { available: false } })
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('FFmpeg, ffprobe, or Fast analysis is unavailable')
    await selectValidFile(user)
    expect(screen.getByRole('button', { name: 'Analyze track' })).toBeDisabled()
  })

  it('renders real SSE progress and supports cancellation', async () => {
    const user = userEvent.setup()
    render(<App />)
    await selectValidFile(user)
    await user.click(screen.getByRole('button', { name: 'Analyze track' }))
    act(() => {
      api.callbacks?.onEvent({
        jobId: 'job-1',
        status: 'analyzing_core',
        stage: 'analyzing_rhythm',
        message: 'Comparing tempo cues.',
        progress: 42,
        timestamp: new Date().toISOString(),
      })
    })
    expect(await screen.findByText('Comparing tempo cues.')).toBeInTheDocument()
    expect(screen.getByLabelText('Server-reported progress 42 percent')).toBeInTheDocument()
    act(() => {
      api.callbacks?.onEvent({
        jobId: 'job-1',
        status: 'analyzing_core',
        stage: 'cancellation_requested',
        message: 'Cancellation requested; stopping at the current safe boundary',
        progress: 42,
        timestamp: new Date().toISOString(),
      })
    })
    expect(await screen.findByRole('heading', { name: 'Stopping safely' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Queued' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel analysis' }))
    expect(api.cancelAnalysis).toHaveBeenCalledWith('job-1')
    expect(await screen.findByRole('heading', { name: 'Analysis cancelled' })).toBeInTheDocument()
  })

  it('applies the server-reported effective mode from live progress', async () => {
    const user = userEvent.setup()
    api.createAnalysis.mockResolvedValueOnce({ ...queuedJob(), requestedMode: 'deep', mode: 'deep' })
    render(<App />)
    await selectValidFile(user)
    await user.click(screen.getByRole('radio', { name: /Deep/ }))
    await user.click(screen.getByRole('button', { name: 'Analyze track' }))
    act(() => {
      api.callbacks?.onEvent({
        jobId: 'job-1',
        status: 'separating_stems',
        mode: 'deep',
        stage: 'separating_stems',
        message: 'Separating private stems.',
        progress: 82,
        timestamp: new Date().toISOString(),
      })
    })
    expect(await screen.findByText('Separating private stems.')).toBeInTheDocument()
    expect(screen.getByText('DEEP')).toBeInTheDocument()
    expect(screen.queryByText(/FALLBACK/)).not.toBeInTheDocument()
  })

  it('renders results and allows editing and disabling detected facts', async () => {
    const user = userEvent.setup()
    const editedAnalysis = {
      ...analysis,
      rhythm: { ...analysis.rhythm, bpm: { ...(analysis.rhythm.bpm as object), value: 124, userEdited: true } },
    }
    const restoredButDisabledAnalysis = {
      ...analysis,
      disabledFeaturePaths: ['rhythm.bpm'],
    }
    api.patchAnalysis
      .mockResolvedValueOnce(completedJob({ analysis: editedAnalysis }))
      .mockResolvedValueOnce(completedJob({ analysis: { ...editedAnalysis, disabledFeaturePaths: ['rhythm.bpm'] } }))
      .mockResolvedValueOnce(completedJob({ analysis: restoredButDisabledAnalysis }))
    render(<App />)
    await startCompleted(user)
    expect(screen.getByText('120 BPM')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Rhythm & harmony' }))
    await user.click(screen.getAllByRole('button', { name: 'Edit' })[0]!)
    const input = screen.getByLabelText('Edit Bpm')
    await user.clear(input)
    await user.type(input, '124')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(api.patchAnalysis).toHaveBeenCalledWith('job-1', [{ path: 'rhythm.bpm', value: 124 }])
    expect(await screen.findByText('124')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Disable for prompt' })[0]!)
    expect(api.patchAnalysis).toHaveBeenLastCalledWith('job-1', [{ path: 'rhythm.bpm', disabledForPrompt: true }])
    await user.click(screen.getAllByRole('button', { name: 'Restore detected' })[0]!)
    expect(api.patchAnalysis).toHaveBeenLastCalledWith('job-1', [{ path: 'rhythm.bpm', restoreDetected: true }])
    expect(await screen.findByText('120')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Use in prompt' })[0]).toBeInTheDocument()
  })

  it('persists acceptance so an explicitly reviewed low-confidence fact can enter prompts', async () => {
    const user = userEvent.setup()
    const lowConfidenceAnalysis = {
      ...analysis,
      rhythm: {
        ...analysis.rhythm,
        meter: { ...(analysis.rhythm.meter as object), confidence: 'low', userAccepted: false },
      },
    }
    const acceptedAnalysis = {
      ...lowConfidenceAnalysis,
      rhythm: {
        ...lowConfidenceAnalysis.rhythm,
        meter: { ...(lowConfidenceAnalysis.rhythm.meter as object), userAccepted: true },
      },
    }
    api.createAnalysis.mockResolvedValueOnce(completedJob({ analysis: lowConfidenceAnalysis }))
    api.patchAnalysis.mockResolvedValueOnce(completedJob({ analysis: acceptedAnalysis, promptPackage: undefined }))
    render(<App />)
    await selectValidFile(user)
    await user.click(screen.getByRole('button', { name: 'Analyze track' }))
    await screen.findByRole('heading', { name: 'synthetic-click.wav' })
    await user.click(screen.getByRole('tab', { name: 'Rhythm & harmony' }))
    const meterCard = screen.getByText('Meter').closest('article')
    expect(meterCard).not.toBeNull()
    await user.click(within(meterCard!).getByRole('button', { name: 'Accept' }))
    expect(api.patchAnalysis).toHaveBeenCalledWith('job-1', [
      { path: 'rhythm.meter', acceptedForPrompt: true },
    ])
    expect(await within(meterCard!).findByText('Accepted for prompt')).toBeInTheDocument()
  })

  it('copies, counts edits, and confirms before regeneration replaces them', async () => {
    const user = userEvent.setup()
    render(<App />)
    await startCompleted(user)
    await user.click(screen.getByRole('tab', { name: 'Prompt' }))
    const editor = screen.getByLabelText('Editable primary prompt')
    const initialLength = promptPackage.primaryPrompt.length
    expect(screen.getByText(`${initialLength} characters`)).toBeInTheDocument()
    await user.type(editor, ' Handmade ending.')
    expect(screen.getByText(`${initialLength + 17} characters`)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Overview' }))
    await user.click(screen.getByRole('tab', { name: 'Prompt' }))
    expect(screen.getByLabelText('Editable primary prompt')).toHaveValue(
      `${promptPackage.primaryPrompt} Handmade ending.`,
    )
    expect(screen.getByText('Manual edits protected')).toBeInTheDocument()
    const clipboardWrite = vi.spyOn(navigator.clipboard, 'writeText')
    await user.click(screen.getByRole('button', { name: 'Copy prompt' }))
    expect(clipboardWrite).toHaveBeenCalledWith(expect.stringContaining('Handmade ending.'))
    expect(await screen.findByText('Prompt copied')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Generate candidates' }))
    expect(screen.getByRole('dialog', { name: 'Replace your manual edits?' })).toBeInTheDocument()
    expect(api.generatePrompt).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Replace prompt' }))
    await waitFor(() => expect(api.generatePrompt).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('Manual edits protected')).not.toBeInTheDocument()
  })

  it('keeps intent-specific prompt controls coherent and requires a genre-transfer target', async () => {
    const user = userEvent.setup()
    render(<App />)
    await startCompleted(user)
    await user.click(screen.getByRole('tab', { name: 'Prompt' }))

    const intent = screen.getByLabelText('Generation intent')
    await user.selectOptions(intent, 'instrumental_reinterpretation')
    expect(screen.getByRole('checkbox', { name: /^Instrumental output/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /^Instrumental output/ })).toBeDisabled()
    expect(screen.getByLabelText('Desired vocal presentation')).toBeDisabled()

    await user.selectOptions(intent, 'change_instrumentation_preserve_structure')
    expect(screen.getByRole('checkbox', { name: /^Instrumental output/ })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Instrumentation' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Instrumentation' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Section structure' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Section structure' })).toBeDisabled()

    await user.selectOptions(intent, 'genre_transfer')
    expect(screen.getByLabelText('Target genre')).toBeRequired()
    await user.click(screen.getByRole('button', { name: 'Generate candidates' }))
    expect(await screen.findByText('Enter a target genre before generating a genre transfer.')).toBeInTheDocument()
    expect(api.generatePrompt).not.toHaveBeenCalled()

    await user.type(screen.getByLabelText('Target genre'), 'synthwave')
    await user.click(screen.getByRole('button', { name: 'Generate candidates' }))
    await waitFor(() => expect(api.generatePrompt).toHaveBeenCalledWith('job-1', expect.objectContaining({
      generationIntent: 'genre_transfer',
      targetGenre: 'synthwave',
    })))
  })

  it('invalidates a stale generated prompt while preserving manual text', async () => {
    const user = userEvent.setup()
    api.patchAnalysis.mockResolvedValueOnce({
      ...completedJob(),
      analysis: { ...analysis, disabledFeaturePaths: ['rhythm.bpm'] },
      promptPackage: undefined,
    })
    render(<App />)
    await startCompleted(user)
    await user.click(screen.getByRole('tab', { name: 'Prompt' }))
    await user.type(screen.getByLabelText('Editable primary prompt'), ' Keep this manual ending.')
    await user.click(screen.getByRole('tab', { name: 'Rhythm & harmony' }))
    await user.click(screen.getAllByRole('button', { name: 'Disable for prompt' })[0]!)
    await user.click(screen.getByRole('tab', { name: 'Prompt' }))
    expect(screen.getByLabelText('Editable primary prompt')).toHaveValue(
      `${promptPackage.primaryPrompt} Keep this manual ending.`,
    )
    expect(await screen.findByText(/manual text is preserved/i)).toBeInTheDocument()
    expect(screen.getByText('Manual edits protected')).toBeInTheDocument()
    expect(screen.queryByText('Synced')).not.toBeInTheDocument()
  })

  it('falls back when the async clipboard API rejects', async () => {
    const user = userEvent.setup()
    const execCommand = vi.fn().mockReturnValue(true)
    Object.defineProperty(document, 'execCommand', { configurable: true, value: execCommand })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })
    render(<App />)
    await startCompleted(user)
    await user.click(screen.getByRole('tab', { name: 'Prompt' }))
    await user.click(screen.getByRole('button', { name: 'Copy prompt' }))
    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(await screen.findByText('Prompt copied')).toBeInTheDocument()
  })

  it('deletes all job data after explicit confirmation', async () => {
    const user = userEvent.setup()
    render(<App />)
    await startCompleted(user)
    await user.click(screen.getByRole('button', { name: 'Delete analysis' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete this analysis and audio?' })
    expect(within(dialog).getByText(/uploaded audio, temporary stems/i)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Close dialog' })).toHaveFocus()
    await user.tab({ shift: true })
    const deleteButton = within(dialog).getByRole('button', { name: 'Delete everything' })
    expect(deleteButton).toHaveFocus()
    await user.click(deleteButton)
    expect(api.deleteAnalysis).toHaveBeenCalledWith('job-1')
    expect(await screen.findByRole('heading', { name: 'Choose your track' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('temporary data deleted')
  })

  it('supports keyboard tabs, section seeking, and section correction', async () => {
    const user = userEvent.setup()
    const editedSections = [...(analysis.structure.sections ?? [])]
    editedSections[1] = { ...editedSections[1]!, inferredLabel: 'chorus', startSeconds: 4.1, endSeconds: 7.9 }
    api.patchAnalysis.mockResolvedValueOnce(completedJob({
      analysis: { ...analysis, structure: { ...analysis.structure, sections: editedSections } },
      promptPackage: undefined,
    }))
    render(<App />)
    await startCompleted(user)
    const overview = screen.getByRole('tab', { name: 'Overview' })
    overview.focus()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'Timeline' })).toHaveFocus()
    const section = await screen.findByRole('button', { name: 'Seek to Build at 0:04' })
    await user.click(section)
    expect(wave.setTime).toHaveBeenCalledWith(4)
    await user.click(screen.getByRole('button', { name: 'Edit section Build' }))
    const label = screen.getByLabelText('Section label')
    await user.clear(label)
    await user.type(label, 'chorus')
    const start = screen.getByLabelText('Start seconds')
    await user.clear(start)
    await user.type(start, '4.1')
    const end = screen.getByLabelText('End seconds')
    await user.clear(end)
    await user.type(end, '7.9')
    await user.click(screen.getByRole('button', { name: 'Save section' }))
    expect(api.patchAnalysis).toHaveBeenCalledWith('job-1', [
      { path: 'structure.sections.1.inferredLabel', value: 'chorus' },
      { path: 'structure.sections.1.startSeconds', value: 4.1 },
      { path: 'structure.sections.1.endSeconds', value: 7.9 },
    ])
    expect(await screen.findByRole('button', { name: 'Edit section chorus' })).toBeInTheDocument()
  })

  it('shows prompt engine requirements, seeds, candidate count, and safe unavailable states', async () => {
    const user = userEvent.setup()
    render(<App />)
    await startCompleted(user)
    await user.click(screen.getByRole('tab', { name: 'Prompt' }))
    expect(screen.getByRole('radio', { name: /Creative/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /Experimental/ })).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Candidate count'), '1')
    await user.click(screen.getByRole('button', { name: 'New seed' }))
    expect(screen.getByLabelText('Optional seed')).not.toHaveValue(null)
    expect(screen.getByRole('button', { name: 'Reuse this seed' })).toBeEnabled()
  })

  it('enforces Deep mode and extra transcript consent for Lyrics analysis', async () => {
    const user = userEvent.setup()
    api.getCapabilities.mockResolvedValueOnce(fullCapabilities)
    render(<App />)
    await screen.findByText('CLAP genre tagger')
    const file = await selectValidFile(user)
    await user.click(screen.getByRole('radio', { name: /Deep/ }))
    await user.click(screen.getByRole('checkbox', { name: /Analyze lyrical language and prosody/i }))
    expect(screen.getByRole('button', { name: 'Analyze track' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /permission to create a private approximate transcript/i }))
    await user.click(screen.getByRole('checkbox', { name: /Genre and style tagging/i }))
    await user.click(screen.getByRole('button', { name: 'Analyze track' }))
    expect(api.createAnalysis).toHaveBeenCalledWith(file, 'deep', expect.objectContaining({
      enableGenreAnalysis: true,
      enableLyricsAnalysis: true,
      lyricsConsentConfirmed: true,
    }))
  })

  it('reviews genre candidates with similarity, acceptance, editing, and disable controls', async () => {
    const user = userEvent.setup()
    render(<App />)
    await startCompleted(user)
    await user.click(screen.getByRole('tab', { name: 'Genre & style' }))
    expect(screen.getByText('similarity 0.310')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Accept' })[0]!)
    expect(api.patchGenre).toHaveBeenCalledWith('job-1', { updates: [{ candidateId: 'electronic', accepted: true }] })
    await user.click(screen.getByRole('checkbox', { name: /Use accepted genre in prompt/i }))
    expect(api.patchGenre).toHaveBeenCalledWith('job-1', { disabledForPrompt: true })
  })

  it('loads, seeks, edits, exports, and explicitly deletes the private transcript', async () => {
    const user = userEvent.setup()
    render(<App />)
    await startCompleted(user)
    await user.click(screen.getByRole('tab', { name: 'Lyrics' }))
    expect(await screen.findByDisplayValue('synthetic local phrase')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Explicit transcript export/i })).toHaveAttribute('href', '/api/analyses/job-1/lyrics/export')
    await user.click(screen.getByRole('button', { name: 'Delete complete transcript' }))
    await user.click(within(screen.getByRole('dialog', { name: 'Delete the private transcript?' })).getByRole('button', { name: 'Delete transcript' }))
    expect(api.deleteLyrics).toHaveBeenCalledWith('job-1')
  })

  it('shows candidate comparison and protects manual edits when choosing a candidate', async () => {
    const user = userEvent.setup()
    render(<App />)
    await startCompleted(user)
    await user.click(screen.getByRole('tab', { name: 'Prompt' }))
    expect(screen.getByRole('heading', { name: 'Candidate comparison' })).toBeInTheDocument()
    const editor = screen.getByLabelText('Editable primary prompt')
    await user.type(editor, ' protected edit')
    await user.click(screen.getByRole('button', { name: 'Use this prompt' }))
    expect(screen.getByRole('dialog', { name: 'Replace your manual edits?' })).toBeInTheDocument()
  })
})
