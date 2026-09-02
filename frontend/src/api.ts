import {
  DEFAULT_CAPABILITIES,
  type AnalysisEvent,
  type AnalysisGroup,
  type AnalysisJob,
  type AnalysisCatalogueItem,
  type AnalysisCataloguePage,
  type AnalysisMode,
  type AnalysisOptions,
  type AnalysisResult,
  type AnalysisSection,
  type AnalysisStage,
  type BlenderVisualizerPreset,
  type Capabilities,
  type CatalogueBatch,
  type CatalogueClient,
  type CataloguePage,
  type CatalogueProject,
  type AuditEvent,
  type Confidence,
  type FactUpdate,
  type GenreAnalysis,
  type GenreCandidate,
  type GenreLayerEvidence,
  type GenrePatch,
  type JobStatus,
  type PromptPackage,
  type PromptFact,
  type PromptPreferences,
  type PrivateLyricsTranscript,
  type QueueItem,
  RENDERER_AVAILABILITY_STATES,
  SPECTRUM_BACKGROUND_MODES,
  SPECTRUM_GEOMETRY_CAPABILITY_STATES,
  WZHK_GENERATIVE_SHAPE_IDS,
  type RendererAvailabilityState,
  type RendererContractSummary,
  type RendererDescriptor,
  type ResolvedVisualizerConfig,
  type RetentionPolicy,
  type SourceAsset,
  type SpaceJourneyParameters,
  type SpectrumWorkspaceJob,
  type SpectrumDesignPresetSummary,
  type SpectrumPreviewSection,
  type SpectrumWorkspacePrepareOptions,
  type SpectrumArtifact,
  type SpectrumCapturePreflight,
  type SpectrumCaptureProvider,
  type SpectrumCaptureSynchronization,
  type SpectrumBackgroundMode,
  type SpectrumGenerativeGeometrySummary,
  type SpectrumGenerativePreviewOverride,
  type SpectrumGeometryCapability,
  type SpectrumGeometryShapeSpec,
  type SpectrumGeometryTelemetry,
  type SpectrumMasterTiming,
  type SpectrumProductionAvailability,
  type SpectrumProductionState,
  type SpectrumValidationReport,
  type WzhkGenerativeShapeId,
  type LyricsPatch,
  type LyricsSegmentQualityDecision,
  type SegmentEdit,
  type SegmentationJob,
  type TrackPromptVisualCueSheet,
  type UploadSession,
  type VirtualSegment,
  type VisualCueCurve,
  type VisualCueEvent,
  type VisualCuePreferences,
  type VisualCueTransition,
  type VisualizerConfigRequest,
  isBlenderVisualizerPreset,
  isRecord,
  isSpaceJourneyPalette,
} from './types'
import { validateSpaceJourneyParameters } from './visualizer'

const API_BASE = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? '/api'

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details?: unknown

  constructor(message: string, code = 'request_failed', status = 0, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}

function requiredString(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  if (typeof value !== 'string') throw new ApiError(`The server returned an invalid ${key}.`, 'invalid_response')
  return value
}

function optionalString(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key]
  return typeof value === 'string' ? value : undefined
}

function numberOr(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function booleanOr(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function requiredStringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new ApiError(`The server returned invalid ${label}.`, 'invalid_response')
  }
  return value.filter((item): item is string => typeof item === 'string')
}

function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function asGroup(value: unknown): AnalysisGroup {
  return isRecord(value) ? value : {}
}

function parseSection(value: unknown, index: number): AnalysisSection | null {
  if (!isRecord(value)) return null
  const startSeconds = numberOr(value.startSeconds, Number.NaN)
  const endSeconds = numberOr(value.endSeconds, Number.NaN)
  if (!Number.isFinite(startSeconds) || !Number.isFinite(endSeconds) || endSeconds <= startSeconds) return null
  const confidence = ['low', 'medium', 'high', 'unknown'].includes(String(value.confidence))
    ? (value.confidence as Confidence)
    : 'unknown'
  return {
    ...value,
    id: typeof value.id === 'string' ? value.id : `section-${index + 1}`,
    neutralLabel: typeof value.neutralLabel === 'string' ? value.neutralLabel : `Section ${index + 1}`,
    inferredLabel: typeof value.inferredLabel === 'string' ? value.inferredLabel : null,
    startSeconds,
    endSeconds,
    confidence,
    instruments: stringArray(value.instruments),
  }
}

function parseGenreCandidate(value: unknown): GenreCandidate | null {
  if (!isRecord(value) || typeof value.id !== 'string' || typeof value.label !== 'string') return null
  return {
    id: value.id,
    label: value.label,
    canonicalLabel: optionalString(value, 'canonicalLabel') ?? value.label,
    parent: typeof value.parent === 'string' ? value.parent : null,
    similarity: numberOr(value.similarity, 0),
    confidence: ['low', 'medium', 'high', 'unknown'].includes(String(value.confidence))
      ? value.confidence as Confidence
      : 'unknown',
    accepted: booleanOr(value.accepted, false),
    rejected: booleanOr(value.rejected, false),
    locked: booleanOr(value.locked, false),
    userEdited: booleanOr(value.userEdited, false),
    custom: booleanOr(value.custom, false),
  }
}

function parseGenreLayer(value: unknown): GenreLayerEvidence | null {
  if (!isRecord(value) || typeof value.method !== 'string') return null
  const layerValue = typeof value.value === 'string'
    ? value.value
    : Array.isArray(value.value)
      ? stringArray(value.value)
      : null
  if (layerValue === null) return null
  return {
    value: layerValue,
    confidence: ['low', 'medium', 'high', 'unknown'].includes(String(value.confidence)) ? value.confidence as Confidence : 'unknown',
    method: value.method,
    supportingWindowIds: stringArray(value.supportingWindowIds),
    supportingSectionIds: stringArray(value.supportingSectionIds),
    alternatives: stringArray(value.alternatives),
    ambiguity: typeof value.ambiguity === 'string' ? value.ambiguity : null,
    source: value.source === 'user_entered' ? 'user_entered' : 'detected',
    accepted: booleanOr(value.accepted, false),
    enabledForPrompt: booleanOr(value.enabledForPrompt, true),
  }
}

function parseGenreAnalysis(value: unknown): GenreAnalysis | null {
  if (!isRecord(value) || typeof value.method !== 'string' || typeof value.modelId !== 'string') return null
  const candidates = (raw: unknown): GenreCandidate[] => Array.isArray(raw)
    ? raw.map(parseGenreCandidate).filter((item): item is GenreCandidate => item !== null)
    : []
  const windows = Array.isArray(value.windowEvidence) ? value.windowEvidence.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== 'string') return []
    const similarities = isRecord(item.similarities)
      ? Object.fromEntries(Object.entries(item.similarities).filter((entry): entry is [string, number] => typeof entry[1] === 'number'))
      : {}
    return [{
      id: item.id,
      kind: optionalString(item, 'kind') ?? 'window',
      startSeconds: numberOr(item.startSeconds, 0),
      endSeconds: numberOr(item.endSeconds, 0),
      topLabels: stringArray(item.topLabels),
      similarities,
      weight: numberOr(item.weight, 1),
      representativeness: numberOr(item.representativeness, 1),
      vocalDominant: booleanOr(item.vocalDominant, false),
      percussionDominant: booleanOr(item.percussionDominant, false),
      sectionIds: stringArray(item.sectionIds),
      analysisView: optionalString(item, 'analysisView') ?? 'full_mix',
    }]
  }) : []
  return {
    broadCandidates: candidates(value.broadCandidates),
    subgenreCandidates: candidates(value.subgenreCandidates),
    blendCandidates: stringArray(value.blendCandidates),
    descriptiveTags: candidates(value.descriptiveTags),
    windowEvidence: windows,
    sectionEvidence: isRecord(value.sectionEvidence)
      ? Object.fromEntries(Object.entries(value.sectionEvidence).map(([key, item]) => [key, stringArray(item)]))
      : {},
    primaryProductionGenre: parseGenreLayer(value.primaryProductionGenre),
    secondaryProductionGenres: parseGenreLayer(value.secondaryProductionGenres),
    vocalDeliveryStyle: parseGenreLayer(value.vocalDeliveryStyle),
    vocalGenreInfluences: parseGenreLayer(value.vocalGenreInfluences),
    sectionGenreEvidence: Array.isArray(value.sectionGenreEvidence)
      ? value.sectionGenreEvidence.map(parseGenreLayer).filter((item): item is GenreLayerEvidence => item !== null)
      : [],
    overallGenreBlend: parseGenreLayer(value.overallGenreBlend),
    confidence: ['low', 'medium', 'high', 'unknown'].includes(String(value.confidence)) ? value.confidence as Confidence : 'unknown',
    ambiguity: typeof value.ambiguity === 'string' ? value.ambiguity : null,
    method: value.method,
    modelId: value.modelId,
    taxonomyVersion: optionalString(value, 'taxonomyVersion') ?? 'unknown',
    selectedDevice: optionalString(value, 'selectedDevice') ?? 'unavailable',
    agreementAcrossWindows: typeof value.agreementAcrossWindows === 'number' ? value.agreementAcrossWindows : null,
    warnings: stringArray(value.warnings),
    userEdited: booleanOr(value.userEdited, false),
    userAccepted: booleanOr(value.userAccepted, false),
    disabledForPrompt: booleanOr(value.disabledForPrompt, false),
  }
}

function parsePromptFact(value: unknown): PromptFact | null {
  if (typeof value === 'string') return { path: value, value: null, role: 'observed' }
  if (!isRecord(value) || typeof value.path !== 'string') return null
  const roles = ['observed', 'user-entered', 'user-accepted', 'preference', 'detected', 'detected-ambiguous', 'detected-component-influence'] as const
  const role = roles.includes(value.role as typeof roles[number]) ? value.role as typeof roles[number] : 'observed'
  return { path: value.path, value: value.value, role }
}

function promptFacts(value: unknown): PromptFact[] {
  return Array.isArray(value)
    ? value.map(parsePromptFact).filter((item): item is PromptFact => item !== null)
    : []
}

