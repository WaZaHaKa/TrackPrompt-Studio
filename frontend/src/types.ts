export type AnalysisMode = 'fast' | 'deep'

export type JobStatus =
  | 'queued'
  | 'validating'
  | 'decoding'
  | 'analyzing_core'
  | 'separating_stems'
  | 'analyzing_deep'
  | 'transcribing_lyrics'
  | 'deriving_lyrical_themes'
  | 'tagging_genre'
  | 'generating_prompt'
  | 'generating_candidates'
  | 'validating_candidates'
  | 'completed'
  | 'cancelled'
  | 'failed'
  | 'expired'

export type AnalysisStage =
  | 'queued'
  | 'validating'
  | 'decoding'
  | 'inspecting_signal'
  | 'analyzing_rhythm'
  | 'analyzing_harmony'
  | 'segmenting_structure'
  | 'analyzing_production'
  | 'extracting_visual_features'
  | 'separating_stems'
  | 'running_enhanced_taggers'
  | 'transcribing_lyrics'
  | 'deriving_lyrical_themes'
  | 'tagging_genre'
  | 'generating_candidates'
  | 'validating_candidates'
  | 'composing_prompt'
  | 'finalizing'
  | 'completed'
  | 'cancellation_requested'
  | 'cancelled'
  | 'failed'
  | 'expired'

export type Confidence = 'low' | 'medium' | 'high' | 'unknown'
export type EvidenceKind =
  | 'direct_measurement'
  | 'strong_estimate'
  | 'heuristic'
  | 'proxy'
  | 'unavailable'
  | 'ambiguous'

export interface FeatureValue<T = unknown> {
  value: T | null
  confidence: Confidence
  score?: number
  method: string
  alternatives?: unknown[]
  warning?: string
  evidenceKind?: EvidenceKind
  userEdited: boolean
  userAccepted: boolean
}

export interface AnalysisFile {
  displayName?: string
  durationSeconds?: number
  sampleRate?: number
  channels?: number
  codec?: string
  container?: string
  bitRate?: number | null
  [key: string]: unknown
}

export interface AnalysisSection {
  id: string
  neutralLabel: string
  inferredLabel?: string | null
  startSeconds: number
  endSeconds: number
  confidence: Confidence
  repetitionGroup?: string | null
  energy?: number | FeatureValue<number> | null
  loudness?: number | FeatureValue<number> | null
  density?: number | FeatureValue<number> | null
  instruments?: string[]
  vocalActivity?: string | FeatureValue<unknown> | null
  harmonySummary?: string | null
  transitionIn?: string | null
  transitionOut?: string | null
  boundaryConfidence?: Confidence | null
  deepEvidence?: {
    relativeRms: Record<string, number>
    activity: Record<string, string>
    method: string
    confidence: Confidence
  } | null
}

export type AnalysisGroup = Record<string, unknown>

export interface StructureGroup extends AnalysisGroup {
  sections?: AnalysisSection[]
  waveformPeaks?: number[] | Array<{ min: number; max: number }>
  energyCurve?: number[]
}

export interface AnalysisResult {
  schemaVersion: string
  analysisVersion: string
  jobId: string
  capabilities: unknown
  requestedMode: AnalysisMode
  effectiveMode: AnalysisMode
  file: AnalysisFile
  waveformPeaks: number[]
  signalQuality: AnalysisGroup
  rhythm: AnalysisGroup
  harmony: AnalysisGroup
  melody: AnalysisGroup
  structure: StructureGroup
  timbre: AnalysisGroup
  instrumentation: AnalysisGroup
  vocals: AnalysisGroup
  production: AnalysisGroup
  styleAndMood: AnalysisGroup
  genreAnalysis?: GenreAnalysis | null
  lyricsSummary?: LyricsAnalysisSummary | null
  warnings: string[]
  analyzerVersions: Record<string, string>
  deepDiagnostics?: {
    adapterId?: string | null
    method?: string | null
    selectedDevice: string
    torchInstalled: boolean
    torchVersion?: string | null
    cudaBuildSupport: boolean
    cudaRuntimeAvailable: boolean
    gpuDeviceName?: string | null
    fallbackReason?: string | null
    stemRelativeRms: Record<string, number>
  } | null
  createdAt: string
  disabledFeaturePaths: string[]
  [key: string]: unknown
}

export type VisualCurveDetail = 'compact' | 'balanced' | 'detailed'

export interface VisualCuePreferences {
  fps: 24 | 25 | 30 | 50 | 60
  includeBeats: boolean
  includeOnsets: boolean
  includeStemEvidence: boolean
  includeCurves: boolean
  curveDetail: VisualCurveDetail
}

export const BLENDER_VISUALIZER_PRESETS = ['abstract-geometry', 'space-journey'] as const

export type BlenderVisualizerPreset = typeof BLENDER_VISUALIZER_PRESETS[number]

export const SPACE_JOURNEY_PALETTES = [
  'andromeda',
  'deep-space',
  'cyan-violet',
  'violet-magenta',
  'monochrome-blue',
  'dark-amber',
] as const

