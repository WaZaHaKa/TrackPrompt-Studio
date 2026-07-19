import {
  DEFAULT_CAPABILITIES,
  type AnalysisEvent,
  type AnalysisGroup,
  type AnalysisJob,
  type AnalysisMode,
  type AnalysisOptions,
  type AnalysisResult,
  type AnalysisSection,
  type AnalysisStage,
  type Capabilities,
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
  type LyricsPatch,
  type LyricsSegmentQualityDecision,
  type TrackPromptVisualCueSheet,
  type VisualCueCurve,
  type VisualCueEvent,
  type VisualCuePreferences,
  type VisualCueTransition,
  isRecord,
} from './types'

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
      jobTtlMinutes: numberOr(limits.jobTtlMinutes, DEFAULT_CAPABILITIES.limits.jobTtlMinutes),
      maxPendingJobs: numberOr(limits.maxPendingJobs, DEFAULT_CAPABILITIES.limits.maxPendingJobs),
    },
    visualCueExportAvailable: booleanOr(value.visualCueExportAvailable, false),
    visualCueSheetSchemaVersion: optionalString(value, 'visualCueSheetSchemaVersion') ?? 'unknown',
    visualFeatureArtifactSchemaVersion: optionalString(value, 'visualFeatureArtifactSchemaVersion') ?? 'unknown',
    blenderVisualizerPreset: optionalString(value, 'blenderVisualizerPreset') ?? '',
    networkFeaturesEnabled: booleanOr(value.networkFeaturesEnabled, false),
  }
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