function parseAnalysis(value: unknown): AnalysisResult | undefined {
  if (!isRecord(value)) return undefined
  const structure = asGroup(value.structure)
  const rawSections = Array.isArray(structure.sections) ? structure.sections : []
  const sections = rawSections
    .map((section, index) => parseSection(section, index))
    .filter((section): section is AnalysisSection => section !== null)

  return {
    ...value,
    schemaVersion: optionalString(value, 'schemaVersion') ?? 'unknown',
    analysisVersion: optionalString(value, 'analysisVersion') ?? 'unknown',
    jobId: optionalString(value, 'jobId') ?? '',
    capabilities: value.capabilities ?? [],
    requestedMode: value.requestedMode === 'deep' ? 'deep' : 'fast',
    effectiveMode: value.effectiveMode === 'deep' ? 'deep' : 'fast',
    file: asGroup(value.file),
    waveformPeaks: Array.isArray(value.waveformPeaks)
      ? value.waveformPeaks.filter((peak): peak is number => typeof peak === 'number' && Number.isFinite(peak))
      : [],
    signalQuality: asGroup(value.signalQuality),
    rhythm: asGroup(value.rhythm),
    harmony: asGroup(value.harmony),
    melody: asGroup(value.melody),
    structure: { ...structure, sections },
    timbre: asGroup(value.timbre),
    instrumentation: asGroup(value.instrumentation),
    vocals: asGroup(value.vocals),
    production: asGroup(value.production),
    styleAndMood: asGroup(value.styleAndMood),
    genreAnalysis: parseGenreAnalysis(value.genreAnalysis),
    lyricsSummary: isRecord(value.lyricsSummary) ? {
      enabled: booleanOr(value.lyricsSummary.enabled, false),
      status: optionalString(value.lyricsSummary, 'status') ?? 'unknown',
      adapterId: optionalString(value.lyricsSummary, 'adapterId'),
      modelId: optionalString(value.lyricsSummary, 'modelId'),
      selectedDevice: optionalString(value.lyricsSummary, 'selectedDevice') ?? 'unavailable',
      language: optionalString(value.lyricsSummary, 'language'),
      languageConfidence: ['low', 'medium', 'high', 'unknown'].includes(String(value.lyricsSummary.languageConfidence)) ? value.lyricsSummary.languageConfidence as Confidence : 'unknown',
      transcriptAvailable: booleanOr(value.lyricsSummary.transcriptAvailable, false),
      segmentCount: numberOr(value.lyricsSummary.segmentCount, 0),
      activeSectionIds: stringArray(value.lyricsSummary.activeSectionIds),
      vocalWordDensity: optionalString(value.lyricsSummary, 'vocalWordDensity'),
      nonLexicalVocalizationTendency: optionalString(value.lyricsSummary, 'nonLexicalVocalizationTendency'),
      abstractThemes: stringArray(value.lyricsSummary.abstractThemes),
      themeConfidence: ['low', 'medium', 'high', 'unknown'].includes(String(value.lyricsSummary.themeConfidence)) ? value.lyricsSummary.themeConfidence as Confidence : 'unknown',
      themesUserApproved: booleanOr(value.lyricsSummary.themesUserApproved, false),
      warnings: stringArray(value.lyricsSummary.warnings),
      createdAt: optionalString(value.lyricsSummary, 'createdAt'),
    } : null,
    warnings: stringArray(value.warnings),
    analyzerVersions: isRecord(value.analyzerVersions)
      ? Object.fromEntries(
          Object.entries(value.analyzerVersions).filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
        )
      : {},
    deepDiagnostics: isRecord(value.deepDiagnostics) ? {
      adapterId: typeof value.deepDiagnostics.adapterId === 'string' ? value.deepDiagnostics.adapterId : null,
      method: typeof value.deepDiagnostics.method === 'string' ? value.deepDiagnostics.method : null,
      selectedDevice: optionalString(value.deepDiagnostics, 'selectedDevice') ?? 'cpu',
      torchInstalled: booleanOr(value.deepDiagnostics.torchInstalled, false),
      torchVersion: optionalString(value.deepDiagnostics, 'torchVersion'),
      cudaBuildSupport: booleanOr(value.deepDiagnostics.cudaBuildSupport, false),
      cudaRuntimeAvailable: booleanOr(value.deepDiagnostics.cudaRuntimeAvailable, false),
      gpuDeviceName: optionalString(value.deepDiagnostics, 'gpuDeviceName'),
      fallbackReason: optionalString(value.deepDiagnostics, 'fallbackReason'),
      stemRelativeRms: isRecord(value.deepDiagnostics.stemRelativeRms)
        ? Object.fromEntries(
            Object.entries(value.deepDiagnostics.stemRelativeRms)
              .filter((entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isFinite(entry[1])),
          )
        : {},
    } : null,
    createdAt: optionalString(value, 'createdAt') ?? new Date(0).toISOString(),
    disabledFeaturePaths: stringArray(value.disabledFeaturePaths),
  }
}

function parsePromptPackage(value: unknown): PromptPackage | undefined {
  if (!isRecord(value)) return undefined
  if (
    typeof value.primaryPrompt !== 'string' ||
    typeof value.compactPrompt !== 'string' ||
    typeof value.detailedPrompt !== 'string'
  ) {
    return undefined
  }
  const rationale = Array.isArray(value.rationale)
    ? value.rationale.flatMap((item) => {
        if (!isRecord(item) || typeof item.phrase !== 'string') return []
        return [{ phrase: item.phrase, factPaths: stringArray(item.factPaths) }]
      })
    : []
  const factsOmitted = Array.isArray(value.factsOmitted)
    ? value.factsOmitted.flatMap((item) => {
        if (!isRecord(item) || typeof item.path !== 'string' || typeof item.reason !== 'string') return []
        return [{ path: item.path, reason: item.reason }]
      })
    : []
  const generation = isRecord(value.generationParameters) ? value.generationParameters : {}
  const parameters = {
    sampling: booleanOr(generation.sampling, false),
    temperature: numberOr(generation.temperature, 0),
    topP: numberOr(generation.topP, 1),
    repetitionPenalty: numberOr(generation.repetitionPenalty, 1),
    maximumTokens: numberOr(generation.maximumTokens, 512),
    timeoutSeconds: numberOr(generation.timeoutSeconds, 60),
  }
  const candidates = Array.isArray(value.candidates) ? value.candidates.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== 'string' || typeof item.prompt !== 'string') return []
    const itemParameters = isRecord(item.generationParameters) ? item.generationParameters : generation
    return [{
      id: item.id,
      prompt: item.prompt,
      shortTitle: optionalString(item, 'shortTitle') ?? 'Prompt candidate',
      engineMode: ['reliable', 'creative', 'experimental'].includes(String(item.engineMode)) ? item.engineMode as 'reliable' | 'creative' | 'experimental' : 'reliable',
      seed: typeof item.seed === 'number' ? item.seed : null,
      modelId: optionalString(item, 'modelId') ?? 'unknown',
      generationParameters: {
        sampling: booleanOr(itemParameters.sampling, false),
        temperature: numberOr(itemParameters.temperature, 0),
        topP: numberOr(itemParameters.topP, 1),
        repetitionPenalty: numberOr(itemParameters.repetitionPenalty, 1),
        maximumTokens: numberOr(itemParameters.maximumTokens, 512),
        timeoutSeconds: numberOr(itemParameters.timeoutSeconds, 60),
      },
      factsUsed: promptFacts(item.factsUsed),
      creativeDirectionsUsed: stringArray(item.creativeDirectionsUsed),
      warnings: stringArray(item.warnings),
    }]
  }) : []
  const selectedCandidateId = typeof value.selectedCandidateId === 'string' ? value.selectedCandidateId : null
  const selectedCandidate = candidates.find((candidate) => candidate.id === selectedCandidateId)
  if (
    (candidates.length > 0 && (!selectedCandidate || selectedCandidate.prompt !== value.primaryPrompt)) ||
    (candidates.length === 0 && selectedCandidateId !== null)
  ) {
    return undefined
  }
  return {
    primaryPrompt: value.primaryPrompt,
    compactPrompt: value.compactPrompt,
    detailedPrompt: value.detailedPrompt,
    exclusions: stringArray(value.exclusions),
    arrangementBlueprint: stringArray(value.arrangementBlueprint),
    rationale,
    factsUsed: promptFacts(value.factsUsed),
    factsOmitted,
    warnings: stringArray(value.warnings),
    engineMode: ['reliable', 'creative', 'experimental'].includes(String(value.engineMode)) ? value.engineMode as 'reliable' | 'creative' | 'experimental' : 'reliable',
    candidates,
    selectedCandidateId,
    modelId: optionalString(value, 'modelId') ?? 'trackprompt-deterministic-composer',
    seed: typeof value.seed === 'number' ? value.seed : null,
    generationParameters: parameters,
    validationWarnings: stringArray(value.validationWarnings),
    deterministicFallbackUsed: booleanOr(value.deterministicFallbackUsed, false),
  }
}

function parseApiError(value: unknown): AnalysisJob['error'] {
  if (!isRecord(value) || typeof value.message !== 'string') return undefined
  return {
    code: typeof value.code === 'string' ? value.code : 'analysis_failed',
    message: value.message,
    details: value.details,
  }
}

const statuses = new Set<JobStatus>([
  'queued',
  'validating',
  'decoding',
  'analyzing_core',
  'separating_stems',
  'analyzing_deep',
  'transcribing_lyrics',
  'deriving_lyrical_themes',
  'tagging_genre',
  'generating_prompt',
  'generating_candidates',
  'validating_candidates',
  'completed',
  'cancelled',
  'failed',
  'expired',
])

const stages = new Set<AnalysisStage>([
  'queued',
  'validating',
  'decoding',
  'inspecting_signal',
  'analyzing_rhythm',
  'analyzing_harmony',
  'segmenting_structure',
  'analyzing_production',
  'extracting_visual_features',
  'separating_stems',
  'running_enhanced_taggers',
  'transcribing_lyrics',
  'deriving_lyrical_themes',
  'tagging_genre',
  'generating_candidates',
  'validating_candidates',
  'composing_prompt',
  'finalizing',
  'completed',
  'cancellation_requested',
  'cancelled',
  'failed',
  'expired',
])

function normalizeStage(value: unknown, status: JobStatus): AnalysisStage {
  const raw = typeof value === 'string' ? value : status
  const aliases: Record<string, AnalysisStage> = {
    analyzing_core: 'inspecting_signal',
    analyzing_deep: 'running_enhanced_taggers',
    generating_prompt: 'composing_prompt',
    finalizing_analysis: 'finalizing',
  }
  const normalized = aliases[raw] ?? raw
  if (stages.has(normalized as AnalysisStage)) return normalized as AnalysisStage
  const normalizedStatus = aliases[status] ?? status
  return stages.has(normalizedStatus as AnalysisStage) ? normalizedStatus as AnalysisStage : 'queued'
}

function parseJob(value: unknown): AnalysisJob {
  if (!isRecord(value)) throw new ApiError('The server returned an invalid analysis job.', 'invalid_response')
  const statusValue = requiredString(value, 'status')
  if (!statuses.has(statusValue as JobStatus)) {
    throw new ApiError('The server returned an unknown analysis state.', 'invalid_response')
  }
  const requestedMode = value.requestedMode === 'deep' ? 'deep' : 'fast'
  const mode = value.mode === 'deep' ? 'deep' : 'fast'
  return {
    jobId: requiredString(value, 'jobId'),
    status: statusValue as JobStatus,
    requestedMode,
    mode,
    stage: normalizeStage(value.stage, statusValue as JobStatus),
    message: optionalString(value, 'message') ?? 'Analysis state updated.',
    createdAt: optionalString(value, 'createdAt') ?? new Date().toISOString(),
    updatedAt: optionalString(value, 'updatedAt') ?? new Date().toISOString(),
    progress: typeof value.progress === 'number' ? value.progress : undefined,
    analysis: parseAnalysis(value.analysis),
    promptPackage: parsePromptPackage(value.promptPackage),
    error: parseApiError(value.error),
  }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiError('The local analysis service could not be reached. Make sure the backend is running.', 'offline')
  }
  if (!response.ok) {
    let payload: unknown
    try {
      payload = await response.json()
    } catch {
      payload = undefined
    }
    const nested = isRecord(payload) && isRecord(payload.error) ? payload.error : payload
    const message = isRecord(nested) && typeof nested.message === 'string'
      ? nested.message
      : `The request failed (${response.status}).`
    const code = isRecord(nested) && typeof nested.code === 'string' ? nested.code : 'request_failed'
    throw new ApiError(message, code, response.status, isRecord(nested) ? nested.details : undefined)
  }
  return response
}

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await request(path, init)
  if (response.status === 204) return undefined
  try {
    return await response.json()
  } catch {
    throw new ApiError('The local service returned unreadable data.', 'invalid_response', response.status)
  }
}