export type SpaceJourneyPalette = typeof SPACE_JOURNEY_PALETTES[number]

export interface SpaceJourneyParameters {
  cameraDistance: number
  cameraOrbitSpeed: number
  ringThickness: number
  ringOcclusion: number
  palette: SpaceJourneyPalette
  glowStrength: number
  shardDensity: number
  fogDepth: number
  bassResponse: number
  drumResponse: number
  vocalResponse: number
}

interface VisualizerConfigRequestBase {
  schemaVersion: '1.0.0'
  seed?: number
}

export type VisualizerConfigRequest =
  | (VisualizerConfigRequestBase & {
      preset: 'abstract-geometry'
      parameters?: never
    })
  | (VisualizerConfigRequestBase & {
      preset: 'space-journey'
      parameters?: Partial<SpaceJourneyParameters>
    })

interface ResolvedVisualizerConfigBase {
  schemaVersion: '1.0.0'
  seed: number
  defaultedParameters: string[]
  warnings: string[]
}

export type ResolvedVisualizerConfig =
  | (ResolvedVisualizerConfigBase & {
      preset: 'abstract-geometry'
      parameters: Record<string, never>
    })
  | (ResolvedVisualizerConfigBase & {
      preset: 'space-journey'
      parameters: SpaceJourneyParameters
    })

export function isBlenderVisualizerPreset(value: unknown): value is BlenderVisualizerPreset {
  return typeof value === 'string'
    && (BLENDER_VISUALIZER_PRESETS as readonly string[]).includes(value)
}

export function isSpaceJourneyPalette(value: unknown): value is SpaceJourneyPalette {
  return typeof value === 'string'
    && (SPACE_JOURNEY_PALETTES as readonly string[]).includes(value)
}

export interface VisualCueEvent {
  index: number
  timeSeconds: number
  frame: number
  confidence: Confidence
  strength: number | null
  sourcePath: string
}

export interface VisualCueSection {
  id: string
  neutralLabel: string
  inferredLabel: string | null
  startSeconds: number
  endSeconds: number
  startFrame: number
  endFrame: number
  energy: number | null
  loudness: number | null
  confidence: Confidence
  boundaryConfidence: Confidence
  repetitionGroup: string | null
  vocalActivity: string | null
  instruments: string[]
  stemActivity: Record<string, string>
  stemRelativeRms: Record<string, number>
  sourcePath: string
}

export interface VisualCueTransition {
  id: string
  timeSeconds: number
  frame: number
  fromSectionId: string
  toSectionId: string
  energyBefore: number | null
  energyAfter: number | null
  energyDelta: number | null
  direction: 'rising' | 'falling' | 'stable' | 'unknown'
  confidence: Confidence
  sourcePaths: string[]
}

export interface VisualCueCurve {
  pointFormat: ['frame', 'value']
  points: Array<[number, number]>
  interpolation: 'linear'
  sourceSampleRateHz: number
  originalPointCount: number
  exportedPointCount: number
  simplification: {
    method: string
    tolerance: number
    maximumError: number
    maximumPointCount: number
  }
  normalization: {
    method: string
    lowerPercentile: number
    upperPercentile: number
    normalizationGroup: string
  }
  smoothing: {
    method: string
    attackSeconds: number
    releaseSeconds: number
    sourceSampleRateHz: number
    outputSampleRateHz: number
  }
}

export interface TrackPromptVisualCueSheet {
  schemaVersion: '1.1.0'
  source: {
    analysisSchemaVersion: string
    analysisVersion: string
    jobId: string
    requestedMode: AnalysisMode
    effectiveMode: AnalysisMode
  }
  timeline: {
    durationSeconds: number
    fps: number
    frameStart: number
    frameEnd: number
    framePolicy: 'nearest-half-up-clamped'
  }
  musicalGrid: {
    bpm: { value: number | null; confidence: Confidence }
    secondsPerBeat: number | null
    meter: { value: string | null; confidence: Confidence }
    downbeatsAvailable: false
  }
  beats: VisualCueEvent[]
  onsets: VisualCueEvent[]
  sections: VisualCueSection[]
  transitions: VisualCueTransition[]
  curves: Record<string, VisualCueCurve>
  warnings: string[]
}

export interface PromptRationale {
  phrase: string
  factPaths: string[]
}

export interface PromptFact {
  path: string
  value: unknown
  role: 'observed' | 'user-entered' | 'user-accepted' | 'preference' | 'detected' | 'detected-ambiguous' | 'detected-component-influence'
}

export interface OmittedFact {
  path: string
  reason: string
}

export interface PromptPackage {
  primaryPrompt: string
  compactPrompt: string
  detailedPrompt: string
  exclusions: string[]
  arrangementBlueprint: string[]
  rationale: PromptRationale[]
  factsUsed: PromptFact[]
  factsOmitted: OmittedFact[]
  warnings: string[]
  engineMode: PromptEngineMode
  candidates: LocalPromptCandidate[]
  selectedCandidateId?: string | null
  modelId: string
  seed?: number | null
  generationParameters: PromptGenerationParameters
  validationWarnings: string[]
  deterministicFallbackUsed: boolean
}

