import type {
  AnalysisJob,
  AnalysisResult,
  Capabilities,
  Confidence,
  FeatureValue,
  PromptPackage,
} from '../types'

export function feature<T>(value: T, confidence: Confidence = 'high', method = 'synthetic-test-analyzer'): FeatureValue<T> {
  return { value, confidence, method, alternatives: [], userEdited: false, userAccepted: false }
}

export const capabilities: Capabilities = {
  fastMode: { available: true, features: ['rhythm', 'harmony', 'structure', 'production'] },
  deepMode: {
    available: false,
    willFallback: true,
    adapters: [{ id: 'separator', name: 'Stem separator', available: false, reason: 'Not installed' }],
  },
  optionalAnalyzers: [],
  genreTagger: null,
  lyricsAdapter: null,
  promptWriter: null,
  gpuTaskQueue: { workers: 1, active: 0, waiting: 0, policy: 'single-heavy-task' },
  ffmpeg: { available: true, version: '7.1' },
  ffprobe: { available: true, version: '7.1' },
  limits: {
    maxUploadMb: 200,
    maxDurationSeconds: 1200,
    maxPendingJobs: 2,
    maxSingleTrackAnalysisSeconds: 1200,
    maxLongformDurationSeconds: 43200,
    maxSourceUploadBytes: 50 * 1024 ** 3,
    uploadChunkBytes: 32 * 1024 ** 2,
    maxActiveUploads: 3,
    maxActiveAnalyses: 1,
    maxActiveGpuTasks: 1,
    longformScanTimeoutSeconds: 7200,
    minimumFreeDiskBytes: 10 * 1024 ** 3,
  },
  catalogue: null,
  visualCueExportAvailable: true,
  visualCueSheetSchemaVersion: '1.1.0',
  visualFeatureArtifactSchemaVersion: '1.0.0',
  blenderVisualizerPreset: 'abstract-geometry',
  blenderVisualizerDefaultPreset: 'abstract-geometry',
  blenderVisualizerPresets: ['abstract-geometry', 'space-journey'],
  blenderVisualizerConfigSchemaVersion: '1.0.0',
  networkFeaturesEnabled: false,
  retentionPolicy: 'explicit-delete-only',
  automaticAnalysisDeletionEnabled: false,
}

export const promptPackage: PromptPackage = {
  primaryPrompt: 'Driving electronic rock around 120 BPM with a minor-key foundation and crisp drums. Create an original melody, arrangement, and lyrics.',
  compactPrompt: 'Driving 120 BPM electronic rock with crisp drums and an original composition.',
  detailedPrompt: 'Driving electronic rock around 120 BPM, tense minor harmony, crisp drums, deep bass, a rising A-B-A energy arc, and spacious production. Create a distinct original melody, arrangement, and lyrics.',
  exclusions: ['No long intro'],
  arrangementBlueprint: ['Open sparsely', 'Build into section B', 'Return to A with more energy'],
  rationale: [{ phrase: 'around 120 BPM', factPaths: ['rhythm.bpm'] }],
  factsUsed: [
    { path: 'rhythm.bpm', value: 120, role: 'observed' },
    { path: 'harmony.mode', value: 'minor', role: 'observed' },
  ],
  factsOmitted: [{ path: 'melody.pitchContourAvailable', reason: 'Low confidence' }],
  warnings: [],
  engineMode: 'reliable',
  candidates: [{
    id: 'candidate-reliable-0',
    prompt: 'Driving electronic track with a rising energy arc. Create an original melody, arrangement, and any lyrics rather than reproducing the reference recording.',
    shortTitle: 'Reliable prompt',
    engineMode: 'reliable',
    seed: 0,
    modelId: 'trackprompt-deterministic-composer',
    generationParameters: { sampling: false, temperature: 0, topP: 1, repetitionPenalty: 1, maximumTokens: 512, timeoutSeconds: 60 },
    factsUsed: [
      { path: 'rhythm.bpm', value: 120, role: 'observed' },
      { path: 'structure.energyArc', value: 'rising', role: 'observed' },
    ],
    creativeDirectionsUsed: [],
    warnings: [],
  }],
  selectedCandidateId: 'candidate-reliable-0',
  modelId: 'trackprompt-deterministic-composer',
  seed: 0,
  generationParameters: { sampling: false, temperature: 0, topP: 1, repetitionPenalty: 1, maximumTokens: 512, timeoutSeconds: 60 },
  validationWarnings: [],
  deterministicFallbackUsed: false,
}