export async function getCapabilities(): Promise<Capabilities> {
  const value = await requestJson('/capabilities')
  if (!isRecord(value)) throw new ApiError('The server returned invalid capabilities.', 'invalid_response')
  const fastMode = isRecord(value.fastMode) ? value.fastMode : {}
  const deepMode = isRecord(value.deepMode) ? value.deepMode : {}
  const ffmpeg = isRecord(value.ffmpeg) ? value.ffmpeg : {}
  const ffprobe = isRecord(value.ffprobe) ? value.ffprobe : {}
  const limits = isRecord(value.limits) ? value.limits : {}
  const catalogue = isRecord(value.catalogue) ? value.catalogue : null
  const legacyVisualizerPreset = optionalString(value, 'blenderVisualizerPreset') ?? ''
  const advertisedVisualizerPresets = Array.isArray(value.blenderVisualizerPresets)
    ? value.blenderVisualizerPresets.filter(isBlenderVisualizerPreset)
    : []
  const defaultVisualizerPreset = isBlenderVisualizerPreset(value.blenderVisualizerDefaultPreset)
    ? value.blenderVisualizerDefaultPreset
    : isBlenderVisualizerPreset(legacyVisualizerPreset)
      ? legacyVisualizerPreset
      : 'abstract-geometry'
  const blenderVisualizerPresets = Array.from(new Set<BlenderVisualizerPreset>([
    'abstract-geometry',
    ...advertisedVisualizerPresets,
    defaultVisualizerPreset,
  ]))
  const adapters = Array.isArray(deepMode.adapters)
    ? deepMode.adapters.flatMap((adapter) => {
        if (!isRecord(adapter) || typeof adapter.id !== 'string') return []
        return [{
          id: adapter.id,
          name: typeof adapter.name === 'string' ? adapter.name : adapter.id,
          available: booleanOr(adapter.available, false),
          reason: optionalString(adapter, 'reason'),
          diskImpactMb: typeof adapter.diskImpactMb === 'number' ? adapter.diskImpactMb : undefined,
          license: optionalString(adapter, 'license'),
          torchInstalled: booleanOr(adapter.torchInstalled, false),
          torchVersion: optionalString(adapter, 'torchVersion'),
          cudaBuildSupport: booleanOr(adapter.cudaBuildSupport, false),
          cudaRuntimeAvailable: booleanOr(adapter.cudaRuntimeAvailable, false),
          gpuDeviceName: optionalString(adapter, 'gpuDeviceName'),
          selectedDevice: optionalString(adapter, 'selectedDevice'),
          fallbackReason: optionalString(adapter, 'fallbackReason'),
        }]
      })
    : []
  const optionalAnalyzers = Array.isArray(value.optionalAnalyzers)
    ? value.optionalAnalyzers.flatMap((analyzer) => {
        if (!isRecord(analyzer) || typeof analyzer.id !== 'string' || typeof analyzer.reason !== 'string') return []
        return [{
          id: analyzer.id,
          name: typeof analyzer.name === 'string' ? analyzer.name : analyzer.id,
          features: stringArray(analyzer.features),
          available: booleanOr(analyzer.available, false),
          reason: analyzer.reason,
          license: optionalString(analyzer, 'license'),
        }]
      })
    : []
  const parseAdapter = (raw: unknown): Capabilities['genreTagger'] => {
    if (!isRecord(raw) || typeof raw.id !== 'string') return null
    return {
      id: raw.id,
      name: optionalString(raw, 'name') ?? raw.id,
      available: booleanOr(raw.available, false),
      reason: optionalString(raw, 'reason'),
      diskImpactMb: typeof raw.diskImpactMb === 'number' ? raw.diskImpactMb : undefined,
      license: optionalString(raw, 'license'),
      installed: booleanOr(raw.installed, false),
      modelReady: booleanOr(raw.modelReady, false),
      enabled: booleanOr(raw.enabled, false),
      modelId: optionalString(raw, 'modelId'),
      modelRevision: optionalString(raw, 'modelRevision'),
      selectedDevice: optionalString(raw, 'selectedDevice'),
      effectiveDevice: optionalString(raw, 'effectiveDevice'),
      gpuDeviceName: optionalString(raw, 'gpuDeviceName'),
      taxonomyVersion: optionalString(raw, 'taxonomyVersion'),
      languagesSupported: optionalString(raw, 'languagesSupported'),
      privacyBehavior: optionalString(raw, 'privacyBehavior'),
      fallbackReason: optionalString(raw, 'fallbackReason'),
      features: stringArray(raw.features),
      serviceReachable: booleanOr(raw.serviceReachable, false),
      reliableAvailable: booleanOr(raw.reliableAvailable, true),
      creativeAvailable: booleanOr(raw.creativeAvailable, false),
      experimentalAvailable: booleanOr(raw.experimentalAvailable, false),
      supportsSeed: booleanOr(raw.supportsSeed, false),
      supportsSampling: booleanOr(raw.supportsSampling, false),
      fallbackBehavior: optionalString(raw, 'fallbackBehavior'),
    }
  }
  const queue = isRecord(value.gpuTaskQueue) ? value.gpuTaskQueue : null
  return {
    fastMode: {
      available: booleanOr(fastMode.available, false),
      features: stringArray(fastMode.features).length > 0
        ? stringArray(fastMode.features)
        : DEFAULT_CAPABILITIES.fastMode.features,
    },
    deepMode: {
      available: booleanOr(deepMode.available, false),
      willFallback: booleanOr(deepMode.willFallback, true),
      adapters,
    },
    optionalAnalyzers,
    genreTagger: parseAdapter(value.genreTagger),
    lyricsAdapter: parseAdapter(value.lyricsAdapter),
    promptWriter: parseAdapter(value.promptWriter),
    gpuTaskQueue: queue ? {
      workers: numberOr(queue.workers, 1),
      active: numberOr(queue.active, 0),
      waiting: numberOr(queue.waiting, 0),
      policy: optionalString(queue, 'policy') ?? 'single-heavy-task',
    } : null,
    ffmpeg: { available: booleanOr(ffmpeg.available, false), version: optionalString(ffmpeg, 'version') },
    ffprobe: { available: booleanOr(ffprobe.available, false), version: optionalString(ffprobe, 'version') },
    limits: {
      maxUploadMb: numberOr(limits.maxUploadMb, DEFAULT_CAPABILITIES.limits.maxUploadMb),
      maxDurationSeconds: numberOr(limits.maxDurationSeconds, DEFAULT_CAPABILITIES.limits.maxDurationSeconds),
      maxPendingJobs: numberOr(limits.maxPendingJobs, DEFAULT_CAPABILITIES.limits.maxPendingJobs),
      maxSingleTrackAnalysisSeconds: numberOr(limits.maxSingleTrackAnalysisSeconds, DEFAULT_CAPABILITIES.limits.maxSingleTrackAnalysisSeconds),
      maxLongformDurationSeconds: numberOr(limits.maxLongformDurationSeconds, DEFAULT_CAPABILITIES.limits.maxLongformDurationSeconds),
      maxSourceUploadBytes: numberOr(limits.maxSourceUploadBytes, DEFAULT_CAPABILITIES.limits.maxSourceUploadBytes),
      uploadChunkBytes: numberOr(limits.uploadChunkBytes, DEFAULT_CAPABILITIES.limits.uploadChunkBytes),
      maxActiveUploads: numberOr(limits.maxActiveUploads, DEFAULT_CAPABILITIES.limits.maxActiveUploads),
      maxActiveAnalyses: numberOr(limits.maxActiveAnalyses, DEFAULT_CAPABILITIES.limits.maxActiveAnalyses),
      maxActiveGpuTasks: numberOr(limits.maxActiveGpuTasks, DEFAULT_CAPABILITIES.limits.maxActiveGpuTasks),
      longformScanTimeoutSeconds: numberOr(limits.longformScanTimeoutSeconds, DEFAULT_CAPABILITIES.limits.longformScanTimeoutSeconds),
      minimumFreeDiskBytes: numberOr(limits.minimumFreeDiskBytes, DEFAULT_CAPABILITIES.limits.minimumFreeDiskBytes),
    },
    catalogue: catalogue ? {
      available: booleanOr(catalogue.available, false),
      catalogueSchemaVersion: optionalString(catalogue, 'catalogueSchemaVersion') ?? 'unknown',
      auditSchemaVersion: optionalString(catalogue, 'auditSchemaVersion') ?? 'unknown',
      segmentSchemaVersion: optionalString(catalogue, 'segmentSchemaVersion') ?? 'unknown',
      reportSchemaVersion: optionalString(catalogue, 'reportSchemaVersion') ?? 'unknown',
      autoSegmentationAvailable: booleanOr(catalogue.autoSegmentationAvailable, false),
      supportedRetentionPolicies: stringArray(catalogue.supportedRetentionPolicies).filter(
        (item): item is RetentionPolicy => ['temporary', 'archive', 'custom'].includes(item),
      ),
      freeStorageBytes: numberOr(catalogue.freeStorageBytes, 0),
      archiveStorageUsedBytes: numberOr(catalogue.archiveStorageUsedBytes, 0),
      archiveQuotaBytes: numberOr(catalogue.archiveQuotaBytes, 0),
    } : null,
    visualCueExportAvailable: booleanOr(value.visualCueExportAvailable, false),
    visualCueSheetSchemaVersion: optionalString(value, 'visualCueSheetSchemaVersion') ?? 'unknown',
    visualFeatureArtifactSchemaVersion: optionalString(value, 'visualFeatureArtifactSchemaVersion') ?? 'unknown',
    blenderVisualizerPreset: legacyVisualizerPreset,
    blenderVisualizerDefaultPreset: defaultVisualizerPreset,
    blenderVisualizerPresets,
    blenderVisualizerConfigSchemaVersion: optionalString(value, 'blenderVisualizerConfigSchemaVersion') ?? 'unknown',
    networkFeaturesEnabled: booleanOr(value.networkFeaturesEnabled, false),
    retentionPolicy: value.retentionPolicy === 'explicit-delete-only'
      ? 'explicit-delete-only'
      : DEFAULT_CAPABILITIES.retentionPolicy,
    automaticAnalysisDeletionEnabled: false,
  }
}

function parseAnalysisCatalogueItem(value: unknown): AnalysisCatalogueItem {
  if (!isRecord(value)) throw new ApiError('The server returned an invalid analysis catalogue item.', 'invalid_response')
  return {
    analysisId: requiredString(value, 'analysisId'),
    displayName: requiredString(value, 'displayName'),
    status: requiredString(value, 'status'),
    retentionPolicy: 'persistent',
    createdAt: requiredString(value, 'createdAt'),
    updatedAt: requiredString(value, 'updatedAt'),
    archivedAt: optionalString(value, 'archivedAt') ?? null,
    durationSeconds: nullableNumber(value.durationSeconds),
    analysisSchemaVersion: optionalString(value, 'analysisSchemaVersion') ?? null,
    archiveHealth: requiredString(value, 'archiveHealth'),
    retainedAudioAvailable: booleanOr(value.retainedAudioAvailable, false),
    analysisAvailable: booleanOr(value.analysisAvailable, false),
    storyPlanAvailable: booleanOr(value.storyPlanAvailable, false),
    shotPlanAvailable: booleanOr(value.shotPlanAvailable, false),
    dependentVideoJobCount: numberOr(value.dependentVideoJobCount, 0),
    explicitDeleteEligible: booleanOr(value.explicitDeleteEligible, false),
    legacyMissing: booleanOr(value.legacyMissing, false),
    deletedAt: optionalString(value, 'deletedAt') ?? null,
  }
}

export async function listAnalyses(options: {
  search?: string
  status?: string
  archiveHealth?: string
  sort?: string
  offset?: number
  limit?: number
} = {}): Promise<AnalysisCataloguePage> {
  const query = new URLSearchParams()
  if (options.search) query.set('search', options.search)
  if (options.status) query.set('status', options.status)
  if (options.archiveHealth) query.set('archiveHealth', options.archiveHealth)
  query.set('sort', options.sort ?? 'created_desc')
  query.set('offset', String(options.offset ?? 0))
  query.set('limit', String(options.limit ?? 50))
  const value = await requestJson(`/analyses?${query.toString()}`)
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new ApiError('The server returned an invalid analysis catalogue page.', 'invalid_response')
  }
  return {
    items: value.items.map(parseAnalysisCatalogueItem),
    total: numberOr(value.total, 0),
    offset: numberOr(value.offset, 0),
    limit: numberOr(value.limit, 50),
  }
}

export async function reconcileAnalysis(analysisId: string): Promise<AnalysisCatalogueItem> {
  return parseAnalysisCatalogueItem(await requestJson(
    `/analyses/${encodeURIComponent(analysisId)}/reconcile`,
    { method: 'POST' },
  ))
}

export async function createAnalysis(
  file: File,
  mode: AnalysisMode,
  options: AnalysisOptions,
): Promise<AnalysisJob> {
  const body = new FormData()
  body.append('file', file)
  body.append('mode', mode)
  body.append('permissionConfirmed', 'true')
  body.append('enableLyricalAnalysis', String(options.enableLyricsAnalysis))
  body.append('enableGenreAnalysis', String(options.enableGenreAnalysis))
  body.append('lyricsConsentConfirmed', String(options.lyricsConsentConfirmed))
  body.append('deriveLyricalThemes', String(options.deriveLyricalThemes))
  body.append('allowFeatureFallback', String(options.allowFeatureFallback))
  return parseJob(await requestJson('/analyses', { method: 'POST', body }))
}