export type PromptEngineMode = 'reliable' | 'creative' | 'experimental'
export type GenreInterpretationMode = 'strict_top' | 'blend' | 'detected_layered' | 'user_selected_only' | 'disabled'
export type LyricsInfluenceMode = 'none' | 'prosody_only' | 'abstract_themes' | 'user_written_direction'

export interface PromptGenerationParameters {
  sampling: boolean
  temperature: number
  topP: number
  repetitionPenalty: number
  maximumTokens: number
  timeoutSeconds: number
}

export interface LocalPromptCandidate {
  id: string
  prompt: string
  shortTitle: string
  engineMode: PromptEngineMode
  seed?: number | null
  modelId: string
  generationParameters: PromptGenerationParameters
  factsUsed: PromptFact[]
  creativeDirectionsUsed: string[]
  warnings: string[]
}

export type GenerationIntent =
  | 'preserve_core_character'
  | 'inspired_variation'
  | 'more_original'
  | 'genre_transfer'
  | 'instrumental_reinterpretation'
  | 'change_mood_preserve_groove'
  | 'change_instrumentation_preserve_structure'
  | 'custom'

export type PromptLength = 'compact' | 'balanced' | 'detailed' | 'custom'

export interface PromptPreferences {
  outputLanguage: string
  generationIntent: GenerationIntent
  promptLength: PromptLength
  customMaxCharacters?: number
  includeBpm: boolean
  includeKey: boolean
  instrumental: boolean
  desiredVocalPresentation?: string
  creativity: number
  preserveEnergyArc: boolean
  preserveInstrumentation: boolean
  preserveStructure: boolean
  preserveGroove: boolean
  targetGenre?: string
  targetMood?: string
  targetDuration?: number
  exclusions: string[]
  disabledFeaturePaths: string[]
  userOverrides: Record<string, unknown>
  variationSeed?: number
  promptEngineMode: PromptEngineMode
  genreInterpretationMode: GenreInterpretationMode
  lyricsInfluenceMode: LyricsInfluenceMode
  candidateCount: 1 | 3
  lockSeed: boolean
  lockedFeaturePaths: string[]
  includeDetectedGenre: boolean
  acceptedGenreIds: string[]
  includeLyricalThemes: boolean
  desiredTransformations: string[]
  userWrittenLyricalDirection?: string
}

export interface AdapterCapability {
  id: string
  name: string
  available: boolean
  reason?: string
  diskImpactMb?: number
  license?: string
  torchInstalled?: boolean
  torchVersion?: string
  cudaBuildSupport?: boolean
  cudaRuntimeAvailable?: boolean
  gpuDeviceName?: string
  selectedDevice?: string
  fallbackReason?: string
  installed?: boolean
  modelReady?: boolean
  enabled?: boolean
  modelId?: string
  modelRevision?: string
  effectiveDevice?: string
  taxonomyVersion?: string
  languagesSupported?: string
  privacyBehavior?: string
  features?: string[]
  serviceReachable?: boolean
  reliableAvailable?: boolean
  creativeAvailable?: boolean
  experimentalAvailable?: boolean
  supportsSeed?: boolean
  supportsSampling?: boolean
  fallbackBehavior?: string
}

export interface Capabilities {
  fastMode: { available: boolean; features: string[] }
  deepMode: {
    available: boolean
    willFallback: boolean
    adapters: AdapterCapability[]
  }
  optionalAnalyzers: Array<{
    id: string
    name: string
    features: string[]
    available: boolean
    reason: string
    license?: string
  }>
  genreTagger?: AdapterCapability | null
  lyricsAdapter?: AdapterCapability | null
  promptWriter?: AdapterCapability | null
  gpuTaskQueue?: { workers: number; active: number; waiting: number; policy: string } | null
  ffmpeg: { available: boolean; version?: string }
  ffprobe: { available: boolean; version?: string }
  limits: {
    maxUploadMb: number
    maxDurationSeconds: number
    maxPendingJobs: number
    maxSingleTrackAnalysisSeconds: number
    maxLongformDurationSeconds: number
    maxSourceUploadBytes: number
    uploadChunkBytes: number
    maxActiveUploads: number
    maxActiveAnalyses: number
    maxActiveGpuTasks: number
    longformScanTimeoutSeconds: number
    minimumFreeDiskBytes: number
  }
  catalogue?: {
    available: boolean
    catalogueSchemaVersion: string
    auditSchemaVersion: string
    segmentSchemaVersion: string
    reportSchemaVersion: string
    autoSegmentationAvailable: boolean
    supportedRetentionPolicies: RetentionPolicy[]
    freeStorageBytes: number
    archiveStorageUsedBytes: number
    archiveQuotaBytes: number
  } | null
  visualCueExportAvailable: boolean
  visualCueSheetSchemaVersion: string
  visualFeatureArtifactSchemaVersion: string
  blenderVisualizerPreset: string
  blenderVisualizerDefaultPreset: BlenderVisualizerPreset
  blenderVisualizerPresets: BlenderVisualizerPreset[]
  blenderVisualizerConfigSchemaVersion: string
  networkFeaturesEnabled: boolean
  retentionPolicy: 'explicit-delete-only'
  automaticAnalysisDeletionEnabled: false
}

