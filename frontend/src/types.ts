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

export interface PromptRationale {
  phrase: string
  factPaths: string[]
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
  factsUsed: string[]
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
export type GenreInterpretationMode = 'strict_top' | 'blend' | 'user_selected_only' | 'disabled'
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
  factsUsed: string[]
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
    jobTtlMinutes: number
    maxPendingJobs: number
  }
  networkFeaturesEnabled: boolean
}

export interface AnalysisOptions {
  enableGenreAnalysis: boolean
  enableLyricsAnalysis: boolean
  lyricsConsentConfirmed: boolean
  deriveLyricalThemes: boolean
  allowFeatureFallback: boolean
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
}

export interface GenreAnalysis {
  broadCandidates: GenreCandidate[]
  subgenreCandidates: GenreCandidate[]
  blendCandidates: string[]
  descriptiveTags: GenreCandidate[]
  windowEvidence: GenreWindowEvidence[]
  sectionEvidence: Record<string, string[]>
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
  warnings: string[]
  createdAt?: string | null
}

export interface LyricsSegment {
  id: string
  startSeconds: number
  endSeconds: number
  text: string
  confidence: Confidence
  noSpeechScore?: number | null
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
  limits: { maxUploadMb: 200, maxDurationSeconds: 1200, jobTtlMinutes: 60, maxPendingJobs: 2 },
  networkFeaturesEnabled: false,
}