export async function patchGenre(jobId: string, patch: GenrePatch): Promise<GenreAnalysis> {
  const value = await requestJson(`/analyses/${encodeURIComponent(jobId)}/genre`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  const genre = parseGenreAnalysis(value)
  if (!genre) throw new ApiError('The server returned invalid genre evidence.', 'invalid_response')
  return genre
}

function parseLyrics(value: unknown): PrivateLyricsTranscript {
  if (!isRecord(value) || typeof value.jobId !== 'string') {
    throw new ApiError('The server returned an invalid private transcript.', 'invalid_response')
  }
  const segments = Array.isArray(value.segments) ? value.segments.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== 'string' || typeof item.text !== 'string') return []
    const qualityDecision = [
      'accepted',
      'uncertain',
      'rejected_as_likely_hallucination',
      'non_lexical',
    ].includes(String(item.qualityDecision))
      ? item.qualityDecision as LyricsSegmentQualityDecision
      : 'uncertain'
    return [{
      id: item.id,
      startSeconds: numberOr(item.startSeconds, 0),
      endSeconds: numberOr(item.endSeconds, 0),
      text: item.text,
      confidence: ['low', 'medium', 'high', 'unknown'].includes(String(item.confidence)) ? item.confidence as Confidence : 'unknown',
      qualityDecision,
      avgLogProbability: typeof item.avgLogProbability === 'number' ? item.avgLogProbability : null,
      noSpeechScore: typeof item.noSpeechScore === 'number' ? item.noSpeechScore : null,
      compressionRatio: typeof item.compressionRatio === 'number' ? item.compressionRatio : null,
      repeatedTokenRatio: typeof item.repeatedTokenRatio === 'number' ? item.repeatedTokenRatio : null,
      activeSectionIds: stringArray(item.activeSectionIds),
      qualityFlags: stringArray(item.qualityFlags),
      userEdited: booleanOr(item.userEdited, false),
    }]
  }) : []
  return {
    schemaVersion: optionalString(value, 'schemaVersion') ?? 'unknown',
    jobId: value.jobId,
    language: optionalString(value, 'language'),
    segments,
    modelId: optionalString(value, 'modelId') ?? 'unknown',
    selectedDevice: optionalString(value, 'selectedDevice') ?? 'unavailable',
    warnings: stringArray(value.warnings),
    userEdited: booleanOr(value.userEdited, false),
    createdAt: optionalString(value, 'createdAt') ?? new Date(0).toISOString(),
  }
}

export async function getLyrics(jobId: string): Promise<PrivateLyricsTranscript> {
  return parseLyrics(await requestJson(`/analyses/${encodeURIComponent(jobId)}/lyrics`))
}

export async function patchLyrics(jobId: string, patch: LyricsPatch): Promise<PrivateLyricsTranscript> {
  return parseLyrics(await requestJson(`/analyses/${encodeURIComponent(jobId)}/lyrics`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  }))
}

export async function deleteLyrics(jobId: string): Promise<void> {
  await request(`/analyses/${encodeURIComponent(jobId)}/lyrics`, { method: 'DELETE' })
}

export function lyricsExportUrl(jobId: string): string {
  return `${API_BASE}/analyses/${encodeURIComponent(jobId)}/lyrics/export`
}

export async function getAnalysis(jobId: string): Promise<AnalysisJob> {
  return parseJob(await requestJson(`/analyses/${encodeURIComponent(jobId)}`))
}

export async function cancelAnalysis(jobId: string): Promise<AnalysisJob> {
  return parseJob(await requestJson(`/analyses/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }))
}

export async function patchAnalysis(jobId: string, updates: FactUpdate[]): Promise<AnalysisJob> {
  return parseJob(await requestJson(`/analyses/${encodeURIComponent(jobId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates }),
  }))
}

export async function generatePrompt(jobId: string, preferences: PromptPreferences): Promise<PromptPackage> {
  const value = await requestJson(`/analyses/${encodeURIComponent(jobId)}/prompt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(preferences),
  })
  const prompt = parsePromptPackage(value)
  if (!prompt) throw new ApiError('The server returned an invalid prompt package.', 'invalid_response')
  return prompt
}

export async function selectPromptCandidate(jobId: string, candidateId: string): Promise<PromptPackage> {
  const value = await requestJson(`/analyses/${encodeURIComponent(jobId)}/prompt`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidateId }),
  })
  const prompt = parsePromptPackage(value)
  if (!prompt) throw new ApiError('The server returned an invalid prompt package.', 'invalid_response')
  return prompt
}

export async function deleteAnalysis(jobId: string): Promise<void> {
  await request(`/analyses/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
}

export function exportUrl(jobId: string, format: 'json' | 'md'): string {
  return `${API_BASE}/analyses/${encodeURIComponent(jobId)}/export.${format}`
}

function parseCueEvent(value: unknown): VisualCueEvent | null {
  if (!isRecord(value) || typeof value.index !== 'number' || typeof value.frame !== 'number') return null
  return {
    index: value.index,
    timeSeconds: numberOr(value.timeSeconds, 0),
    frame: value.frame,
    confidence: ['low', 'medium', 'high', 'unknown'].includes(String(value.confidence))
      ? value.confidence as Confidence
      : 'unknown',
    strength: nullableNumber(value.strength),
    sourcePath: optionalString(value, 'sourcePath') ?? '',
  }
}

function parseCueCurve(value: unknown): VisualCueCurve | null {
  if (!isRecord(value) || !Array.isArray(value.points)) return null
  const simplification = isRecord(value.simplification) ? value.simplification : {}
  const normalization = isRecord(value.normalization) ? value.normalization : {}
  const smoothing = isRecord(value.smoothing) ? value.smoothing : {}
  const points = value.points.flatMap((point) => (
    Array.isArray(point)
      && point.length === 2
      && typeof point[0] === 'number'
      && typeof point[1] === 'number'
      && Number.isFinite(point[0])
      && Number.isFinite(point[1])
      ? [[point[0], point[1]] as [number, number]]
      : []
  ))
  if (points.length < 2) return null
  return {
    pointFormat: ['frame', 'value'],
    points,
    interpolation: 'linear',
    sourceSampleRateHz: numberOr(value.sourceSampleRateHz, 0),
    originalPointCount: numberOr(value.originalPointCount, points.length),
    exportedPointCount: numberOr(value.exportedPointCount, points.length),
    simplification: {
      method: optionalString(simplification, 'method') ?? 'unknown',
      tolerance: numberOr(simplification.tolerance, 0),
      maximumError: numberOr(simplification.maximumError, 0),
      maximumPointCount: numberOr(simplification.maximumPointCount, points.length),
    },
    normalization: {
      method: optionalString(normalization, 'method') ?? 'unknown',
      lowerPercentile: numberOr(normalization.lowerPercentile, 0),
      upperPercentile: numberOr(normalization.upperPercentile, 100),
      normalizationGroup: optionalString(normalization, 'normalizationGroup') ?? 'unknown',
    },
    smoothing: {
      method: optionalString(smoothing, 'method') ?? 'unknown',
      attackSeconds: numberOr(smoothing.attackSeconds, 0),
      releaseSeconds: numberOr(smoothing.releaseSeconds, 0),
      sourceSampleRateHz: numberOr(smoothing.sourceSampleRateHz, 0),
      outputSampleRateHz: numberOr(smoothing.outputSampleRateHz, 0),
    },
  }
}

function parseVisualCueSheet(value: unknown): TrackPromptVisualCueSheet {
  if (!isRecord(value) || value.schemaVersion !== '1.1.0') {
    throw new ApiError('The server returned an unsupported visual cue sheet.', 'invalid_response')
  }
  const source = isRecord(value.source) ? value.source : {}
  const timeline = isRecord(value.timeline) ? value.timeline : {}
  const musicalGrid = isRecord(value.musicalGrid) ? value.musicalGrid : {}
  const bpm = isRecord(musicalGrid.bpm) ? musicalGrid.bpm : {}
  const meter = isRecord(musicalGrid.meter) ? musicalGrid.meter : {}
  const confidence = (raw: unknown): Confidence => (
    ['low', 'medium', 'high', 'unknown'].includes(String(raw)) ? raw as Confidence : 'unknown'
  )
  const requestedMode = source.requestedMode === 'deep' ? 'deep' : 'fast'
  const effectiveMode = source.effectiveMode === 'deep' ? 'deep' : 'fast'
  const sections = Array.isArray(value.sections) ? value.sections.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== 'string') return []
    const stemActivity = isRecord(item.stemActivity)
      ? Object.fromEntries(Object.entries(item.stemActivity).filter((entry): entry is [string, string] => typeof entry[1] === 'string'))
      : {}
    const stemRelativeRms = isRecord(item.stemRelativeRms)
      ? Object.fromEntries(Object.entries(item.stemRelativeRms).filter((entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isFinite(entry[1])))
      : {}
    return [{
      id: item.id,
      neutralLabel: optionalString(item, 'neutralLabel') ?? item.id,
      inferredLabel: optionalString(item, 'inferredLabel') ?? null,
      startSeconds: numberOr(item.startSeconds, 0),
      endSeconds: numberOr(item.endSeconds, 0),
      startFrame: numberOr(item.startFrame, 1),
      endFrame: numberOr(item.endFrame, 1),
      energy: nullableNumber(item.energy),
      loudness: nullableNumber(item.loudness),
      confidence: confidence(item.confidence),
      boundaryConfidence: confidence(item.boundaryConfidence),
      repetitionGroup: optionalString(item, 'repetitionGroup') ?? null,
      vocalActivity: optionalString(item, 'vocalActivity') ?? null,
      instruments: stringArray(item.instruments),
      stemActivity,
      stemRelativeRms,
      sourcePath: optionalString(item, 'sourcePath') ?? '',
    }]
  }) : []
  const transitions = Array.isArray(value.transitions) ? value.transitions.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== 'string') return []
    const direction: VisualCueTransition['direction'] = ['rising', 'falling', 'stable'].includes(String(item.direction))
      ? item.direction as 'rising' | 'falling' | 'stable'
      : 'unknown'
    return [{
      id: item.id,
      timeSeconds: numberOr(item.timeSeconds, 0),
      frame: numberOr(item.frame, 1),
      fromSectionId: optionalString(item, 'fromSectionId') ?? '',
      toSectionId: optionalString(item, 'toSectionId') ?? '',
      energyBefore: nullableNumber(item.energyBefore),
      energyAfter: nullableNumber(item.energyAfter),
      energyDelta: nullableNumber(item.energyDelta),
      direction,
      confidence: confidence(item.confidence),
      sourcePaths: stringArray(item.sourcePaths),
    }]
  }) : []
  const curves: Record<string, VisualCueCurve> = {}
  if (isRecord(value.curves)) {
    for (const [name, raw] of Object.entries(value.curves)) {
      const curve = parseCueCurve(raw)
      if (curve) curves[name] = curve
    }
  }
  return {
    schemaVersion: '1.1.0',
    source: {
      analysisSchemaVersion: optionalString(source, 'analysisSchemaVersion') ?? 'unknown',
      analysisVersion: optionalString(source, 'analysisVersion') ?? 'unknown',
      jobId: requiredString(source, 'jobId'),
      requestedMode,
      effectiveMode,
    },
    timeline: {
      durationSeconds: numberOr(timeline.durationSeconds, 0),
      fps: numberOr(timeline.fps, 30),
      frameStart: numberOr(timeline.frameStart, 1),
      frameEnd: numberOr(timeline.frameEnd, 1),
      framePolicy: 'nearest-half-up-clamped',
    },
    musicalGrid: {
      bpm: { value: nullableNumber(bpm.value), confidence: confidence(bpm.confidence) },
      secondsPerBeat: nullableNumber(musicalGrid.secondsPerBeat),
      meter: { value: optionalString(meter, 'value') ?? null, confidence: confidence(meter.confidence) },
      downbeatsAvailable: false,
    },
    beats: Array.isArray(value.beats) ? value.beats.flatMap((item) => {
      const event = parseCueEvent(item)
      return event ? [event] : []
    }) : [],
    onsets: Array.isArray(value.onsets) ? value.onsets.flatMap((item) => {
      const event = parseCueEvent(item)
      return event ? [event] : []
    }) : [],
    sections,
    transitions,
    curves,
    warnings: stringArray(value.warnings),
  }
}