export const RENDERER_AVAILABILITY_STATES = [
  'READY',
  'READY_FOR_PREVIEW',
  'READY_FOR_CAPTURE',
  'MISSING_RAINMETER',
  'MISSING_ASSETS',
  'MISSING_FFMPEG',
  'MISSING_CAPTURE_PROVIDER',
  'MISSING_MASTER',
  'INVALID_MASTER_DURATION',
  'INVALID_WORKSPACE',
  'UNSUPPORTED_PLATFORM',
  'INVALID_VENDOR_SNAPSHOT',
  'INVALID_CONTRACT',
  'INVALID_DESIGN_PRESET',
  'ASSET_DURATION_MISMATCH',
  'WORKSPACE_UNAVAILABLE',
] as const

export type RendererAvailabilityState = typeof RENDERER_AVAILABILITY_STATES[number]

export interface RendererRequirement {
  id: string
  label: string
  available: boolean
  requiredForPreparation: boolean
  detail: string
}

export interface RendererContractSummary {
  artist: string
  title: string
  bpm: number
  meter: string
  totalBars: number
  gridDurationSeconds: number
  masterDurationSeconds: number | null
  tailDurationSeconds: number | null
  width: number
  height: number
  fps: number
}

export type SpectrumPreviewSection = 'intro' | 'main' | 'outro'

export const SPECTRUM_BACKGROUND_MODES = [
  'generative-geometry',
  'static-structured',
] as const

export type SpectrumBackgroundMode = typeof SPECTRUM_BACKGROUND_MODES[number]

export const WZHK_GENERATIVE_SHAPE_IDS = [
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
] as const

export type WzhkGenerativeShapeId = typeof WZHK_GENERATIVE_SHAPE_IDS[number]
export type SpectrumGeometryPreviewSection = SpectrumPreviewSection | 'post-grid-tail'
export type SpectrumGeometryPreviewMode = 'shape' | 'morph' | 'section' | 'lab'
export type SpectrumPreviewAudioMode = 'disabled' | 'simulated'

export interface SpectrumGeometryShapeSpec {
  shapeId: WzhkGenerativeShapeId
  seed: number
}

interface SpectrumGenerativePreviewOverrideBase {
  pointCount?: number
  rotationDegrees?: number
  scale?: number
  seed?: number
  audioMode: SpectrumPreviewAudioMode
}

export type SpectrumGenerativePreviewOverride = SpectrumGenerativePreviewOverrideBase & (
  | {
      mode: 'shape'
      shapeA: SpectrumGeometryShapeSpec
    }
  | {
      mode: 'morph'
      shapeA: SpectrumGeometryShapeSpec
      shapeB: SpectrumGeometryShapeSpec
      morphProgress: number
    }
  | {
      mode: 'section'
      section: SpectrumGeometryPreviewSection
    }
  | {
      mode: 'lab'
      shapeA: SpectrumGeometryShapeSpec
      shapeB?: SpectrumGeometryShapeSpec
      morphProgress?: number
    }
)

export interface SpectrumGenerativeGeometrySummary {
  enabled: boolean
  subsystemId: 'wzhk-generative-geometry'
  renderMode: 'neopixel-points'
  seed: number
  pointCount: number
  performanceProfile: 'preview' | 'production' | 'high'
  shapeFamilies: WzhkGenerativeShapeId[]
}

export const SPECTRUM_GEOMETRY_CAPABILITY_STATES = [
  'READY',
  'WEBGL2_UNAVAILABLE',
  'GPU_RENDERER_UNAVAILABLE',
  'SHADER_COMPILE_FAILED',
  'PERFORMANCE_INSUFFICIENT',
  'BROWSER_UNAVAILABLE',
] as const

export type SpectrumGeometryCapabilityState = typeof SPECTRUM_GEOMETRY_CAPABILITY_STATES[number]

export interface SpectrumGeometryCapability {
  state: SpectrumGeometryCapabilityState
  webgl2: boolean | null
  gpuRenderer: string | null
  shaderCompiled: boolean | null
  performanceMeasured: boolean
  performanceSufficient: boolean | null
  rendererFps: number | null
  averageFrameTimeMs: number | null
  pointCount: number | null
  detail: string | null
}

export interface SpectrumGeometryTelemetry {
  actualFps: number | null
  averageFrameTimeMs: number | null
  pointCount: number | null
  droppedRendererFrames: number | null
  gpuRenderer: string | null
}

export interface SpectrumTimelineSectionSummary {
  id: SpectrumPreviewSection | 'post-grid-tail'
  label: string
  startSeconds: number
  endSeconds: number | null
  spectrumColor: string
}