export const fullCapabilities: Capabilities = {
  ...capabilities,
  deepMode: { available: true, willFallback: false, adapters: [{ id: 'demucs', name: 'Demucs', available: true, selectedDevice: 'cuda', gpuDeviceName: 'NVIDIA GeForce RTX 3060' }] },
  genreTagger: { id: 'clap', name: 'CLAP genre tagger', available: true, modelReady: true, modelId: 'laion/clap-htsat-unfused', effectiveDevice: 'cuda', taxonomyVersion: '2.0.0' },
  lyricsAdapter: { id: 'whisper', name: 'faster-whisper lyrics', available: true, modelReady: true, modelId: 'Systran/faster-whisper-small', effectiveDevice: 'cuda' },
  promptWriter: { id: 'ollama', name: 'Ollama prompt writer', available: true, modelReady: true, modelId: 'qwen2.5:7b-instruct-q4_K_M', effectiveDevice: 'cuda', creativeAvailable: true, experimentalAvailable: true },
}

export const analysis: AnalysisResult = {
  schemaVersion: '1.4.0',
  analysisVersion: '0.5.0',
  jobId: 'job-1',
  capabilities: ['fast-core'],
  requestedMode: 'fast',
  effectiveMode: 'fast',
  file: {
    displayName: 'synthetic-click.wav',
    durationSeconds: 12,
    sampleRate: 44100,
    channels: 2,
    codec: 'pcm_s16le',
    container: 'wav',
    privateMetadata: { title: 'Private title' },
  },
  waveformPeaks: [0.1, 0.5, 0.9, 0.2, 0.7, 0.3],
  signalQuality: {
    sufficientSignal: feature(true),
    clipping: feature(false),
  },
  rhythm: {
    bpm: feature(120),
    meter: feature('4/4', 'medium'),
    grooveDescriptors: feature(['straight', 'driving']),
  },
  harmony: {
    key: feature('A'),
    mode: feature('minor'),
    tonalConfidence: feature('stable', 'medium'),
    chords: feature([
      { chord: 'Am', startSeconds: 0, endSeconds: 4, confidence: 'medium' },
      { chord: 'F', startSeconds: 4, endSeconds: 8, confidence: 'medium' },
    ], 'medium'),
  },
  melody: {
    register: feature('mid'),
    movement: feature('mostly stepwise', 'medium'),
  },
  structure: {
    sections: [
      { id: 'a1', neutralLabel: 'A', inferredLabel: 'Intro', startSeconds: 0, endSeconds: 4, confidence: 'medium', energy: 0.25, density: 0.2, instruments: ['drums'] },
      { id: 'b1', neutralLabel: 'B', inferredLabel: 'Build', startSeconds: 4, endSeconds: 8, confidence: 'medium', energy: 0.78, density: 0.7, instruments: ['drums', 'bass'] },
      { id: 'a2', neutralLabel: 'A′', startSeconds: 8, endSeconds: 12, confidence: 'high', energy: 0.6, density: 0.55, instruments: ['drums', 'bass', 'synth'] },
    ],
    energyArc: feature('rising with a short release'),
  },
  timbre: {
    descriptors: feature(['bright', 'percussive'], 'medium'),
  },
  instrumentation: {
    candidates: feature([{ name: 'drums', prominence: 'high', confidence: 'high', sections: ['A', 'B'] }]),
    coarseCategoriesOnly: true,
  },
  vocals: {
    presence: feature('not detected', 'medium'),
  },
  production: {
    integratedLoudnessLufs: feature(-14.2),
    macroDynamicRangeDb: feature(8.4, 'medium'),
    stereoWidth: feature(0.62),
    spaciousnessProxy: feature('moderate', 'medium'),
  },
  styleAndMood: {
    broadStyle: feature(['electronic rock'], 'medium'),
    mood: feature(['tense', 'focused'], 'medium'),
    energy: feature('high'),
  },
  genreAnalysis: {
    broadCandidates: [{ id: 'electronic', label: 'electronic', canonicalLabel: 'electronic', similarity: 0.31, confidence: 'medium', accepted: false, rejected: false, locked: false, userEdited: false, custom: false }],
    subgenreCandidates: [{ id: 'melodic-techno', label: 'melodic techno', canonicalLabel: 'melodic techno', parent: 'electronic', similarity: 0.27, confidence: 'medium', accepted: false, rejected: false, locked: false, userEdited: false, custom: false }],
    blendCandidates: ['melodic techno with progressive-house influence'],
    descriptiveTags: [],
    windowEvidence: [{ id: 'full_mix:w1', kind: 'middle', startSeconds: 1, endSeconds: 9, topLabels: ['electronic'], similarities: { electronic: 0.31 }, weight: 0.9, representativeness: 0.85, vocalDominant: false, percussionDominant: true, sectionIds: ['section-1'], analysisView: 'full_mix' }],
    sectionEvidence: {},
    primaryProductionGenre: { value: 'electronic', confidence: 'medium', method: 'private accompaniment view', supportingWindowIds: ['full_mix:w1'], supportingSectionIds: ['section-1'], alternatives: [], ambiguity: null, source: 'detected', accepted: false, enabledForPrompt: true },
    secondaryProductionGenres: { value: ['melodic techno'], confidence: 'medium', method: 'family-gated ranking', supportingWindowIds: ['full_mix:w1'], supportingSectionIds: ['section-1'], alternatives: [], ambiguity: null, source: 'detected', accepted: false, enabledForPrompt: true },
    vocalDeliveryStyle: { value: ['spoken-rhythmic'], confidence: 'medium', method: 'private vocal acoustics', supportingWindowIds: [], supportingSectionIds: ['section-1'], alternatives: [], ambiguity: null, source: 'detected', accepted: false, enabledForPrompt: true },
    vocalGenreInfluences: { value: ['hip-hop'], confidence: 'low', method: 'component evidence', supportingWindowIds: [], supportingSectionIds: ['section-1'], alternatives: [], ambiguity: null, source: 'detected', accepted: false, enabledForPrompt: true },
    sectionGenreEvidence: [],
    overallGenreBlend: { value: 'melodic techno production with hip-hop vocal influence', confidence: 'medium', method: 'layer synthesis', supportingWindowIds: ['full_mix:w1'], supportingSectionIds: ['section-1'], alternatives: [], ambiguity: null, source: 'detected', accepted: false, enabledForPrompt: true },
    confidence: 'medium',
    ambiguity: 'Electronic alternatives are close.',
    method: 'hierarchical CLAP cosine similarity; not probability',
    modelId: 'laion/clap-htsat-unfused',
    taxonomyVersion: '2.0.0',
    selectedDevice: 'cuda',
    agreementAcrossWindows: 0.8,
    warnings: [],
    userEdited: false,
    userAccepted: false,
    disabledForPrompt: false,
  },
  lyricsSummary: {
    enabled: true,
    status: 'completed',
    adapterId: 'faster-whisper',
    modelId: 'Systran/faster-whisper-small',
    selectedDevice: 'cuda',
    language: 'en',
    languageConfidence: 'medium',
    transcriptAvailable: true,
    segmentCount: 1,
    activeSectionIds: [],
    vocalWordDensity: 'sparse',
    nonLexicalVocalizationTendency: 'unknown',
    abstractThemes: ['persistence and movement'],
    themeConfidence: 'medium',
    themesUserApproved: false,
    warnings: ['Approximate transcript.'],
  },
  warnings: ['Meter is approximate.'],
  analyzerVersions: { 'trackprompt-core': '0.5.0' },
  createdAt: '2026-07-15T10:00:00Z',
  disabledFeaturePaths: [],
}

export function queuedJob(): AnalysisJob {
  return {
    jobId: 'job-1',
    status: 'queued',
    requestedMode: 'fast',
    mode: 'fast',
    stage: 'queued',
    message: 'Waiting for a local worker.',
    progress: 0,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  }
}

export function completedJob(overrides: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    ...queuedJob(),
    status: 'completed',
    stage: 'completed',
    message: 'Analysis complete.',
    progress: 100,
    analysis,
    promptPackage,
    ...overrides,
  }
}