function parseResolvedVisualizerConfig(value: unknown): ResolvedVisualizerConfig {
  if (!isRecord(value) || value.schemaVersion !== '1.0.0' || !isBlenderVisualizerPreset(value.preset)) {
    throw new ApiError('The server returned an invalid visualizer configuration.', 'invalid_response')
  }
  if (typeof value.seed !== 'number' || !Number.isInteger(value.seed) || value.seed < 0 || value.seed > 2_147_483_647) {
    throw new ApiError('The server returned an invalid visualizer seed.', 'invalid_response')
  }
  const rawParameters = value.parameters
  if (!isRecord(rawParameters)) {
    throw new ApiError('The server returned invalid visualizer parameters.', 'invalid_response')
  }
  const shared = {
    schemaVersion: '1.0.0' as const,
    seed: value.seed,
    defaultedParameters: requiredStringArray(value.defaultedParameters, 'defaulted visualizer parameters'),
    warnings: requiredStringArray(value.warnings, 'visualizer warnings'),
  }
  if (value.preset === 'abstract-geometry') {
    if (Object.keys(rawParameters).length > 0) {
      throw new ApiError('The server returned unexpected Abstract Geometry parameters.', 'invalid_response')
    }
    return { ...shared, preset: value.preset, parameters: {} }
  }

  const numericParameter = (key: Exclude<keyof SpaceJourneyParameters, 'palette'>): number => {
    const parameter = rawParameters[key]
    if (typeof parameter !== 'number' || !Number.isFinite(parameter)) {
      throw new ApiError(`The server returned an invalid ${key} parameter.`, 'invalid_response')
    }
    return parameter
  }
  if (!isSpaceJourneyPalette(rawParameters.palette)) {
    throw new ApiError('The server returned an invalid visualizer palette.', 'invalid_response')
  }
  const parameters: SpaceJourneyParameters = {
    cameraDistance: numericParameter('cameraDistance'),
    cameraOrbitSpeed: numericParameter('cameraOrbitSpeed'),
    ringThickness: numericParameter('ringThickness'),
    ringOcclusion: numericParameter('ringOcclusion'),
    palette: rawParameters.palette,
    glowStrength: numericParameter('glowStrength'),
    shardDensity: numericParameter('shardDensity'),
    fogDepth: numericParameter('fogDepth'),
    bassResponse: numericParameter('bassResponse'),
    drumResponse: numericParameter('drumResponse'),
    vocalResponse: numericParameter('vocalResponse'),
  }
  if (Object.keys(validateSpaceJourneyParameters(parameters)).length > 0) {
    throw new ApiError('The server returned out-of-range visualizer parameters.', 'invalid_response')
  }
  return { ...shared, preset: value.preset, parameters }
}

export async function resolveVisualizerConfig(
  config: VisualizerConfigRequest,
): Promise<ResolvedVisualizerConfig> {
  const value = await requestJson('/visualizer/config/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  const resolved = parseResolvedVisualizerConfig(value)
  if (resolved.preset !== config.preset) {
    throw new ApiError('The server returned a different visualizer preset than requested.', 'invalid_response')
  }
  if (config.seed !== undefined && resolved.seed !== config.seed) {
    throw new ApiError('The server returned a different visualizer seed than requested.', 'invalid_response')
  }
  if (config.preset === 'space-journey' && config.parameters) {
    for (const [name, expected] of Object.entries(config.parameters) as Array<
      [keyof SpaceJourneyParameters, SpaceJourneyParameters[keyof SpaceJourneyParameters]]
    >) {
      if (resolved.preset !== 'space-journey' || resolved.parameters[name] !== expected) {
        throw new ApiError(
          `The server returned a different ${name} parameter than requested.`,
          'invalid_response',
        )
      }
    }
  }
  return resolved
}

export async function exportVisualCues(
  jobId: string,
  preferences: VisualCuePreferences,
): Promise<{ cueSheet: TrackPromptVisualCueSheet; blob: Blob; filename: string }> {
  const query = new URLSearchParams({
    fps: String(preferences.fps),
    includeBeats: String(preferences.includeBeats),
    includeOnsets: String(preferences.includeOnsets),
    includeStemEvidence: String(preferences.includeStemEvidence),
    includeCurves: String(preferences.includeCurves),
    curveDetail: preferences.curveDetail,
  })
  const response = await request(
    `/analyses/${encodeURIComponent(jobId)}/visual-cues/export?${query.toString()}`,
  )
  let value: unknown
  try {
    value = await response.json()
  } catch {
    throw new ApiError('The visual cue export was unreadable.', 'invalid_response', response.status)
  }
  const cueSheet = parseVisualCueSheet(value)
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' })
  return {
    cueSheet,
    blob,
    filename: `trackprompt-${jobId}-visual-cues.json`,
  }
}

export function audioUrl(jobId: string): string {
  return `${API_BASE}/analyses/${encodeURIComponent(jobId)}/audio`
}

export interface EventCallbacks {
  onOpen?: () => void
  onEvent: (event: AnalysisEvent) => void
  onTerminal: (status: 'completed' | 'failed' | 'cancelled' | 'expired') => void
  onConnectionError: () => void
}

function parseEvent(data: string): AnalysisEvent | null {
  let value: unknown
  try {
    value = JSON.parse(data) as unknown
  } catch {
    return null
  }
  if (!isRecord(value)) return null
  const status = typeof value.status === 'string' && statuses.has(value.status as JobStatus)
    ? (value.status as JobStatus)
    : 'queued'
  return {
    jobId: typeof value.jobId === 'string' ? value.jobId : '',
    status,
    mode: value.mode === 'deep' || value.mode === 'fast' ? value.mode : undefined,
    stage: normalizeStage(value.stage, status),
    message: typeof value.message === 'string' ? value.message : 'Analysis state updated.',
    sequence: numberOr(value.sequence, 0),
    timestamp: typeof value.timestamp === 'string' ? value.timestamp : new Date().toISOString(),
    progress: typeof value.progress === 'number' && Number.isFinite(value.progress) ? value.progress : undefined,
  }
}

export function subscribeToAnalysisEvents(jobId: string, callbacks: EventCallbacks): () => void {
  const source = new EventSource(`${API_BASE}/analyses/${encodeURIComponent(jobId)}/events`)
  source.onopen = () => callbacks.onOpen?.()
  const handle = (event: MessageEvent<string>): void => {
    const parsed = parseEvent(event.data)
    if (parsed) callbacks.onEvent(parsed)
  }
  const terminal = (status: 'completed' | 'failed' | 'cancelled' | 'expired') => (event: MessageEvent<string>): void => {
    handle(event)
    callbacks.onTerminal(status)
    source.close()
  }
  source.addEventListener('progress', handle as EventListener)
  source.addEventListener('completed', terminal('completed') as EventListener)
  source.addEventListener('failed', terminal('failed') as EventListener)
  source.addEventListener('cancelled', terminal('cancelled') as EventListener)
  source.addEventListener('expired', terminal('expired') as EventListener)
  source.onmessage = handle
  source.onerror = () => callbacks.onConnectionError()
  return () => source.close()
}

function parseCataloguePage<T>(value: unknown, parser: (item: unknown) => T): CataloguePage<T> {
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new ApiError('The server returned an invalid catalogue page.', 'invalid_response')
  }
  return {
    items: value.items.map(parser),
    total: numberOr(value.total, value.items.length),
    offset: numberOr(value.offset, 0),
    limit: numberOr(value.limit, value.items.length || 1),
  }
}

function parseClient(value: unknown): CatalogueClient {
  if (!isRecord(value)) throw new ApiError('The server returned an invalid client.', 'invalid_response')
  return {
    id: requiredString(value, 'id'),
    displayName: requiredString(value, 'displayName'),
    privateNotes: optionalString(value, 'privateNotes') ?? '',
    tags: stringArray(value.tags),
    archived: booleanOr(value.archived, false),
    projectCount: numberOr(value.projectCount, 0),
    createdAt: requiredString(value, 'createdAt'),
    updatedAt: requiredString(value, 'updatedAt'),
  }
}

function parseProject(value: unknown): CatalogueProject {
  if (!isRecord(value)) throw new ApiError('The server returned an invalid project.', 'invalid_response')
  const retention = ['temporary', 'archive', 'custom'].includes(String(value.retentionPolicy))
    ? value.retentionPolicy as RetentionPolicy
    : 'archive'
  return {
    id: requiredString(value, 'id'),
    clientId: requiredString(value, 'clientId'),
    name: requiredString(value, 'name'),
    description: optionalString(value, 'description') ?? '',
    status: optionalString(value, 'status') ?? 'active',
    retentionPolicy: retention,
    retentionUntil: optionalString(value, 'retentionUntil') ?? null,
    tags: stringArray(value.tags),
    archivedAt: optionalString(value, 'archivedAt') ?? null,
    storageBytes: numberOr(value.storageBytes, 0),
    batchCount: numberOr(value.batchCount, 0),
    createdAt: requiredString(value, 'createdAt'),
    updatedAt: requiredString(value, 'updatedAt'),
  }
}

function parseBatch(value: unknown): CatalogueBatch {
  if (!isRecord(value)) throw new ApiError('The server returned an invalid batch.', 'invalid_response')
  const state = typeof value.state === 'string' ? value.state as CatalogueBatch['state'] : 'draft'
  return {
    id: requiredString(value, 'id'),
    projectId: requiredString(value, 'projectId'),
    name: requiredString(value, 'name'),
    sequence: numberOr(value.sequence, 0),
    defaultAnalysisMode: value.defaultAnalysisMode === 'deep' ? 'deep' : 'fast',
    enableGenreAnalysis: booleanOr(value.enableGenreAnalysis, false),
    enableLyricalAnalysis: booleanOr(value.enableLyricalAnalysis, false),
    lyricsConsentConfirmed: booleanOr(value.lyricsConsentConfirmed, false),
    state,
    itemTotal: numberOr(value.itemTotal, 0),
    completedItems: numberOr(value.completedItems, 0),
    failedItems: numberOr(value.failedItems, 0),
    durationSeconds: numberOr(value.durationSeconds, 0),
    progress: numberOr(value.progress, 0),
    createdAt: requiredString(value, 'createdAt'),
    completedAt: optionalString(value, 'completedAt') ?? null,
  }
}

function parseUploadSession(value: unknown): UploadSession {
  if (!isRecord(value)) throw new ApiError('The server returned an invalid upload session.', 'invalid_response')
  return {
    id: requiredString(value, 'id'),
    batchId: requiredString(value, 'batchId'),
    displayName: requiredString(value, 'displayName'),
    totalBytes: numberOr(value.totalBytes, 0),
    receivedBytes: numberOr(value.receivedBytes, 0),
    chunkSizeBytes: numberOr(value.chunkSizeBytes, 16 * 1024 * 1024),
    expectedSha256: optionalString(value, 'expectedSha256') ?? null,
    state: typeof value.state === 'string' ? value.state as UploadSession['state'] : 'failed',
    assetId: optionalString(value, 'assetId') ?? null,
    duplicateAssetId: optionalString(value, 'duplicateAssetId') ?? null,
    createdAt: requiredString(value, 'createdAt'),
    updatedAt: requiredString(value, 'updatedAt'),
    expiresAt: requiredString(value, 'expiresAt'),
  }
}

function parseAsset(value: unknown): SourceAsset {
  if (!isRecord(value)) throw new ApiError('The server returned an invalid source asset.', 'invalid_response')
  return {
    id: requiredString(value, 'id'),
    projectId: requiredString(value, 'projectId'),
    batchId: requiredString(value, 'batchId'),
    displayName: requiredString(value, 'displayName'),
    contentSha256: requiredString(value, 'contentSha256'),
    byteSize: numberOr(value.byteSize, 0),
    durationSeconds: numberOr(value.durationSeconds, 0),
    codec: requiredString(value, 'codec'),
    container: requiredString(value, 'container'),
    sampleRate: numberOr(value.sampleRate, 0),
    channels: numberOr(value.channels, 0),
    originalOrder: numberOr(value.originalOrder, 0),
    uploadState: typeof value.uploadState === 'string' ? value.uploadState as SourceAsset['uploadState'] : 'failed',
    storageState: optionalString(value, 'storageState') ?? 'unknown',
    archivalState: typeof value.archivalState === 'string' ? value.archivalState as RetentionPolicy : 'archive',
    segmentationState: optionalString(value, 'segmentationState') ?? 'not_started',
    createdAt: requiredString(value, 'createdAt'),
  }
}