export interface SpectrumDesignPresetSummary {
  presetId: 'scattered'
  displayName: string
  previewTimingSource: 'external-media-player-position'
  productionTimingSource: 'trackprompt-production-clock'
  previewTimingAccuracy: 'preview-level'
  productionTimingAccuracy: 'host-monotonic-process-boundary'
  progressVisible: boolean
  backgroundMode: SpectrumBackgroundMode
  generativeGeometry: SpectrumGenerativeGeometrySummary | null
  sections: SpectrumTimelineSectionSummary[]
}

export interface SpectrumVisualOverrides {
  spectrumScale?: number
  sensitivity?: number
  logoScale?: number
  accentColor?: string
  backgroundIntensity?: number
}

interface SpectrumWorkspacePrepareOptionsBase {
  backgroundMode: SpectrumBackgroundMode
  visualOverrides: SpectrumVisualOverrides
}

export type SpectrumWorkspacePrepareOptions = SpectrumWorkspacePrepareOptionsBase & (
  | {
      mode: 'preview'
      previewSection: SpectrumPreviewSection | null
      generativePreview?: SpectrumGenerativePreviewOverride
    }
  | {
      mode: 'production'
      previewSection: null
      generativePreview?: never
    }
)

export interface RendererDescriptor {
  rendererId: string
  displayName: string
  description: string
  platform: 'cross-platform' | 'windows'
  capabilities: string[]
  availability: RendererAvailabilityState
  available: boolean
  preparationAvailable: boolean
  previewAvailability: SpectrumProductionAvailability | null
  captureAvailability: SpectrumProductionAvailability | null
  previewAvailable: boolean
  captureAvailable: boolean
  warnings: string[]
  requirements: RendererRequirement[]
  contractSummary: RendererContractSummary | null
  designPreset: SpectrumDesignPresetSummary | null
  geometryCapability?: SpectrumGeometryCapability | null
}

export interface SpectrumWorkspaceJob {
  schemaVersion: '1.0.0' | '2.0.0' | '3.0.0' | '4.0.0'
  jobId: string
  rendererId: 'wzhk-spectrum'
  state: SpectrumProductionState | 'PREPARED'
  workspaceRelativePath: string
  contractValid: boolean
  brandingApplied: boolean
  vendorUnchanged: boolean
  generatedWorkspaceHash: string
  vendorSourceHash: string
  vendorCommit: string
  logoResolved: boolean
  masterAudioResolved: boolean
  warnings: string[]
  contractSummary: RendererContractSummary
  mode: 'preview' | 'production'
  backgroundMode: SpectrumBackgroundMode
  presetId: 'scattered' | null
  presetName: string | null
  compositionRevision?: 'scattered-geometry-first-3.7' | null
  previewSection: SpectrumPreviewSection | null
  generativePreviewOverride: SpectrumGenerativePreviewOverride | null
  designHash: string | null
  timingSource: 'external-media-player-position' | 'trackprompt-production-clock' | null
  timingAccuracy: 'preview-level' | 'host-monotonic-process-boundary' | null
  timelineControllerVersion: string | null
  visualQaRequired: boolean
  masterTiming: SpectrumMasterTiming | null
  productionAvailability: SpectrumProductionAvailability | null
  capturePreflight: SpectrumCapturePreflight | null
  artifacts: SpectrumArtifact[]
  synchronization: SpectrumCaptureSynchronization | null
  validationReport: SpectrumValidationReport | null
  captureProvider: string | null
  encoder: string | null
  capturedFrames: number | null
  droppedFrames: number | null
  captureDurationSeconds: number | null
  errorMessage: string | null
  geometryCapability?: SpectrumGeometryCapability | null
  geometryTelemetry?: SpectrumGeometryTelemetry | null
}

export type SpectrumProductionAvailability =
  | 'READY_FOR_PREVIEW'
  | 'READY_FOR_CAPTURE'
  | 'MISSING_RAINMETER'
  | 'MISSING_FFMPEG'
  | 'MISSING_CAPTURE_PROVIDER'
  | 'INVALID_WORKSPACE'
  | 'MISSING_MASTER'
  | 'INVALID_MASTER_DURATION'

export type SpectrumProductionState =
  | 'WORKSPACE_READY'
  | 'PREVIEW_READY'
  | 'CAPTURE_PREFLIGHT'
  | 'CAPTURE_READY'
  | 'CAPTURING'
  | 'CAPTURE_COMPLETE'
  | 'MUXING'
  | 'VALIDATING'
  | 'COMPLETE'
  | 'FAILED'
  | 'CANCELLED'

export interface SpectrumMasterTiming {
  gridDurationSeconds: number
  masterDurationSeconds: number
  tailDurationSeconds: number
  configuredFinalFadeSeconds: number
  finalFadeStartSeconds: number
}

export interface SpectrumCaptureProvider {
  providerId: 'ffmpeg-gfxcapture'
  displayName: string
  available: boolean
  supportsWindowCapture: boolean
  supportsConstantFrameRate: boolean
  crashResilientContainer: 'matroska'
  encoder: 'h264_nvenc' | 'libx264' | null
  hardwareAccelerationVerified: boolean
  detail: string
}