function parseSegment(value: unknown): VirtualSegment {
  if (!isRecord(value)) throw new ApiError('The server returned an invalid segment.', 'invalid_response')
  const evidence: Record<string, number> = {}
  if (isRecord(value.evidence)) {
    Object.entries(value.evidence).forEach(([key, item]) => {
      if (typeof item === 'number' && Number.isFinite(item)) evidence[key] = item
    })
  }
  return {
    id: requiredString(value, 'id'),
    sourceAssetId: requiredString(value, 'sourceAssetId'),
    sequenceIndex: numberOr(value.sequenceIndex, 0),
    label: requiredString(value, 'label'),
    startSeconds: numberOr(value.startSeconds, 0),
    endSeconds: numberOr(value.endSeconds, 0),
    stableCoreStartSeconds: numberOr(value.stableCoreStartSeconds, 0),
    stableCoreEndSeconds: numberOr(value.stableCoreEndSeconds, 0),
    transitionInStartSeconds: nullableNumber(value.transitionInStartSeconds),
    transitionInEndSeconds: nullableNumber(value.transitionInEndSeconds),
    transitionOutStartSeconds: nullableNumber(value.transitionOutStartSeconds),
    transitionOutEndSeconds: nullableNumber(value.transitionOutEndSeconds),
    confidence: ['low', 'medium', 'high', 'unknown'].includes(String(value.confidence)) ? value.confidence as Confidence : 'unknown',
    confidenceScore: nullableNumber(value.confidenceScore),
    transitionType: typeof value.transitionType === 'string' ? value.transitionType as VirtualSegment['transitionType'] : 'uncertain',
    reviewState: typeof value.reviewState === 'string' ? value.reviewState as VirtualSegment['reviewState'] : 'unresolved',
    accepted: booleanOr(value.accepted, false),
    childAnalysisJobId: optionalString(value, 'childAnalysisJobId') ?? null,
    evidence,
    revision: numberOr(value.revision, 1),
  }
}

function parseSegmentationJob(value: unknown): SegmentationJob {
  if (!isRecord(value)) throw new ApiError('The server returned an invalid segmentation job.', 'invalid_response')
  const rawState = optionalString(value, 'state') ?? 'failed'
  const state = ['queued', 'running', 'completed', 'failed', 'cancelled'].includes(rawState)
    ? rawState as SegmentationJob['state']
    : 'failed'
  return {
    id: requiredString(value, 'id'),
    assetId: requiredString(value, 'assetId'),
    state,
    stage: optionalString(value, 'stage') ?? 'Unknown stage',
    progress: numberOr(value.progress, 0),
    observationCount: numberOr(value.observationCount, 0),
    candidateCount: numberOr(value.candidateCount, 0),
    refinedBoundaryCount: numberOr(value.refinedBoundaryCount, 0),
    peakBufferBytes: numberOr(value.peakBufferBytes, 0),
    elapsedSeconds: numberOr(value.elapsedSeconds, 0),
    errorCode: optionalString(value, 'errorCode') ?? null,
    createdAt: requiredString(value, 'createdAt'),
    updatedAt: requiredString(value, 'updatedAt'),
  }
}

function parseQueueItem(value: unknown): QueueItem {
  if (!isRecord(value)) throw new ApiError('The server returned an invalid queue item.', 'invalid_response')
  return {
    id: requiredString(value, 'id'),
    batchId: requiredString(value, 'batchId'),
    segmentId: requiredString(value, 'segmentId'),
    state: typeof value.state === 'string' ? value.state as QueueItem['state'] : 'failed',
    attempt: numberOr(value.attempt, 0),
    analysisMode: value.analysisMode === 'deep' ? 'deep' : 'fast',
    jobId: optionalString(value, 'jobId') ?? null,
    failureReason: optionalString(value, 'failureReason') ?? null,
    createdAt: requiredString(value, 'createdAt'),
    updatedAt: requiredString(value, 'updatedAt'),
  }
}

export async function listCatalogueClients(search = '', offset = 0, limit = 50): Promise<CataloguePage<CatalogueClient>> {
  const query = new URLSearchParams({ search, offset: String(offset), limit: String(limit) })
  return parseCataloguePage(await requestJson(`/clients?${query}`), parseClient)
}

export async function createCatalogueClient(displayName: string): Promise<CatalogueClient> {
  return parseClient(await requestJson('/clients', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ displayName }),
  }))
}

export async function updateCatalogueClient(
  clientId: string,
  patch: { displayName?: string; archived?: boolean },
): Promise<CatalogueClient> {
  return parseClient(await requestJson(`/clients/${encodeURIComponent(clientId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
  }))
}

export async function listCatalogueProjects(clientId: string): Promise<CatalogueProject[]> {
  const page = parseCataloguePage(
    await requestJson(`/clients/${encodeURIComponent(clientId)}/projects?limit=200`),
    parseProject,
  )
  return page.items
}

export async function createCatalogueProject(
  clientId: string,
  name: string,
  retentionPolicy: RetentionPolicy,
): Promise<CatalogueProject> {
  return parseProject(await requestJson('/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clientId, name, retentionPolicy }),
  }))
}

export async function updateCatalogueProject(
  projectId: string,
  patch: { name?: string; archived?: boolean; retentionPolicy?: RetentionPolicy },
): Promise<CatalogueProject> {
  return parseProject(await requestJson(`/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
  }))
}

export async function permanentlyDeleteCatalogueProject(projectId: string): Promise<void> {
  await requestJson(`/projects/${encodeURIComponent(projectId)}?confirm=true`, { method: 'DELETE' })
}

export async function listCatalogueBatches(projectId: string): Promise<CatalogueBatch[]> {
  const page = parseCataloguePage(
    await requestJson(`/projects/${encodeURIComponent(projectId)}/batches?limit=200`),
    parseBatch,
  )
  return page.items
}

export async function createCatalogueBatch(projectId: string, name: string): Promise<CatalogueBatch> {
  return parseBatch(await requestJson(`/projects/${encodeURIComponent(projectId)}/batches`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, defaultAnalysisMode: 'fast' }),
  }))
}

export async function createUploadSession(
  batchId: string,
  file: File,
  originalOrder: number,
): Promise<UploadSession> {
  return parseUploadSession(await requestJson('/upload-sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      batchId,
      displayName: file.name,
      totalBytes: file.size,
      idempotencyKey: `${batchId}:${originalOrder}:${file.size}:${file.lastModified}`,
      originalOrder,
      permissionConfirmed: true,
    }),
  }))
}

async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return Array.from(new Uint8Array(digest)).map((value) => value.toString(16).padStart(2, '0')).join('')
}

export async function appendUploadChunk(session: UploadSession, file: File): Promise<number> {
  const start = session.receivedBytes
  const endExclusive = Math.min(file.size, start + session.chunkSizeBytes)
  const chunk = file.slice(start, endExclusive)
  const chunkHash = await sha256Hex(chunk)
  const value = await requestJson(`/upload-sessions/${encodeURIComponent(session.id)}`, {
    method: 'PATCH',
    headers: {
      'Content-Range': `bytes ${start}-${endExclusive - 1}/${file.size}`,
      'Upload-Offset': String(start),
      'X-Chunk-SHA256': chunkHash,
      'Content-Type': 'application/octet-stream',
    },
    body: chunk,
  })
  if (!isRecord(value)) throw new ApiError('The server returned invalid chunk progress.', 'invalid_response')
  return numberOr(value.receivedBytes, start)
}

export async function getUploadSession(uploadId: string): Promise<UploadSession> {
  return parseUploadSession(await requestJson(`/upload-sessions/${encodeURIComponent(uploadId)}`))
}

export async function completeUploadSession(uploadId: string): Promise<SourceAsset> {
  return parseAsset(await requestJson(`/upload-sessions/${encodeURIComponent(uploadId)}/complete`, { method: 'POST' }))
}

export async function cancelUploadSession(uploadId: string): Promise<void> {
  await requestJson(`/upload-sessions/${encodeURIComponent(uploadId)}`, { method: 'DELETE' })
}

export async function listSourceAssets(batchId: string): Promise<SourceAsset[]> {
  return parseCataloguePage(
    await requestJson(`/batches/${encodeURIComponent(batchId)}/assets?limit=200`),
    parseAsset,
  ).items
}

export async function segmentSourceAsset(assetId: string): Promise<VirtualSegment[]> {
  const value = await requestJson(`/assets/${encodeURIComponent(assetId)}/segment`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  })
  if (!isRecord(value) || !Array.isArray(value.segments)) {
    throw new ApiError('The server returned invalid segmentation results.', 'invalid_response')
  }
  return value.segments.map(parseSegment)
}

export async function startSegmentationJob(assetId: string): Promise<SegmentationJob> {
  return parseSegmentationJob(await requestJson(`/assets/${encodeURIComponent(assetId)}/segmentation-jobs`, {
    method: 'POST',
  }))
}

export async function getSegmentationJob(jobId: string): Promise<SegmentationJob> {
  return parseSegmentationJob(await requestJson(`/segmentation-jobs/${encodeURIComponent(jobId)}`))
}

export async function cancelSegmentationJob(jobId: string): Promise<SegmentationJob> {
  return parseSegmentationJob(await requestJson(`/segmentation-jobs/${encodeURIComponent(jobId)}`, {
    method: 'DELETE',
  }))
}

export async function listVirtualSegments(assetId: string): Promise<VirtualSegment[]> {
  const value = await requestJson(`/assets/${encodeURIComponent(assetId)}/segments`)
  if (!Array.isArray(value)) throw new ApiError('The server returned invalid segments.', 'invalid_response')
  return value.map(parseSegment)
}

export async function reviewSegment(
  assetId: string,
  segmentId: string,
  operation: 'accept' | 'reject',
): Promise<VirtualSegment[]> {
  const value = await requestJson(`/assets/${encodeURIComponent(assetId)}/segments`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation, segmentId, reason: `Boundary ${operation}` }),
  })
  if (!Array.isArray(value)) throw new ApiError('The server returned invalid segments.', 'invalid_response')
  return value.map(parseSegment)
}

export async function editVirtualSegments(assetId: string, edit: SegmentEdit): Promise<VirtualSegment[]> {
  const value = await requestJson(`/assets/${encodeURIComponent(assetId)}/segments`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(edit),
  })
  if (!Array.isArray(value)) throw new ApiError('The server returned invalid segments.', 'invalid_response')
  return value.map(parseSegment)
}

export async function enqueueSegmentAnalyses(assetId: string): Promise<QueueItem[]> {
  const value = await requestJson(`/assets/${encodeURIComponent(assetId)}/segments/analyze`, { method: 'POST' })
  if (!Array.isArray(value)) throw new ApiError('The server returned an invalid queue.', 'invalid_response')
  return value.map(parseQueueItem)
}

export async function listBatchQueue(batchId: string): Promise<QueueItem[]> {
  const value = await requestJson(`/batches/${encodeURIComponent(batchId)}/queue`)
  if (!Array.isArray(value)) throw new ApiError('The server returned an invalid queue.', 'invalid_response')
  return value.map(parseQueueItem)
}

export async function setBatchAction(batchId: string, action: 'start' | 'pause' | 'resume' | 'cancel' | 'retry-failed'): Promise<unknown> {
  return requestJson(`/batches/${encodeURIComponent(batchId)}/${action}`, { method: 'POST' })
}

export async function listProjectAudit(projectId: string): Promise<AuditEvent[]> {
  const page = parseCataloguePage(await requestJson(`/projects/${encodeURIComponent(projectId)}/audit?limit=200`), (value) => {
    if (!isRecord(value)) throw new ApiError('The server returned an invalid audit event.', 'invalid_response')
    return {
      eventId: requiredString(value, 'eventId'), timestamp: requiredString(value, 'timestamp'),
      sequence: numberOr(value.sequence, 0), projectId: requiredString(value, 'projectId'),
      batchId: optionalString(value, 'batchId') ?? null, entityType: requiredString(value, 'entityType'),
      entityId: requiredString(value, 'entityId'), eventType: requiredString(value, 'eventType'),
      actorType: requiredString(value, 'actorType'), correlationId: requiredString(value, 'correlationId'),
      schemaVersion: requiredString(value, 'schemaVersion'), payload: isRecord(value.payload) ? value.payload : {},
      previousEventHash: requiredString(value, 'previousEventHash'), eventHash: requiredString(value, 'eventHash'),
    }
  })
  return page.items
}

export async function generateBatchReport(batchId: string): Promise<void> {
  await requestJson(`/batches/${encodeURIComponent(batchId)}/report`, { method: 'POST' })
}

export function batchReportUrl(batchId: string, format: 'json' | 'md' | 'csv'): string {
  return `${API_BASE}/batches/${encodeURIComponent(batchId)}/report.${format}`
}

function parseRendererContractSummary(value: unknown): RendererContractSummary {
  if (!isRecord(value)) {
    throw new ApiError('The server returned an invalid renderer contract summary.', 'invalid_response')
  }
  const numberField = (key: string): number => {
    const field = value[key]
    if (typeof field !== 'number' || !Number.isFinite(field) || field <= 0) {
      throw new ApiError(`The server returned an invalid renderer ${key}.`, 'invalid_response')
    }
    return field
  }
  return {
    artist: requiredString(value, 'artist'),
    title: requiredString(value, 'title'),
    bpm: numberField('bpm'),
    meter: requiredString(value, 'meter'),
    totalBars: numberField('totalBars'),
    gridDurationSeconds: numberField('gridDurationSeconds'),
    masterDurationSeconds: nullableNumber(value.masterDurationSeconds),
    tailDurationSeconds: nullableNumber(value.tailDurationSeconds),
    width: numberField('width'),
    height: numberField('height'),
    fps: numberField('fps'),
  }
}

function parseRendererDescriptor(value: unknown): RendererDescriptor {
  if (!isRecord(value) || !RENDERER_AVAILABILITY_STATES.includes(value.availability as RendererAvailabilityState)) {
    throw new ApiError('The server returned an invalid renderer descriptor.', 'invalid_response')
  }
  const requirements = Array.isArray(value.requirements)
    ? value.requirements.map((requirement) => {
        if (!isRecord(requirement)) {
          throw new ApiError('The server returned an invalid renderer requirement.', 'invalid_response')
        }
        return {
          id: requiredString(requirement, 'id'),
          label: requiredString(requirement, 'label'),
          available: booleanOr(requirement.available, false),
          requiredForPreparation: booleanOr(requirement.requiredForPreparation, true),
          detail: requiredString(requirement, 'detail'),
        }
      })
    : []
  const platform = value.platform
  if (platform !== 'windows' && platform !== 'cross-platform') {
    throw new ApiError('The server returned an invalid renderer platform.', 'invalid_response')
  }
  const designPreset = value.designPreset == null
    ? null
    : parseSpectrumDesignPresetSummary(value.designPreset)
  return {
    rendererId: requiredString(value, 'rendererId'),
    displayName: requiredString(value, 'displayName'),
    description: requiredString(value, 'description'),
    platform,
    capabilities: stringArray(value.capabilities),
    availability: value.availability as RendererAvailabilityState,
    available: booleanOr(value.available, false),
    preparationAvailable: booleanOr(value.preparationAvailable, false),
    previewAvailability: parseSpectrumProductionAvailability(value.previewAvailability),
    captureAvailability: parseSpectrumProductionAvailability(value.captureAvailability),
    previewAvailable: booleanOr(value.previewAvailable, false),
    captureAvailable: booleanOr(value.captureAvailable, false),
    warnings: stringArray(value.warnings),
    requirements,
    contractSummary: value.contractSummary == null
      ? null
      : parseRendererContractSummary(value.contractSummary),
    designPreset,
    geometryCapability: parseSpectrumGeometryCapability(value.geometryCapability),
  }
}

function parseSpectrumBackgroundMode(
  value: unknown,
  fallback: SpectrumBackgroundMode,
): SpectrumBackgroundMode {
  return typeof value === 'string'
    && (SPECTRUM_BACKGROUND_MODES as readonly string[]).includes(value)
    ? value as SpectrumBackgroundMode
    : fallback
}

function isWzhkGenerativeShapeId(value: unknown): value is WzhkGenerativeShapeId {
  return typeof value === 'string'
    && (WZHK_GENERATIVE_SHAPE_IDS as readonly string[]).includes(value)
}

function parseSpectrumGenerativeGeometrySummary(
  value: unknown,
): SpectrumGenerativeGeometrySummary | null {
  if (!isRecord(value)
      || value.subsystemId !== 'wzhk-generative-geometry'
      || value.renderMode !== 'neopixel-points'
      || !['preview', 'production', 'high'].includes(String(value.performanceProfile))) {
    return null
  }
  const seed = nullableNumber(value.seed)
  const pointCount = nullableNumber(value.pointCount)
  const shapeFamilies = Array.isArray(value.shapeFamilies)
    ? value.shapeFamilies.filter(isWzhkGenerativeShapeId)
    : []
  if (seed === null || pointCount === null || shapeFamilies.length < 6) return null
  return {
    enabled: booleanOr(value.enabled, false),
    subsystemId: 'wzhk-generative-geometry',
    renderMode: 'neopixel-points',
    seed,
    pointCount,
    performanceProfile: value.performanceProfile as 'preview' | 'production' | 'high',
    shapeFamilies,
  }
}

function parseSpectrumDesignPresetSummary(value: unknown): SpectrumDesignPresetSummary {
  if (!isRecord(value) || value.presetId !== 'scattered'
      || value.previewTimingSource !== 'external-media-player-position'
      || value.productionTimingSource !== 'trackprompt-production-clock'
      || value.previewTimingAccuracy !== 'preview-level'
      || value.productionTimingAccuracy !== 'host-monotonic-process-boundary'
      || !Array.isArray(value.sections)) {
    throw new ApiError('The server returned an invalid Spectrum design preset.', 'invalid_response')
  }
  const sections = value.sections.map((section) => {
    if (!isRecord(section) || !['intro', 'main', 'outro', 'post-grid-tail'].includes(String(section.id))) {
      throw new ApiError('The server returned an invalid Spectrum timeline section.', 'invalid_response')
    }
    return {
      id: section.id as SpectrumPreviewSection | 'post-grid-tail',
      label: requiredString(section, 'label'),
      startSeconds: numberOr(section.startSeconds, -1),
      endSeconds: nullableNumber(section.endSeconds),
      spectrumColor: requiredString(section, 'spectrumColor'),
    }
  })
  if (sections.length !== 4 || sections.some((section) => section.startSeconds < 0 || (section.endSeconds !== null && section.endSeconds <= section.startSeconds))) {
    throw new ApiError('The server returned an invalid Spectrum timeline.', 'invalid_response')
  }
  return {
    presetId: 'scattered',
    displayName: requiredString(value, 'displayName'),
    previewTimingSource: 'external-media-player-position',
    productionTimingSource: 'trackprompt-production-clock',
    previewTimingAccuracy: 'preview-level',
    productionTimingAccuracy: 'host-monotonic-process-boundary',
    progressVisible: booleanOr(value.progressVisible, false),
    backgroundMode: parseSpectrumBackgroundMode(value.backgroundMode, 'generative-geometry'),
    generativeGeometry: parseSpectrumGenerativeGeometrySummary(value.generativeGeometry),
    sections,
  }
}

function parseSpectrumGeometryCapability(value: unknown): SpectrumGeometryCapability | null {
  if (!isRecord(value)
      || typeof value.state !== 'string'
      || !(SPECTRUM_GEOMETRY_CAPABILITY_STATES as readonly string[]).includes(value.state)) {
    return null
  }
  return {
    state: value.state as SpectrumGeometryCapability['state'],
    webgl2: typeof value.webgl2 === 'boolean' ? value.webgl2 : null,
    gpuRenderer: optionalString(value, 'gpuRenderer') ?? null,
    shaderCompiled: typeof value.shaderCompiled === 'boolean' ? value.shaderCompiled : null,
    performanceMeasured: booleanOr(value.performanceMeasured, false),
    performanceSufficient: typeof value.performanceSufficient === 'boolean'
      ? value.performanceSufficient
      : null,
    rendererFps: nullableNumber(value.rendererFps),
    averageFrameTimeMs: nullableNumber(value.averageFrameTimeMs),
    pointCount: nullableNumber(value.pointCount),
    detail: optionalString(value, 'detail') ?? null,
  }
}

function boundedInteger(value: unknown, minimum: number, maximum: number): number | null {
  return typeof value === 'number'
    && Number.isInteger(value)
    && value >= minimum
    && value <= maximum
    ? value
    : null
}

function boundedNumber(value: unknown, minimum: number, maximum: number): number | null {
  return typeof value === 'number'
    && Number.isFinite(value)
    && value >= minimum
    && value <= maximum
    ? value
    : null
}

function parseSpectrumGeometryShapeSpec(
  value: unknown,
  fallbackSeed: number,
): SpectrumGeometryShapeSpec | null {
  if (!isRecord(value) || !isWzhkGenerativeShapeId(value.shapeId)) return null
  return {
    shapeId: value.shapeId,
    seed: boundedInteger(value.seed, 0, 2_147_483_647) ?? fallbackSeed,
  }
}

function parseSpectrumGenerativePreviewOverride(
  value: unknown,
): SpectrumGenerativePreviewOverride | null {
  if (!isRecord(value) || !['shape', 'morph', 'section', 'lab'].includes(String(value.mode))) {
    return null
  }
  const seed = boundedInteger(value.seed, 0, 2_147_483_647) ?? 0
  const shapeA = parseSpectrumGeometryShapeSpec(value.shapeA, seed)
  const shapeB = parseSpectrumGeometryShapeSpec(value.shapeB, seed)
  const pointCount = boundedInteger(value.pointCount, 16, 16_384)
  const rotationDegrees = boundedNumber(value.rotationDegrees, -360, 360)
  const scale = boundedNumber(value.scale, Number.EPSILON, 4)
  const common = {
    audioMode: value.audioMode === 'simulated' ? 'simulated' as const : 'disabled' as const,
    ...(pointCount === null ? {} : { pointCount }),
    ...(rotationDegrees === null ? {} : { rotationDegrees }),
    ...(scale === null ? {} : { scale }),
    seed,
  }
  if (value.mode === 'shape' && shapeA) {
    return { ...common, mode: 'shape', shapeA }
  }
  const morphProgress = boundedNumber(value.morphProgress, 0, 1)
  if (value.mode === 'morph' && shapeA && shapeB && morphProgress !== null) {
    return { ...common, mode: 'morph', shapeA, shapeB, morphProgress }
  }
  if (value.mode === 'section'
      && ['intro', 'main', 'outro', 'post-grid-tail'].includes(String(value.section))) {
    return {
      ...common,
      mode: 'section',
      section: value.section as 'intro' | 'main' | 'outro' | 'post-grid-tail',
    }
  }
  if (value.mode === 'lab' && shapeA) {
    return {
      ...common,
      mode: 'lab',
      shapeA,
      ...(shapeB === null ? {} : { shapeB }),
      ...(shapeB === null || morphProgress === null ? {} : { morphProgress }),
    }
  }
  return null
}

function parseSpectrumGeometryTelemetry(value: unknown): SpectrumGeometryTelemetry | null {
  if (!isRecord(value)) return null
  const telemetry: SpectrumGeometryTelemetry = {
    actualFps: nullableNumber(value.actualFps) ?? nullableNumber(value.rendererFps),
    averageFrameTimeMs: nullableNumber(value.averageFrameTimeMs),
    pointCount: nullableNumber(value.pointCount),
    droppedRendererFrames: nullableNumber(value.droppedRendererFrames)
      ?? nullableNumber(value.droppedFrames)
      ?? nullableNumber(value.droppedRendererUpdates),
    gpuRenderer: optionalString(value, 'gpuRenderer') ?? null,
  }
  return Object.values(telemetry).every((field) => field === null) ? null : telemetry
}