export interface SpectrumCapturePreflight {
  availability: SpectrumProductionAvailability
  ready: boolean
  provider: SpectrumCaptureProvider
  timing: SpectrumMasterTiming | null
  rainmeterPathResolved: boolean
  ffmpegPathResolved: boolean
  ffprobePathResolved: boolean
  playbackPathResolved: boolean
  workspaceValid: boolean
  masterValid: boolean
  operatorNotice: string
  warnings: string[]
}

export interface SpectrumArtifact {
  artifactType: string
  relativePath: string
  sha256: string
  sizeBytes: number
  createdState: SpectrumProductionState
  provenance: string
  timestampSeconds: number | null
}

export interface SpectrumCaptureSynchronization {
  method: 'owned-playback-process-ffmpeg-progress-clock'
  measuredStartOffsetSeconds: number
  measuredEndOffsetSeconds: number
  correctionAppliedSeconds: number
  precision: 'host-monotonic-process-boundary'
}

export interface SpectrumValidationReport {
  valid: boolean
  checks: Array<{ id: string; passed: boolean; measured: string; expected: string }>
}

export interface AnalysisOptions {
  enableGenreAnalysis: boolean
  enableLyricsAnalysis: boolean
  lyricsConsentConfirmed: boolean
  deriveLyricalThemes: boolean
  allowFeatureFallback: boolean
}

export interface AnalysisCatalogueItem {
  analysisId: string
  displayName: string
  status: string
  retentionPolicy: 'persistent'
  createdAt: string
  updatedAt: string
  archivedAt: string | null
  durationSeconds: number | null
  analysisSchemaVersion: string | null
  archiveHealth: string
  retainedAudioAvailable: boolean
  analysisAvailable: boolean
  storyPlanAvailable: boolean
  shotPlanAvailable: boolean
  dependentVideoJobCount: number
  explicitDeleteEligible: boolean
  legacyMissing: boolean
  deletedAt: string | null
}

export interface AnalysisCataloguePage {
  items: AnalysisCatalogueItem[]
  total: number
  offset: number
  limit: number
}

export type RetentionPolicy = 'temporary' | 'archive' | 'custom'
export type BatchState = 'draft' | 'uploading' | 'awaiting_review' | 'queued' | 'running' | 'paused' | 'completed' | 'cancelled'
export type UploadState = 'created' | 'uploading' | 'paused' | 'verifying' | 'completed' | 'cancelled' | 'failed'
export type TransitionType = 'silence_gap' | 'hard_cut' | 'fade' | 'crossfade' | 'gradual_transition' | 'uncertain'
export type ReviewState = 'detected' | 'accepted' | 'rejected' | 'user_edited' | 'imported' | 'unresolved'
export type QueueState = 'stored' | 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled'

export interface CataloguePage<T> {
  items: T[]
  total: number
  offset: number
  limit: number
}

export interface CatalogueClient {
  id: string
  displayName: string
  privateNotes: string
  tags: string[]
  archived: boolean
  projectCount: number
  createdAt: string
  updatedAt: string
}

export interface CatalogueProject {
  id: string
  clientId: string
  name: string
  description: string
  status: string
  retentionPolicy: RetentionPolicy
  retentionUntil?: string | null
  tags: string[]
  archivedAt?: string | null
  storageBytes: number
  batchCount: number
  createdAt: string
  updatedAt: string
}

export interface CatalogueBatch {
  id: string
  projectId: string
  name: string
  sequence: number
  defaultAnalysisMode: AnalysisMode
  enableGenreAnalysis: boolean
  enableLyricalAnalysis: boolean
  lyricsConsentConfirmed: boolean
  state: BatchState
  itemTotal: number
  completedItems: number
  failedItems: number
  durationSeconds: number
  progress: number
  createdAt: string
  completedAt?: string | null
}

export interface UploadSession {
  id: string
  batchId: string
  displayName: string
  totalBytes: number
  receivedBytes: number
  chunkSizeBytes: number
  expectedSha256?: string | null
  state: UploadState
  assetId?: string | null
  duplicateAssetId?: string | null
  createdAt: string
  updatedAt: string
  expiresAt: string
}

export interface SourceAsset {
  id: string
  projectId: string
  batchId: string
  displayName: string
  contentSha256: string
  byteSize: number
  durationSeconds: number
  codec: string
  container: string
  sampleRate: number
  channels: number
  originalOrder: number
  uploadState: UploadState
  storageState: string
  archivalState: RetentionPolicy
  segmentationState: string
  createdAt: string
}

export interface VirtualSegment {
  id: string
  sourceAssetId: string
  sequenceIndex: number
  label: string
  startSeconds: number
  endSeconds: number
  stableCoreStartSeconds: number
  stableCoreEndSeconds: number
  transitionInStartSeconds?: number | null
  transitionInEndSeconds?: number | null
  transitionOutStartSeconds?: number | null
  transitionOutEndSeconds?: number | null
  confidence: Confidence
  confidenceScore?: number | null
  transitionType: TransitionType
  reviewState: ReviewState
  accepted: boolean
  childAnalysisJobId?: string | null
  evidence: Record<string, number>
  revision: number
}