const SPECTRUM_PRODUCTION_STATES = new Set<SpectrumProductionState>([
  'WORKSPACE_READY', 'PREVIEW_READY', 'CAPTURE_PREFLIGHT', 'CAPTURE_READY', 'CAPTURING',
  'CAPTURE_COMPLETE', 'MUXING', 'VALIDATING', 'COMPLETE', 'FAILED', 'CANCELLED',
])

const SPECTRUM_PRODUCTION_AVAILABILITIES = new Set<SpectrumProductionAvailability>([
  'READY_FOR_PREVIEW', 'READY_FOR_CAPTURE', 'MISSING_RAINMETER', 'MISSING_FFMPEG',
  'MISSING_CAPTURE_PROVIDER', 'INVALID_WORKSPACE', 'MISSING_MASTER', 'INVALID_MASTER_DURATION',
])

function parseSpectrumProductionAvailability(value: unknown): SpectrumProductionAvailability | null {
  return typeof value === 'string' && SPECTRUM_PRODUCTION_AVAILABILITIES.has(value as SpectrumProductionAvailability)
    ? value as SpectrumProductionAvailability
    : null
}

function parseSpectrumMasterTiming(value: unknown): SpectrumMasterTiming | null {
  if (!isRecord(value)) return null
  return {
    gridDurationSeconds: numberOr(value.gridDurationSeconds, 0),
    masterDurationSeconds: numberOr(value.masterDurationSeconds, 0),
    tailDurationSeconds: numberOr(value.tailDurationSeconds, 0),
    configuredFinalFadeSeconds: numberOr(value.configuredFinalFadeSeconds, 0),
    finalFadeStartSeconds: numberOr(value.finalFadeStartSeconds, 0),
  }
}

function parseCaptureProvider(value: unknown): SpectrumCaptureProvider {
  if (!isRecord(value) || value.providerId !== 'ffmpeg-gfxcapture') {
    throw new ApiError('The server returned an invalid Spectrum capture provider.', 'invalid_response')
  }
  return {
    providerId: 'ffmpeg-gfxcapture',
    displayName: requiredString(value, 'displayName'),
    available: booleanOr(value.available, false),
    supportsWindowCapture: booleanOr(value.supportsWindowCapture, false),
    supportsConstantFrameRate: booleanOr(value.supportsConstantFrameRate, false),
    crashResilientContainer: 'matroska',
    encoder: value.encoder === 'h264_nvenc' || value.encoder === 'libx264' ? value.encoder : null,
    hardwareAccelerationVerified: booleanOr(value.hardwareAccelerationVerified, false),
    detail: requiredString(value, 'detail'),
  }
}

function parseCapturePreflight(value: unknown): SpectrumCapturePreflight | null {
  if (!isRecord(value)) return null
  const availability = parseSpectrumProductionAvailability(value.availability)
  if (!availability) throw new ApiError('The server returned an invalid capture readiness state.', 'invalid_response')
  return {
    availability,
    ready: booleanOr(value.ready, false),
    provider: parseCaptureProvider(value.provider),
    timing: parseSpectrumMasterTiming(value.timing),
    rainmeterPathResolved: booleanOr(value.rainmeterPathResolved, false),
    ffmpegPathResolved: booleanOr(value.ffmpegPathResolved, false),
    ffprobePathResolved: booleanOr(value.ffprobePathResolved, false),
    playbackPathResolved: booleanOr(value.playbackPathResolved, false),
    workspaceValid: booleanOr(value.workspaceValid, false),
    masterValid: booleanOr(value.masterValid, false),
    operatorNotice: requiredString(value, 'operatorNotice'),
    warnings: stringArray(value.warnings),
  }
}

function parseArtifacts(value: unknown): SpectrumArtifact[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((artifact) => {
    if (!isRecord(artifact)) return []
    const createdState = String(artifact.createdState) as SpectrumProductionState
    if (!SPECTRUM_PRODUCTION_STATES.has(createdState)) return []
    return [{
      artifactType: requiredString(artifact, 'artifactType'),
      relativePath: requiredString(artifact, 'relativePath'),
      sha256: requiredString(artifact, 'sha256'),
      sizeBytes: numberOr(artifact.sizeBytes, 0),
      createdState,
      provenance: requiredString(artifact, 'provenance'),
      timestampSeconds: nullableNumber(artifact.timestampSeconds),
    }]
  })
}

function parseSynchronization(value: unknown): SpectrumCaptureSynchronization | null {
  if (!isRecord(value) || value.method !== 'owned-playback-process-ffmpeg-progress-clock'
      || value.precision !== 'host-monotonic-process-boundary') return null
  return {
    method: 'owned-playback-process-ffmpeg-progress-clock',
    measuredStartOffsetSeconds: numberOr(value.measuredStartOffsetSeconds, 0),
    measuredEndOffsetSeconds: numberOr(value.measuredEndOffsetSeconds, 0),
    correctionAppliedSeconds: numberOr(value.correctionAppliedSeconds, 0),
    precision: 'host-monotonic-process-boundary',
  }
}

function parseValidationReport(value: unknown): SpectrumValidationReport | null {
  if (!isRecord(value) || !Array.isArray(value.checks)) return null
  return {
    valid: booleanOr(value.valid, false),
    checks: value.checks.flatMap((check) => isRecord(check) ? [{
      id: requiredString(check, 'id'),
      passed: booleanOr(check.passed, false),
      measured: requiredString(check, 'measured'),
      expected: requiredString(check, 'expected'),
    }] : []),
  }
}

function parseSpectrumWorkspaceJob(value: unknown): SpectrumWorkspaceJob {
  const schemaValid = isRecord(value) && ['1.0.0', '2.0.0', '3.0.0', '4.0.0'].includes(String(value.schemaVersion))
  const state = isRecord(value) ? String(value.state) : ''
  if (!schemaValid || !isRecord(value) || value.rendererId !== 'wzhk-spectrum'
      || (state !== 'PREPARED' && !SPECTRUM_PRODUCTION_STATES.has(state as SpectrumProductionState))) {
    throw new ApiError('The server returned an invalid Spectrum workspace job.', 'invalid_response')
  }
  const contractSummary = parseRendererContractSummary(value.contractSummary)
  const compositionRevision = value.compositionRevision ?? null
  if (compositionRevision !== null && compositionRevision !== 'scattered-geometry-first-3.7') {
    throw new ApiError('The server returned an invalid Spectrum composition revision.', 'invalid_response')
  }
  return {
    schemaVersion: value.schemaVersion as '1.0.0' | '2.0.0' | '3.0.0' | '4.0.0',
    jobId: requiredString(value, 'jobId'),
    rendererId: 'wzhk-spectrum',
    state: state as SpectrumProductionState | 'PREPARED',
    workspaceRelativePath: requiredString(value, 'workspaceRelativePath'),
    contractValid: booleanOr(value.contractValid, false),
    brandingApplied: booleanOr(value.brandingApplied, false),
    vendorUnchanged: booleanOr(value.vendorUnchanged, false),
    generatedWorkspaceHash: requiredString(value, 'generatedWorkspaceHash'),
    vendorSourceHash: requiredString(value, 'vendorSourceHash'),
    vendorCommit: requiredString(value, 'vendorCommit'),
    logoResolved: booleanOr(value.logoResolved, false),
    masterAudioResolved: booleanOr(value.masterAudioResolved, false),
    warnings: stringArray(value.warnings),
    contractSummary,
    mode: value.mode === 'production' ? 'production' : 'preview',
    backgroundMode: parseSpectrumBackgroundMode(value.backgroundMode, 'static-structured'),
    presetId: value.presetId === 'scattered' ? 'scattered' : null,
    presetName: optionalString(value, 'presetName') ?? null,
    compositionRevision,
    previewSection: value.previewSection === 'intro' || value.previewSection === 'main' || value.previewSection === 'outro'
      ? value.previewSection
      : null,
    generativePreviewOverride: parseSpectrumGenerativePreviewOverride(value.generativePreviewOverride),
    designHash: optionalString(value, 'designHash') ?? null,
    timingSource: value.timingSource === 'external-media-player-position' || value.timingSource === 'trackprompt-production-clock'
      ? value.timingSource : null,
    timingAccuracy: value.timingAccuracy === 'preview-level' || value.timingAccuracy === 'host-monotonic-process-boundary'
      ? value.timingAccuracy : null,
    timelineControllerVersion: optionalString(value, 'timelineControllerVersion') ?? null,
    visualQaRequired: booleanOr(value.visualQaRequired, true),
    masterTiming: parseSpectrumMasterTiming(value.masterTiming),
    productionAvailability: parseSpectrumProductionAvailability(value.productionAvailability),
    capturePreflight: parseCapturePreflight(value.capturePreflight),
    artifacts: parseArtifacts(value.artifacts),
    synchronization: parseSynchronization(value.synchronization),
    validationReport: parseValidationReport(value.validationReport),
    captureProvider: optionalString(value, 'captureProvider') ?? null,
    encoder: optionalString(value, 'encoder') ?? null,
    capturedFrames: nullableNumber(value.capturedFrames),
    droppedFrames: nullableNumber(value.droppedFrames),
    captureDurationSeconds: nullableNumber(value.captureDurationSeconds),
    errorMessage: optionalString(value, 'errorMessage') ?? null,
    geometryCapability: parseSpectrumGeometryCapability(value.geometryCapability),
    geometryTelemetry: parseSpectrumGeometryTelemetry(value.geometryTelemetry),
  }
}

export async function getRendererDescriptor(rendererId: string): Promise<RendererDescriptor> {
  return parseRendererDescriptor(await requestJson(`/renderers/${encodeURIComponent(rendererId)}`))
}

export async function listRenderers(): Promise<RendererDescriptor[]> {
  const value = await requestJson('/renderers')
  if (!isRecord(value) || !Array.isArray(value.renderers)) {
    throw new ApiError('The server returned an invalid renderer registry.', 'invalid_response')
  }
  return value.renderers.map(parseRendererDescriptor)
}

export async function prepareWzhkSpectrumWorkspace(
  options: SpectrumWorkspacePrepareOptions,
): Promise<SpectrumWorkspaceJob> {
  const body: Record<string, unknown> = {
    contractId: 'scattered',
    presetId: 'scattered',
    mode: options.mode,
    backgroundMode: options.backgroundMode,
    previewSection: options.previewSection,
    visualOverrides: options.visualOverrides,
  }
  if (options.mode === 'preview'
      && options.backgroundMode === 'generative-geometry'
      && options.generativePreview !== undefined) {
    body.generativePreview = options.generativePreview
  }
  return parseSpectrumWorkspaceJob(await requestJson('/renderers/wzhk-spectrum/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }))
}

export async function getWzhkSpectrumWorkspace(jobId: string): Promise<SpectrumWorkspaceJob> {
  return parseSpectrumWorkspaceJob(await requestJson(`/renderers/wzhk-spectrum/jobs/${encodeURIComponent(jobId)}`))
}

export async function preflightWzhkSpectrumCapture(jobId: string): Promise<SpectrumWorkspaceJob> {
  return parseSpectrumWorkspaceJob(await requestJson(`/renderers/wzhk-spectrum/jobs/${encodeURIComponent(jobId)}/capture-preflight`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh: true }),
  }))
}

export async function startWzhkSpectrumProduction(jobId: string): Promise<SpectrumWorkspaceJob> {
  return parseSpectrumWorkspaceJob(await requestJson(`/renderers/wzhk-spectrum/jobs/${encodeURIComponent(jobId)}/production`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      operatorConfirmed: true,
      confirmationPhrase: 'START WZHK SCATTERED CAPTURE',
    }),
  }))
}

export async function cancelWzhkSpectrumProduction(jobId: string): Promise<SpectrumWorkspaceJob> {
  return parseSpectrumWorkspaceJob(await requestJson(`/renderers/wzhk-spectrum/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: 'Operator cancelled Spectrum production from TrackPrompt Studio.' }),
  }))
}