export interface SegmentationJob {
  id: string
  assetId: string
  state: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  stage: string
  progress: number
  observationCount: number
  candidateCount: number
  refinedBoundaryCount: number
  peakBufferBytes: number
  elapsedSeconds: number
  errorCode?: string | null
  createdAt: string
  updatedAt: string
}

export type SegmentEditOperation = 'add' | 'move' | 'delete' | 'merge' | 'split' | 'rename' | 'accept' | 'reject' | 'restore'

export interface SegmentEdit {
  operation: SegmentEditOperation
  segmentId?: string
  adjacentSegmentId?: string
  atSeconds?: number
  startSeconds?: number
  endSeconds?: number
  label?: string
  reason: string
}

export interface QueueItem {
  id: string
  batchId: string
  segmentId: string
  state: QueueState
  attempt: number
  analysisMode: AnalysisMode
  jobId?: string | null
  failureReason?: string | null
  createdAt: string
  updatedAt: string
}

export interface AuditEvent {
  eventId: string
  timestamp: string
  sequence: number
  projectId: string
  batchId?: string | null
  entityType: string
  entityId: string
  eventType: string
  actorType: string
  correlationId: string
  schemaVersion: string
  payload: Record<string, unknown>
  previousEventHash: string
  eventHash: string
}

export interface GenreCandidate {
  id: string
  label: string
  canonicalLabel: string
  parent?: string | null
  similarity: number
  confidence: Confidence
  accepted: boolean
  rejected: boolean
  locked: boolean
  userEdited: boolean
  custom: boolean
}

export interface GenreWindowEvidence {
  id: string
  kind: string
  startSeconds: number
  endSeconds: number
  topLabels: string[]
  similarities: Record<string, number>
  weight: number
  representativeness: number
  vocalDominant: boolean
  percussionDominant: boolean
  sectionIds: string[]
  analysisView: string
}

export interface GenreLayerEvidence {
  value: string | string[]
  confidence: Confidence
  method: string
  supportingWindowIds: string[]
  supportingSectionIds: string[]
  alternatives: string[]
  ambiguity?: string | null
  source: 'detected' | 'user_entered'
  accepted: boolean
  enabledForPrompt: boolean
}

export interface GenreAnalysis {
  broadCandidates: GenreCandidate[]
  subgenreCandidates: GenreCandidate[]
  blendCandidates: string[]
  descriptiveTags: GenreCandidate[]
  windowEvidence: GenreWindowEvidence[]
  sectionEvidence: Record<string, string[]>
  primaryProductionGenre?: GenreLayerEvidence | null
  secondaryProductionGenres?: GenreLayerEvidence | null
  vocalDeliveryStyle?: GenreLayerEvidence | null
  vocalGenreInfluences?: GenreLayerEvidence | null
  sectionGenreEvidence: GenreLayerEvidence[]
  overallGenreBlend?: GenreLayerEvidence | null
  confidence: Confidence
  ambiguity?: string | null
  method: string
  modelId: string
  taxonomyVersion: string
  selectedDevice: string
  agreementAcrossWindows?: number | null
  warnings: string[]
  userEdited: boolean
  userAccepted: boolean
  disabledForPrompt: boolean
}

export interface LyricsAnalysisSummary {
  enabled: boolean
  status: string
  adapterId?: string | null
  modelId?: string | null
  selectedDevice: string
  language?: string | null
  languageConfidence: Confidence
  transcriptAvailable: boolean
  segmentCount: number
  activeSectionIds: string[]
  vocalWordDensity?: string | null
  nonLexicalVocalizationTendency?: string | null
  abstractThemes: string[]
  themeConfidence: Confidence
  themesUserApproved: boolean
  warnings: string[]
  createdAt?: string | null
}

export type LyricsSegmentQualityDecision =
  | 'accepted'
  | 'uncertain'
  | 'rejected_as_likely_hallucination'
  | 'non_lexical'

export interface LyricsSegment {
  id: string
  startSeconds: number
  endSeconds: number
  text: string
  confidence: Confidence
  qualityDecision: LyricsSegmentQualityDecision
  avgLogProbability?: number | null
  noSpeechScore?: number | null
  compressionRatio?: number | null
  repeatedTokenRatio?: number | null
  activeSectionIds: string[]
  qualityFlags: string[]
  userEdited: boolean
}

export interface PrivateLyricsTranscript {
  schemaVersion: string
  jobId: string
  language?: string | null
  segments: LyricsSegment[]
  modelId: string
  selectedDevice: string
  warnings: string[]
  userEdited: boolean
  createdAt: string
}

export interface GenreCandidateUpdate {
  candidateId: string
  label?: string
  accepted?: boolean
  rejected?: boolean
  locked?: boolean
  restoreDetected?: boolean
}

export interface GenrePatch {
  updates?: GenreCandidateUpdate[]
  customGenre?: string
  disabledForPrompt?: boolean
  restoreAll?: boolean
}

export interface LyricsSegmentUpdate {
  segmentId: string
  text?: string
  markUncertain?: boolean
  delete?: boolean
  restoreDetected?: boolean
}

export interface LyricsPatch {
  updates?: LyricsSegmentUpdate[]
  abstractThemes?: string[]
}

export interface SafeApiError {
  code: string
  message: string
  details?: unknown
}

export interface AnalysisJob {
  jobId: string
  status: JobStatus
  requestedMode: AnalysisMode
  mode: AnalysisMode
  stage: AnalysisStage
  message: string
  createdAt: string
  updatedAt: string
  progress?: number
  analysis?: AnalysisResult
  promptPackage?: PromptPackage
  error?: SafeApiError
}

export interface AnalysisEvent {
  jobId: string
  status: JobStatus
  mode?: AnalysisMode
  stage: AnalysisStage
  message: string
  sequence: number
  timestamp: string
  progress?: number
}

export interface FactUpdate {
  path: string
  value?: unknown
  disabledForPrompt?: boolean
  acceptedForPrompt?: boolean
  restoreDetected?: boolean
}

export interface FeatureEntry {
  path: string
  label: string
  feature: FeatureValue
}

const confidenceValues = new Set<Confidence>(['low', 'medium', 'high', 'unknown'])

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isFeatureValue(value: unknown): value is FeatureValue {
  if (!isRecord(value)) return false
  return (
    'value' in value &&
    typeof value.method === 'string' &&
    typeof value.confidence === 'string' &&
    confidenceValues.has(value.confidence as Confidence) &&
    typeof value.userEdited === 'boolean' &&
    (value.userAccepted === undefined || typeof value.userAccepted === 'boolean')
  )
}

export function humanizeKey(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/^./, (first) => first.toUpperCase())
}

export function formatFeatureValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not detected'
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (Array.isArray(value)) return value.map(formatFeatureValue).join(', ')
  if (isRecord(value)) {
    return Object.entries(value)
      .map(([key, item]) => `${humanizeKey(key)}: ${formatFeatureValue(item)}`)
      .join(' · ')
  }
  if (typeof value === 'string') return value
  if (typeof value === 'bigint') return value.toString()
  if (typeof value === 'symbol') return value.description ?? 'Unknown symbol'
  return 'Unsupported value'
}

export function collectFeatureEntries(group: AnalysisGroup, prefix: string): FeatureEntry[] {
  const entries: FeatureEntry[] = []
  const visit = (value: unknown, path: string, label: string): void => {
    if (isFeatureValue(value)) {
      entries.push({ path, label, feature: value })
      return
    }
    if (isRecord(value)) {
      Object.entries(value).forEach(([key, child]) => {
        visit(child, `${path}.${key}`, humanizeKey(key))
      })
    }
  }
  Object.entries(group).forEach(([key, value]) => visit(value, `${prefix}.${key}`, humanizeKey(key)))
  return entries
}

export function getFeatureAtPath(analysis: AnalysisResult, path: string): FeatureValue | undefined {
  let current: unknown = analysis
  for (const segment of path.split('.')) {
    if (!isRecord(current)) return undefined
    current = current[segment]
  }
  return isFeatureValue(current) ? current : undefined
}

export const DEFAULT_CAPABILITIES: Capabilities = {
  fastMode: { available: true, features: ['Core signal, rhythm, harmony, structure, and mix analysis'] },
  deepMode: { available: false, willFallback: true, adapters: [] },
  optionalAnalyzers: [],
  genreTagger: null,
  lyricsAdapter: null,
  promptWriter: null,
  gpuTaskQueue: { workers: 1, active: 0, waiting: 0, policy: 'single-heavy-task' },
  ffmpeg: { available: false },
  ffprobe: { available: false },
  limits: {
    maxUploadMb: 200,
    maxDurationSeconds: 1200,
    maxPendingJobs: 2,
    maxSingleTrackAnalysisSeconds: 1200,
    maxLongformDurationSeconds: 43200,
    maxSourceUploadBytes: 50 * 1024 * 1024 * 1024,
    uploadChunkBytes: 32 * 1024 * 1024,
    maxActiveUploads: 3,
    maxActiveAnalyses: 1,
    maxActiveGpuTasks: 1,
    longformScanTimeoutSeconds: 7200,
    minimumFreeDiskBytes: 10 * 1024 * 1024 * 1024,
  },
  catalogue: null,
  visualCueExportAvailable: false,
  visualCueSheetSchemaVersion: 'unknown',
  visualFeatureArtifactSchemaVersion: 'unknown',
  blenderVisualizerPreset: '',
  blenderVisualizerDefaultPreset: 'abstract-geometry',
  blenderVisualizerPresets: ['abstract-geometry'],
  blenderVisualizerConfigSchemaVersion: 'unknown',
  networkFeaturesEnabled: false,
  retentionPolicy: 'explicit-delete-only',
  automaticAnalysisDeletionEnabled: false,
}
