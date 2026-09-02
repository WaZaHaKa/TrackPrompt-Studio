import type {
  AuthorizationResult,
  AuthorizationReview,
  CalibrationCandidate,
  CalibrationSummary,
  CheckStatus,
  CloudPackageResult,
  CloudReadiness,
  DryRunResult,
  DirectorAct,
  DirectorAssessment,
  DirectorDecision,
  DirectorReview,
  DirectorShot,
  DirectorWorkspace,
  EtaEstimate,
  EncodeCandidate,
  EncodeJob,
  FolderSelection,
  LogEntry,
  MissionCapabilities,
  MissionControlClient,
  MissionSettings,
  OutputClassification,
  OutputInspection,
  PerformanceStatus,
  PreflightCheck,
  PreflightResult,
  ProjectSummary,
  RenderEvent,
  RenderJob,
  RenderMetrics,
  RenderPhase,
  RenderProfileSummary,
  RenderSelection,
  RenderState,
  SceneSummary,
  StageProgress,
  StartRenderRequest,
  StructuredError,
  SystemPaths,
  SystemStatus,
  OutputVariantProgress,
  WorkerProgress,
} from './types'

export const MISSION_CONTROL_API_BASE =
  import.meta.env.VITE_MISSION_CONTROL_API_BASE_URL?.replace(/\/$/, '') ?? '/api/mission-control'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {}
}

function first(record: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) {
    if (record[key] !== undefined) return record[key]
  }
  return undefined
}

function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function nullableString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function booleanValue(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function records(value: unknown, ...containerKeys: string[]): Record<string, unknown>[] {
  const candidate = Array.isArray(value)
    ? value
    : first(asRecord(value), 'items', 'results', ...containerKeys)
  return Array.isArray(candidate) ? candidate.filter(isRecord) : []
}

function normalizeToken(value: unknown): string {
  return stringValue(value, 'unknown').trim().toLowerCase().replace(/[\s-]+/g, '_')
}

const renderStates = new Set<RenderState>([
  'idle', 'validating', 'authorization_required', 'ready', 'starting', 'running',
  'stop_requested', 'retry_requested', 'cancel_requested', 'finishing_current_chunk', 'paused_safely', 'resumable',
  'encoding', 'verifying', 'complete', 'failed', 'cancelled',
])

function renderState(value: unknown): RenderState {
  const normalized = normalizeToken(value)
  return renderStates.has(normalized as RenderState) ? normalized as RenderState : 'idle'
}

const renderPhases = new Set<RenderPhase>([
  'scene_load', 'render_frame', 'write_frame', 'validate_frame', 'validate_chunk',
  'publish_chunk', 'waiting_for_storage', 'encode_master', 'encode_delivery',
  'mux_audio', 'final_verify', 'idle',
])

function renderPhase(value: unknown): RenderPhase {
  const normalized = normalizeToken(value)
  return renderPhases.has(normalized as RenderPhase) ? normalized as RenderPhase : 'idle'
}

function capabilities(value: unknown): MissionCapabilities {
  const item = asRecord(value)
  const eventTransport = normalizeToken(first(item, 'realtime_events', 'realtimeEvents', 'event_transport'))
  return {
    nativeFolderPicker: booleanValue(first(item, 'native_folder_picker', 'nativeFolderPicker')),
    realtimeEvents: eventTransport === 'sse' || eventTransport === 'websocket' || eventTransport === 'polling'
      ? eventTransport
      : 'unavailable',
    renderExecution: booleanValue(first(item, 'render_execution', 'renderExecution')),
    encode: booleanValue(item.encode),
    performanceMode: booleanValue(first(item, 'performance_mode', 'performanceMode')),
    cloudPreparation: booleanValue(first(item, 'cloud_preparation', 'cloudPreparation')),
    cloudLive: booleanValue(first(item, 'cloud_live', 'cloudLive')),
    demoMode: booleanValue(first(item, 'demo_mode', 'demoMode')),
  }
}

export function parseSystemStatus(value: unknown): SystemStatus {
  const item = asRecord(value)
  const components = records(item.components)
  const component = (id: string): Record<string, unknown> | undefined => components.find((entry) => entry.id === id)
  const blender = component('blender')
  const powershell = component('powershell')
  const ffmpeg = component('ffmpeg')
  const ready = normalizeToken(item.status) === 'ready'
  return {
    serviceName: stringValue(first(item, 'service_name', 'serviceName'), 'WZHK Media Mission Control'),
    version: stringValue(item.version, 'unknown'),
    ready,
    instanceId: nullableString(first(item, 'instance_id', 'instanceId')),
    startedAt: nullableString(first(item, 'started_at', 'startedAt')),
    machineName: nullableString(first(item, 'machine_name', 'machineName')),
    blenderReady: normalizeToken(blender?.status) === 'pass',
    ffmpegReady: normalizeToken(ffmpeg?.status) === 'pass',
    rendererBusy: booleanValue(first(item, 'renderer_busy', 'rendererBusy')),
    activeJobId: nullableString(first(item, 'active_job_id', 'activeJobId', 'current_job_id', 'currentJobId')),
    capabilities: {
      ...capabilities(item.capabilities),
      nativeFolderPicker: true,
      realtimeEvents: 'sse',
      renderExecution: ready && normalizeToken(blender?.status) === 'pass' && normalizeToken(powershell?.status) === 'pass',
    },
    warnings: [
      ...strings(item.warnings),
      ...components.filter((entry) => normalizeToken(entry.status) !== 'pass').map((entry) => stringValue(entry.detail)).filter(Boolean),
    ],
  }
}

export function parseSystemPaths(value: unknown): SystemPaths {
  const item = asRecord(value)
  return {
    blenderPath: nullableString(first(item, 'blender_path', 'blenderPath')),
    ffmpegPath: nullableString(first(item, 'ffmpeg_path', 'ffmpegPath')),
    profileRoot: nullableString(first(item, 'profile_root', 'profileRoot')),
    outputDefault: nullableString(first(item, 'output_default', 'outputDefault', 'default_output_root', 'defaultOutputRoot')),
    calibrationRoot: nullableString(first(item, 'calibration_root', 'calibrationRoot')),
    preferredDrive: nullableString(first(item, 'preferred_drive', 'preferredDrive')),
  }
}

function parseProject(value: unknown): ProjectSummary | null {
  const item = asRecord(value)
  const id = nullableString(first(item, 'id', 'project_id', 'projectId'))
  if (!id) return null
  return {
    id,
    displayName: stringValue(first(item, 'display_name', 'displayName', 'name'), id),
    description: nullableString(item.description),
    recommendedSceneId: nullableString(first(item, 'recommended_scene_id', 'recommendedSceneId')),
    recommendedProfileId: nullableString(first(item, 'recommended_profile_id', 'recommendedProfileId')),
    current: booleanValue(first(item, 'current', 'is_current', 'isCurrent')),
    thumbnailUrl: nullableString(first(item, 'thumbnail_url', 'thumbnailUrl')),
  }
}

function parseScene(value: unknown): SceneSummary | null {
  const item = asRecord(value)
  const id = nullableString(first(item, 'id', 'scene_id', 'sceneId'))
  if (!id) return null
  const status = booleanValue(item.verified) ? 'verified' : normalizeToken(item.status)
  const frameStart = numberValue(first(item, 'frame_start', 'frameStart'), 1)
  const frameEnd = numberValue(first(item, 'frame_end', 'frameEnd'))
  return {
    id,
    projectId: stringValue(first(item, 'project_id', 'projectId')),
    displayName: stringValue(first(item, 'display_name', 'displayName', 'name'), id),
    approved: booleanValue(item.approved, booleanValue(item.verified)),
    status: status === 'verified' || status === 'changed' || status === 'missing' ? status : 'unknown',
    sha256: nullableString(first(item, 'sha256', 'scene_sha256', 'sceneSha256')),
    path: nullableString(item.path),
    thumbnailUrl: nullableString(first(item, 'thumbnail_url', 'thumbnailUrl')),
    frameStart,
    frameEnd,
    totalFrames: numberValue(first(item, 'total_frames', 'totalFrames'), Math.max(0, frameEnd - frameStart + 1)),
    fps: numberValue(item.fps, 30),
  }
}

function parseProfile(value: unknown): RenderProfileSummary | null {
  const item = asRecord(value)
  const id = nullableString(first(item, 'id', 'profile_id', 'profileId'))
  if (!id) return null
  const resolution = asRecord(item.resolution)
  const rawAuthorization = normalizeToken(first(item, 'authorization_status', 'authorizationStatus'))
  const authorization = booleanValue(item.authorized)
    ? 'authorized'
    : rawAuthorization.includes('required') || rawAuthorization.includes('unauthorized') || rawAuthorization.includes('missing')
      ? 'required'
      : rawAuthorization.includes('invalid') || rawAuthorization.includes('changed')
        ? 'invalid'
        : rawAuthorization
  const outputVariants = records(first(item, 'output_variants', 'outputVariants'))
    .map((variant) => {
      const variantId = nullableString(first(variant, 'id', 'output_variant_id', 'outputVariantId'))
      if (!variantId) return null
      return {
        id: variantId,
        enabledByDefault: booleanValue(first(variant, 'enabled_by_default', 'enabledByDefault')),
        required: booleanValue(variant.required),
        width: numberValue(variant.width),
        height: numberValue(variant.height),
        fps: numberValue(variant.fps, numberValue(item.fps, 30)),
        deliverableRole: stringValue(first(variant, 'deliverable_role', 'deliverableRole'), 'optional-deliverable'),
        compositionProfileId: stringValue(first(variant, 'composition_profile_id', 'compositionProfileId'), `${variantId}-composition`),
      }
    })
    .filter((variant): variant is NonNullable<typeof variant> => variant !== null)
  return {
    id,
    projectId: stringValue(first(item, 'project_id', 'projectId', 'project'), ''),
    sceneId: stringValue(first(item, 'scene_id', 'sceneId'), ''),
    displayName: stringValue(first(item, 'display_name', 'displayName', 'name'), id),
    width: numberValue(first(item, 'width', 'resolution_width', 'resolutionWidth'), numberValue(resolution.width)),
    height: numberValue(first(item, 'height', 'resolution_height', 'resolutionHeight'), numberValue(resolution.height)),
    fps: numberValue(item.fps, 30),
    expectedSeconds: nullableNumber(first(item, 'expected_seconds', 'expectedSeconds'))
      ?? (nullableNumber(first(item, 'expected_hours', 'expectedHours')) === null ? null : numberValue(first(item, 'expected_hours', 'expectedHours')) * 3600),
    conservativeSeconds: nullableNumber(first(item, 'conservative_seconds', 'conservativeSeconds'))
      ?? (nullableNumber(first(item, 'conservative_hours', 'conservativeHours')) === null ? null : numberValue(first(item, 'conservative_hours', 'conservativeHours')) * 3600),
    storageGiB: nullableNumber(first(item, 'storage_gib', 'storageGiB', 'planned_frame_sequence_gib', 'plannedFrameSequenceGib')),
    minimumFreeGiB: nullableNumber(first(item, 'minimum_free_gib', 'minimumFreeGiB', 'minimum_launch_free_gib', 'minimumLaunchFreeGib')),
    qualityRole: stringValue(first(item, 'quality_role', 'qualityRole', 'role'), 'Render profile'),
    qualityDescription: nullableString(first(item, 'quality_description', 'qualityDescription', 'description', 'quality_verdict', 'qualityVerdict')),
    calibrated: booleanValue(item.calibrated),
    authorizationStatus: authorization === 'authorized' || authorization === 'required' || authorization === 'invalid'
      ? authorization
      : 'unknown',
    recommended: booleanValue(first(item, 'recommended', 'is_recommended', 'isRecommended')),
    localRecommendation: nullableString(first(item, 'local_recommendation', 'localRecommendation')),
    lastUsedAt: nullableString(first(item, 'last_used_at', 'lastUsedAt')),
    savedFileSha256: nullableString(first(item, 'saved_file_sha256', 'savedFileSha256', 'profile_sha256', 'profileSha256')),
    path: nullableString(item.path),
    outputVariants,
  }
}

function outputClassification(value: unknown): OutputClassification {
  const normalized = normalizeToken(value)
  const aliases: Record<string, OutputClassification> = {
    new_output: 'empty',
    empty_directory: 'empty',
    compatible_resumable: 'compatible_resume',
    contains_unrelated_files: 'unrelated_files',
    contains_hidden_system_entries: 'hidden_entries',
    not_a_directory: 'missing',
  }
  if (aliases[normalized]) return aliases[normalized]
  const valid = new Set<OutputClassification>([
    'empty', 'compatible_resume', 'incompatible_render', 'unrelated_files',
    'hidden_entries', 'parent_suitable', 'missing', 'unknown',
  ])
  return valid.has(normalized as OutputClassification) ? normalized as OutputClassification : 'unknown'
}

export function parseOutputInspection(value: unknown): OutputInspection {
  const item = asRecord(value)
  const nestedInspection = first(item, 'inspection')
  if (isRecord(nestedInspection)) {
    const parsed = parseOutputInspection(nestedInspection)
    return { ...parsed, path: stringValue(item.path, parsed.path) }
  }
  const conflict = asRecord(first(item, 'conflicting_identity', 'conflictingIdentity', 'existing_identity', 'existingIdentity'))
  const hasConflict = Object.keys(conflict).length > 0
  const entryNames = records(item.entries).map((entry) => stringValue(entry.name)).filter(Boolean)
  const conflictingEntries = strings(first(item, 'conflicting_entries', 'conflictingEntries'))
  const issues = strings(item.issues)
  const classification = outputClassification(item.classification)
  return {
    path: stringValue(item.path),
    classification,
    usable: booleanValue(item.usable),
    resumable: booleanValue(item.resumable, classification === 'compatible_resume'),
    entries: [...new Set([...conflictingEntries, ...entryNames])],
    message: stringValue(item.message, issues.join(' ') || 'The local service inspected this folder.'),
    suggestedChildName: nullableString(first(item, 'suggested_child_name', 'suggestedChildName')),
    conflictingIdentity: hasConflict ? {
      projectId: nullableString(first(conflict, 'project_id', 'projectId')),
      sceneId: nullableString(first(conflict, 'scene_id', 'sceneId')),
      profileId: nullableString(first(conflict, 'profile_id', 'profileId')),
      sceneSha256: nullableString(first(conflict, 'scene_sha256', 'sceneSha256')),
      profileSha256: nullableString(first(conflict, 'profile_sha256', 'profileSha256')),
    } : null,
  }
}

function checkStatus(value: unknown): CheckStatus {
  const normalized = normalizeToken(value)
  return normalized === 'pass' || normalized === 'warning' || normalized === 'fail' || normalized === 'pending'
    ? normalized
    : 'pending'
}

function parsePreflightCheck(value: unknown, index: number): PreflightCheck {
  const item = asRecord(value)
  return {
    id: stringValue(item.id, `check-${index + 1}`),
    label: stringValue(first(item, 'label', 'name'), `Check ${index + 1}`),
    status: checkStatus(item.status),
    summary: stringValue(first(item, 'summary', 'message')),
    technicalDetails: nullableString(first(item, 'technical_details', 'technicalDetails', 'details', 'detail')),
  }
}

export function parsePreflight(value: unknown): PreflightResult {
  const item = asRecord(value)
  const identity = asRecord(item.identity)
  return {
    ready: booleanValue(item.ready),
    authorizationRequired: booleanValue(first(item, 'authorization_required', 'authorizationRequired')),
    checks: records(item.checks).map(parsePreflightCheck),
    sceneSha256: nullableString(first(item, 'scene_sha256', 'sceneSha256')) ?? nullableString(first(identity, 'scene_sha256', 'sceneSha256')),
    profileSha256: nullableString(first(item, 'profile_sha256', 'profileSha256')) ?? nullableString(first(identity, 'profile_sha256', 'profileSha256')),
    exactOperation: stringValue(first(item, 'exact_operation', 'exactOperation'), 'Full production render'),
    rawDetails: first(item, 'raw_details', 'rawDetails', 'details', 'raw_engine_result', 'rawEngineResult') ?? null,
  }
}

export function parseStructuredError(value: unknown, fallbackTitle = 'Action could not be completed'): StructuredError {
  const outer = asRecord(value)
  const item = isRecord(outer.error) ? outer.error : isRecord(outer.detail) ? outer.detail : outer
  const message = stringValue(first(item, 'summary', 'message', 'detail'), fallbackTitle)
  return {
    code: stringValue(item.code, 'request_failed'),
    title: stringValue(item.title, fallbackTitle),
    summary: message,
    likelyCause: nullableString(first(item, 'likely_cause', 'likelyCause')),
    recommendedAction: nullableString(first(item, 'recommended_action', 'recommendedAction')),
    retryable: booleanValue(item.retryable),
    context: asRecord(item.context),
    technicalDetails: nullableString(first(item, 'technical_details', 'technicalDetails')),
    relatedPath: nullableString(first(item, 'related_path', 'relatedPath')),
    timestamp: stringValue(item.timestamp, new Date().toISOString()),
    jobId: nullableString(first(item, 'job_id', 'jobId')),
  }
}

function metrics(value: unknown, event: Record<string, unknown>): RenderMetrics {
  const item = asRecord(value)
  const pick = (...keys: string[]): unknown => first(item, ...keys) ?? first(event, ...keys)
  return {
    currentSecondsPerFrame: nullableNumber(pick('current_seconds_per_frame', 'currentSecondsPerFrame')),
    rollingMedianSeconds: nullableNumber(pick('rolling_median_seconds', 'rollingMedianSeconds', 'rolling_median')),
    rollingMeanSeconds: nullableNumber(pick('rolling_mean_seconds', 'rollingMeanSeconds', 'rolling_mean')),
    p90Seconds: nullableNumber(pick('p90_seconds', 'p90Seconds', 'p90')),
    currentStorageBytes: nullableNumber(pick('current_storage_bytes', 'currentStorageBytes', 'current_storage_used')),
    projectedStorageBytes: nullableNumber(pick('projected_storage_bytes', 'projectedStorageBytes', 'projected_storage')),
    freeStorageBytes: nullableNumber(pick('free_storage_bytes', 'freeStorageBytes', 'free_storage')),
    gpuUtilizationPercent: nullableNumber(pick('gpu_utilization_percent', 'gpuUtilizationPercent', 'gpu_utilization')),
    vramUsedBytes: nullableNumber(pick('vram_used_bytes', 'vramUsedBytes', 'vram_use'))
      ?? (nullableNumber(pick('vram_used_mib', 'vramUsedMib')) === null ? null : numberValue(pick('vram_used_mib', 'vramUsedMib')) * 1024 * 1024),
    gpuTemperatureC: nullableNumber(pick('gpu_temperature_c', 'gpuTemperatureC', 'gpu_temperature')),
    cpuUtilizationPercent: nullableNumber(pick('cpu_utilization_percent', 'cpuUtilizationPercent', 'cpu_use')),
    ramUsedBytes: nullableNumber(pick('ram_used_bytes', 'ramUsedBytes', 'ram_use'))
      ?? (nullableNumber(pick('ram_used_mib', 'ramUsedMib')) === null ? null : numberValue(pick('ram_used_mib', 'ramUsedMib')) * 1024 * 1024),
  }
}

function confidence(value: unknown): 'low' | 'medium' | 'high' | 'unknown' {
  const normalized = normalizeToken(value)
  return normalized === 'low' || normalized === 'medium' || normalized === 'high' ? normalized : 'unknown'
}

function optionalRenderState(value: unknown): RenderState | null {
  if (value === undefined || value === null || value === '') return null
  const normalized = normalizeToken(value)
  return renderStates.has(normalized as RenderState) ? normalized as RenderState : null
}

function optionalRenderPhase(value: unknown): RenderPhase | null {
  if (value === undefined || value === null || value === '') return null
  const normalized = normalizeToken(value)
  return renderPhases.has(normalized as RenderPhase) ? normalized as RenderPhase : null
}

function publicArtifactUrl(value: unknown): string | null {
  const url = nullableString(value)
  return url && (/^https?:\/\//i.test(url) || url.startsWith('/api/')) ? url : null
}

export function parseEtaEstimate(value: unknown): EtaEstimate | null {
  if (!isRecord(value)) return null
  const item = value
  const bounds = asRecord(first(item, 'bounds', 'confidence_bounds', 'confidenceBounds'))
  const pick = (...keys: string[]): unknown => first(item, ...keys) ?? first(bounds, ...keys)
  const rawState = normalizeToken(first(item, 'state', 'estimate_state', 'estimateState'))
  const rawFreshness = normalizeToken(first(item, 'freshness', 'estimate_freshness', 'estimateFreshness'))
  const state = rawState === 'calibrating' || rawState === 'stable' || rawState === 'degraded' || rawState === 'unavailable'
    ? rawState
    : 'unavailable'
  const freshness = rawFreshness === 'fresh' || rawFreshness === 'stale'
    ? rawFreshness
    : typeof item.stale === 'boolean'
      ? item.stale ? 'stale' : 'fresh'
      : 'unknown'
  const estimate: EtaEstimate = {
    state,
    p50Seconds: nullableNumber(pick(
      'p50_seconds',
      'p50Seconds',
      'p50_seconds_remaining',
      'p50SecondsRemaining',
      'p50_remaining_seconds',
      'p50RemainingSeconds',
      'median_seconds',
      'medianSeconds',
    )),
    p90Seconds: nullableNumber(pick(
      'p90_seconds',
      'p90Seconds',
      'p90_seconds_remaining',
      'p90SecondsRemaining',
      'p90_remaining_seconds',
      'p90RemainingSeconds',
      'conservative_seconds',
      'conservativeSeconds',
    )),
    p50CompletionAt: nullableString(pick(
      'p50_completion_at',
      'p50CompletionAt',
      'p50_finish_at',
      'p50FinishAt',
      'estimated_completion_at',
      'estimatedCompletionAt',
    )),
    p90CompletionAt: nullableString(pick(
      'p90_completion_at',
      'p90CompletionAt',
      'p90_finish_at',
      'p90FinishAt',
      'conservative_completion_at',
      'conservativeCompletionAt',
    )),
    confidence: confidence(first(item, 'confidence', 'eta_confidence', 'etaConfidence')),
    freshness,
    lastEstimateAt: nullableString(first(
      item,
      'last_estimate_at',
      'lastEstimateAt',
      'calculated_at',
      'calculatedAt',
      'updated_at',
      'updatedAt',
    )),
    sampleCount: nullableNumber(first(item, 'sample_count', 'sampleCount', 'observations')),
  }
  return estimate.state !== 'unavailable'
    || estimate.p50Seconds !== null
    || estimate.p90Seconds !== null
    || estimate.p50CompletionAt !== null
    || estimate.p90CompletionAt !== null
    ? estimate
    : null
}

function parseWorker(value: unknown, index: number): WorkerProgress | null {
  if (typeof value === 'string' && value.length > 0) {
    return {
      id: value,
      status: 'active',
      active: true,
      currentTaskId: null,
      currentFrame: null,
      retryCount: 0,
      failureCount: 0,
      lastHeartbeatAt: null,
    }
  }
  if (!isRecord(value)) return null
  const status = normalizeToken(first(value, 'status', 'state'))
  const id = stringValue(first(value, 'id', 'worker_id', 'workerId'), `worker-${index + 1}`)
  return {
    id,
    status,
    active: typeof value.active === 'boolean'
      ? value.active
      : status !== 'failed' && status !== 'lost' && status !== 'offline' && status !== 'stopped',
    currentTaskId: nullableString(first(value, 'current_task_id', 'currentTaskId', 'task_id', 'taskId')),
    currentFrame: nullableNumber(first(value, 'current_frame', 'currentFrame', 'frame')),
    retryCount: numberValue(first(value, 'retry_count', 'retryCount', 'retries')),
    failureCount: numberValue(first(value, 'failure_count', 'failureCount', 'failures')),
    lastHeartbeatAt: nullableString(first(value, 'last_heartbeat_at', 'lastHeartbeatAt', 'heartbeat_at', 'heartbeatAt')),
  }
}

function parseWorkers(value: unknown): WorkerProgress[] {
  const candidate = Array.isArray(value)
    ? value
    : first(asRecord(value), 'workers', 'items', 'active_workers', 'activeWorkers')
  return Array.isArray(candidate)
    ? candidate.map(parseWorker).filter((worker): worker is WorkerProgress => worker !== null)
    : []
}

function stageState(value: unknown): StageProgress['state'] {
  const normalized = normalizeToken(value)
  return normalized === 'pending'
    || normalized === 'calibrating'
    || normalized === 'running'
    || normalized === 'paused'
    || normalized === 'complete'
    || normalized === 'failed'
    || normalized === 'cancelled'
    || normalized === 'skipped'
    || normalized === 'indeterminate'
    ? normalized
    : 'unknown'
}

function parseStageProgress(value: unknown, index: number): StageProgress | null {
  if (!isRecord(value)) return null
  const units = asRecord(first(value, 'units', 'work'))
  const rawProgress = nullableNumber(first(value, 'progress', 'progress_ratio', 'progressRatio', 'percent_complete', 'percentComplete'))
  const progress = rawProgress === null ? null : Math.max(0, Math.min(1, rawProgress > 1 ? rawProgress / 100 : rawProgress))
  const id = stringValue(first(value, 'id', 'stage_id', 'stageId', 'stage'), `stage-${index + 1}`)
  const directEta = {
    state: first(value, 'estimate_state', 'estimateState'),
    confidence: first(value, 'eta_confidence', 'etaConfidence', 'confidence'),
    p50RemainingSeconds: first(value, 'eta_p50_seconds', 'etaP50Seconds', 'p50_remaining_seconds', 'p50RemainingSeconds'),
    p90RemainingSeconds: first(value, 'eta_p90_seconds', 'etaP90Seconds', 'p90_remaining_seconds', 'p90RemainingSeconds'),
    lastEstimateAt: first(value, 'last_estimate_at', 'lastEstimateAt', 'updated_at', 'updatedAt'),
  }
  return {
    id,
    label: stringValue(first(value, 'label', 'display_name', 'displayName', 'name'), id),
    state: stageState(first(value, 'state', 'status')),
    completedUnits: nullableNumber(first(value, 'completed_units', 'completedUnits', 'completed'))
      ?? nullableNumber(first(units, 'completed', 'done')),
    totalUnits: nullableNumber(first(value, 'total_units', 'totalUnits', 'total'))
      ?? nullableNumber(first(units, 'total')),
    progress,
    throughput: nullableNumber(first(value, 'throughput', 'throughput_per_second', 'throughputPerSecond', 'units_per_second', 'unitsPerSecond')),
    throughputUnit: nullableString(first(value, 'throughput_unit', 'throughputUnit', 'unit')),
    elapsedSeconds: nullableNumber(first(value, 'elapsed_seconds', 'elapsedSeconds')),
    startedAt: nullableString(first(value, 'started_at', 'startedAt')),
    updatedAt: nullableString(first(value, 'updated_at', 'updatedAt')),
    eta: parseEtaEstimate(first(value, 'eta', 'eta_estimate', 'etaEstimate')) ?? parseEtaEstimate(directEta),
  }
}

function parseStages(value: unknown): StageProgress[] {
  const candidate = Array.isArray(value)
    ? value
    : first(asRecord(value), 'stages', 'items', 'stage_progress', 'stageProgress')
  return Array.isArray(candidate)
    ? candidate.map(parseStageProgress).filter((stage): stage is StageProgress => stage !== null)
    : []
}

export function parseOutputVariantProgress(value: unknown, index = 0): OutputVariantProgress | null {
  if (!isRecord(value)) return null
  const item = value
  const identity = asRecord(first(item, 'identity', 'variant_identity', 'variantIdentity', 'output_variant', 'outputVariant'))
  const dimensions = asRecord(first(item, 'dimensions', 'resolution'))
  const composition = asRecord(first(item, 'composition_profile', 'compositionProfile', 'composition'))
  const profile = asRecord(first(item, 'render_profile', 'renderProfile', 'profile'))
  const progress = asRecord(first(item, 'progress', 'frame_progress', 'frameProgress'))
  const artifacts = asRecord(first(item, 'artifacts', 'latest_artifacts', 'latestArtifacts'))
  const preview = asRecord(
    first(artifacts, 'preview', 'latest_preview', 'latestPreview')
      ?? first(progress, 'preview', 'latest_preview', 'latestPreview'),
  )
  const fullFrame = asRecord(first(artifacts, 'frame', 'full_frame', 'fullFrame', 'latest_frame', 'latestFrame'))
  const pick = (...keys: string[]): unknown => first(item, ...keys) ?? first(progress, ...keys)
  const pickIdentity = (...keys: string[]): unknown => first(item, ...keys) ?? first(identity, ...keys)
  const previewReference = first(
    item,
    'preview_url',
    'previewUrl',
    'latest_frame_preview_url',
    'latestFramePreviewUrl',
  ) ?? first(progress, 'preview_url', 'previewUrl')
    ?? first(preview, 'url', 'href', 'preview_url', 'previewUrl')
  const fullFrameReference = first(
    item,
    'full_frame_url',
    'fullFrameUrl',
    'latest_full_frame_url',
    'latestFullFrameUrl',
  ) ?? first(progress, 'full_frame_url', 'fullFrameUrl')
    ?? first(fullFrame, 'url', 'href', 'full_frame_url', 'fullFrameUrl')
  const previewMatch = nullableString(previewReference)?.match(/frame[_-]?(\d{1,9})\.png/i)
  const id = stringValue(
    pickIdentity('id', 'variant_id', 'variantId', 'output_variant_id', 'outputVariantId'),
    `variant-${index + 1}`,
  )
  const width = numberValue(first(item, 'width') ?? first(dimensions, 'width'))
  const height = numberValue(first(item, 'height') ?? first(dimensions, 'height'))
  const variantStages = parseStages(first(item, 'stages', 'stage_progress', 'stageProgress') ?? first(progress, 'stages', 'stage_progress', 'stageProgress'))
  const explicitState = optionalRenderState(first(item, 'state', 'status') ?? first(progress, 'state', 'status'))
  const inferredState: RenderState | null = variantStages.some((stage) => stage.state === 'failed')
    ? 'failed'
    : variantStages.length > 0 && variantStages.every((stage) => stage.state === 'complete' || stage.state === 'skipped')
      ? 'complete'
      : variantStages.some((stage) => stage.state === 'running')
        ? 'running'
        : variantStages.some((stage) => stage.state === 'paused')
          ? 'paused_safely'
          : null
  return {
    id,
    displayName: stringValue(first(item, 'display_name', 'displayName', 'label', 'name'), id),
    enabled: booleanValue(first(item, 'enabled'), true),
    required: booleanValue(first(item, 'required')),
    width,
    height,
    fps: nullableNumber(first(item, 'fps') ?? first(dimensions, 'fps')),
    aspectRatio: nullableString(first(item, 'aspect_ratio', 'aspectRatio') ?? first(dimensions, 'aspect_ratio', 'aspectRatio')),
    deliverableRole: nullableString(first(item, 'deliverable_role', 'deliverableRole', 'role')),
    compositionMode: nullableString(first(item, 'composition_mode', 'compositionMode') ?? first(composition, 'mode')),
    compositionProfileId: nullableString(first(
      item,
      'composition_profile_id',
      'compositionProfileId',
    )) ?? nullableString(first(composition, 'id')),
    compositionProfileSha256: nullableString(first(
      item,
      'composition_profile_sha256',
      'compositionProfileSha256',
      'composition_sha256',
      'compositionSha256',
    )) ?? nullableString(first(composition, 'sha256', 'composition_sha256', 'compositionSha256')),
    profileId: nullableString(first(item, 'profile_id', 'profileId', 'render_profile_id', 'renderProfileId'))
      ?? nullableString(first(profile, 'id'))
      ?? nullableString(first(identity, 'profile_id', 'profileId')),
    profileSha256: nullableString(first(item, 'profile_sha256', 'profileSha256', 'render_profile_sha256', 'renderProfileSha256'))
      ?? nullableString(first(profile, 'sha256'))
      ?? nullableString(first(identity, 'profile_sha256', 'profileSha256')),
    outputVariantSha256: nullableString(first(item, 'output_variant_sha256', 'outputVariantSha256')),
    state: explicitState ?? inferredState,
    phase: optionalRenderPhase(first(item, 'phase', 'stage') ?? first(progress, 'phase', 'stage')),
    frameStart: nullableNumber(pick('frame_start', 'frameStart')),
    frameEnd: nullableNumber(pick('frame_end', 'frameEnd')),
    currentFrame: nullableNumber(pick('current_frame', 'currentFrame')),
    currentFrameStartedAt: nullableString(pick('current_frame_started_at', 'currentFrameStartedAt')),
    lastOutputAt: nullableString(pick('last_output_at', 'lastOutputAt')),
    latestRenderedFrame: nullableNumber(pick(
      'latest_rendered_frame',
      'latestRenderedFrame',
      'latest_rendered_frame_index',
      'latestRenderedFrameIndex',
    )),
    latestSafeFrame: nullableNumber(pick(
      'latest_safe_frame',
      'latestSafeFrame',
      'latest_safe_frame_index',
      'latestSafeFrameIndex',
      'latest_published_frame',
      'latestPublishedFrame',
    )),
    renderedFrames: numberValue(pick('rendered_frames', 'renderedFrames', 'rendered_frame_count', 'renderedFrameCount')),
    inFlightFrames: (() => {
      const raw = pick('in_flight_frames', 'inFlightFrames', 'inflight_frames', 'inflightFrames', 'inflight_frame_count', 'inflightFrameCount')
      return Array.isArray(raw) ? raw.length : numberValue(raw)
    })(),
    validatedFrames: numberValue(pick('validated_frames', 'validatedFrames', 'validated_frame_count', 'validatedFrameCount')),
    publishedFrames: numberValue(
      pick('published_frames', 'publishedFrames', 'published_frame_count', 'publishedFrameCount', 'safe_frames', 'safeFrames'),
      numberValue(pick('validated_frames', 'validatedFrames', 'validated_frame_count', 'validatedFrameCount')),
    ),
    totalFrames: numberValue(pick('total_frames', 'totalFrames', 'total_frame_count', 'totalFrameCount')),
    activeChunkId: nullableString(pick('active_chunk_id', 'activeChunkId', 'chunk_id', 'chunkId')),
    chunkStart: nullableNumber(pick('chunk_start', 'chunkStart')),
    chunkEnd: nullableNumber(pick('chunk_end', 'chunkEnd')),
    currentChunkProgress: nullableNumber(pick('current_chunk_progress', 'currentChunkProgress')),
    chunksCompleted: numberValue(pick('chunks_completed', 'chunksCompleted')),
    chunksTotal: numberValue(pick('chunks_total', 'chunksTotal')),
    previewUrl: publicArtifactUrl(previewReference),
    fullFrameUrl: publicArtifactUrl(fullFrameReference),
    previewFrame: nullableNumber(first(item, 'preview_frame', 'previewFrame') ?? first(preview, 'frame'))
      ?? (previewMatch?.[1] ? Number.parseInt(previewMatch[1], 10) : null),
    latestPreviewAt: nullableString(first(item, 'latest_preview_at', 'latestPreviewAt') ?? first(preview, 'created_at', 'createdAt', 'timestamp')),
    workers: parseWorkers(
      first(item, 'workers', 'active_workers', 'activeWorkers')
        ?? first(progress, 'workers', 'active_worker_ids', 'activeWorkerIds'),
    ),
    retryCount: numberValue(pick('retry_count', 'retryCount', 'retries')),
    failureCount: numberValue(pick('failure_count', 'failureCount', 'failures')),
    stages: variantStages,
    eta: parseEtaEstimate(first(item, 'eta', 'eta_estimate', 'etaEstimate', 'render_eta', 'renderEta')),
  }
}

function parseOutputVariants(value: unknown): OutputVariantProgress[] {
  const candidate = Array.isArray(value)
    ? value
    : first(asRecord(value), 'variants', 'items', 'output_variants', 'outputVariants')
  return Array.isArray(candidate)
    ? candidate.map(parseOutputVariantProgress).filter((variant): variant is OutputVariantProgress => variant !== null)
    : []
}

export function parseRenderEvent(value: unknown): RenderEvent {
  const item = asRecord(value)
  const identity = asRecord(item.identity)
  const rawError = first(item, 'error', 'structured_error', 'structuredError')
  const safeStop = normalizeToken(first(item, 'safe_stop_status', 'safeStopStatus'))
  const previewReference = nullableString(first(item, 'preview_url', 'previewUrl', 'latest_frame_preview_reference', 'latest_frame_preview', 'latestFramePreview'))
  const previewMatch = previewReference?.match(/frame[_-]?(\d{1,9})\.png/i)
  const previewUrl = publicArtifactUrl(previewReference)
  const etaForecast = asRecord(first(
    item,
    'eta_forecast',
    'etaForecast',
    'matrix_forecast',
    'matrixForecast',
    'forecast',
  ))
  const variantForecasts = records(first(etaForecast, 'variant_forecasts', 'variantForecasts'))
  const variantEta = new Map<string, EtaEstimate>()
  variantForecasts.forEach((forecast) => {
    const id = nullableString(first(forecast, 'output_variant_id', 'outputVariantId', 'variant_id', 'variantId', 'id'))
    const estimate = parseEtaEstimate(forecast)
    if (id && estimate) variantEta.set(id, estimate)
  })
  const outputVariants = parseOutputVariants(first(
    item,
    'output_variants',
    'outputVariants',
    'output_variant_progress',
    'outputVariantProgress',
    'variants',
  )).map((variant) => ({ ...variant, eta: variant.eta ?? variantEta.get(variant.id) ?? null }))
  return {
    schemaVersion: stringValue(first(item, 'schema_version', 'schemaVersion'), '1'),
    sequence: numberValue(first(item, 'sequence', 'event_sequence', 'eventSequence')),
    timestamp: stringValue(item.timestamp, new Date().toISOString()),
    jobId: stringValue(first(item, 'job_id', 'jobId', 'id')),
    projectId: nullableString(first(item, 'project_id', 'projectId')) ?? nullableString(first(identity, 'project_id', 'projectId')),
    state: renderState(item.state),
    phase: renderPhase(item.phase),
    sceneId: nullableString(first(item, 'scene_id', 'sceneId')) ?? nullableString(first(identity, 'scene_id', 'sceneId')),
    sceneSha256: nullableString(first(item, 'scene_sha256', 'sceneSha256')) ?? nullableString(first(identity, 'scene_sha256', 'sceneSha256')),
    profileId: nullableString(first(item, 'profile_id', 'profileId')) ?? nullableString(first(identity, 'profile_id', 'profileId')),
    profileSha256: nullableString(first(item, 'profile_sha256', 'profileSha256')) ?? nullableString(first(identity, 'profile_sha256', 'profileSha256')),
    frameStart: nullableNumber(first(item, 'frame_start', 'frameStart')),
    frameEnd: nullableNumber(first(item, 'frame_end', 'frameEnd')),
    currentFrame: nullableNumber(first(item, 'current_frame', 'currentFrame')),
    latestRenderedFrame: nullableNumber(first(item, 'latest_rendered_frame', 'latestRenderedFrame')),
    rendererEventType: nullableString(first(item, 'renderer_event_type', 'rendererEventType')),
    rendererEventSequence: nullableNumber(first(item, 'renderer_event_sequence', 'rendererEventSequence')),
    rendererStatus: nullableString(first(item, 'renderer_status', 'rendererStatus')),
    workerId: nullableString(first(item, 'worker_id', 'workerId')),
    activeChunkId: nullableString(first(item, 'active_chunk_id', 'activeChunkId')),
    currentActId: nullableString(first(item, 'current_act_id', 'currentActId')),
    currentActName: nullableString(first(item, 'current_act_name', 'currentActName')),
    currentShotId: nullableString(first(item, 'current_shot_id', 'currentShotId')),
    currentShotName: nullableString(first(item, 'current_shot_name', 'currentShotName')),
    lastCompletedFrame: nullableNumber(first(item, 'last_completed_frame', 'lastCompletedFrame')),
    renderedFrames: numberValue(first(item, 'rendered_frames', 'renderedFrames', 'rendered_frame_count', 'renderedFrameCount')),
    inFlightFrames: numberValue(first(item, 'in_flight_frames', 'inFlightFrames', 'in_flight_frame_count', 'inflight_frame_count', 'inflightFrameCount')),
    validatedFrames: numberValue(first(item, 'validated_frames', 'validatedFrames', 'validated_frame_count', 'validatedFrameCount')),
    publishedFrames: numberValue(first(item, 'published_frames', 'publishedFrames', 'published_frame_count', 'publishedFrameCount')),
    totalFrames: numberValue(first(item, 'total_frames', 'totalFrames', 'total_frame_count', 'totalFrameCount')),
    chunkStart: nullableNumber(first(item, 'chunk_start', 'chunkStart')),
    chunkEnd: nullableNumber(first(item, 'chunk_end', 'chunkEnd')),
    currentChunkProgress: nullableNumber(first(item, 'current_chunk_progress', 'currentChunkProgress')),
    chunksCompleted: numberValue(first(item, 'chunks_completed', 'chunksCompleted')),
    chunksTotal: numberValue(first(item, 'chunks_total', 'chunksTotal')),
    estimatedCompletionAt: nullableString(first(item, 'estimated_completion_at', 'estimatedCompletionAt', 'estimated_completion_time', 'estimatedCompletionTime')),
    etaConfidence: confidence(first(item, 'eta_confidence', 'etaConfidence')),
    metrics: metrics(item.metrics, item),
    previewUrl,
    fullFrameUrl: publicArtifactUrl(first(item, 'full_frame_url', 'fullFrameUrl', 'latest_full_frame_url', 'latestFullFrameUrl')),
    previewFrame: nullableNumber(first(item, 'preview_frame', 'previewFrame', 'latest_preview_frame', 'latestPreviewFrame'))
      ?? (previewMatch?.[1] ? Number.parseInt(previewMatch[1], 10) : null),
    latestPreviewAt: nullableString(first(item, 'latest_preview_at', 'latestPreviewAt')),
    latestLogLine: nullableString(first(item, 'latest_log_line', 'latestLogLine')),
    warning: nullableString(item.warning),
    error: rawError === null || rawError === undefined ? null : parseStructuredError(rawError, 'Render issue'),
    safeStopStatus: safeStop === 'finishing_current_chunk'
      ? 'finishing_chunk'
      : safeStop === 'none' || safeStop === 'requested' || safeStop === 'finishing_chunk' || safeStop === 'paused' || safeStop === 'cancelled'
        ? safeStop
        : 'unknown',
    rendererActive: typeof first(item, 'renderer_active', 'rendererActive') === 'boolean'
      ? booleanValue(first(item, 'renderer_active', 'rendererActive'))
      : null,
    watcherActive: typeof first(item, 'watcher_active', 'watcherActive') === 'boolean'
      ? booleanValue(first(item, 'watcher_active', 'watcherActive'))
      : null,
    currentFrameStartedAt: nullableString(first(item, 'current_frame_started_at', 'currentFrameStartedAt')),
    lastOutputAt: nullableString(first(item, 'last_output_at', 'lastOutputAt')),
    activeVariantId: nullableString(first(item, 'active_variant_id', 'activeVariantId', 'output_variant_id', 'outputVariantId')),
    outputVariants,
    stages: parseStages(first(item, 'stages', 'stage_progress', 'stageProgress')),
    eta: parseEtaEstimate(first(item, 'eta', 'eta_estimate', 'etaEstimate', 'render_eta', 'renderEta')),
    aggregateEta: parseEtaEstimate(first(item, 'aggregate_eta', 'aggregateEta', 'total_eta', 'totalEta', 'job_eta', 'jobEta'))
      ?? parseEtaEstimate(etaForecast),
    workers: parseWorkers(first(item, 'workers', 'active_workers', 'activeWorkers')),
    retryCount: numberValue(first(item, 'retry_count', 'retryCount', 'retries')),
    failureCount: numberValue(first(item, 'failure_count', 'failureCount', 'failures')),
  }
}

export function parseRenderJob(value: unknown): RenderJob {
  const item = asRecord(value)
  const event = parseRenderEvent(item)
  const identity = asRecord(item.identity)
  const resumable = event.state === 'paused_safely' || event.state === 'resumable'
  return {
    ...event,
    createdAt: stringValue(first(item, 'created_at', 'createdAt'), event.timestamp),
    updatedAt: stringValue(first(item, 'updated_at', 'updatedAt'), event.timestamp),
    outputPath: nullableString(first(item, 'output_path', 'outputPath')) ?? nullableString(first(identity, 'output_directory', 'outputDirectory')),
    projectName: nullableString(first(item, 'project_name', 'projectName')),
    sceneName: nullableString(first(item, 'scene_name', 'sceneName')),
    profileName: nullableString(first(item, 'profile_name', 'profileName')),
    canResume: resumable || booleanValue(first(item, 'can_resume', 'canResume')),
    canEncode: event.state === 'complete' || booleanValue(first(item, 'can_encode', 'canEncode')),
    dryRun: booleanValue(first(item, 'dry_run', 'dryRun'), normalizeToken(item.renderer) === 'fake'),
  }
}

function directorAssessment(value: unknown): DirectorAssessment {
  return value === 'clear' || value === 'acceptable' || value === 'needs-revision' ? value : 'unknown'
}

function directorDecision(value: unknown): DirectorDecision {
  if (value !== 'approve' && value !== 'revise') throw new Error('Director review decision is invalid.')
  return value
}

function parseDirectorAct(value: unknown): DirectorAct {
  const item = asRecord(value)
  return {
    id: stringValue(item.id),
    name: stringValue(item.name),
    frameStart: numberValue(first(item, 'frame_start', 'frameStart')),
    frameEnd: numberValue(first(item, 'frame_end', 'frameEnd')),
    narrativePurpose: stringValue(first(item, 'narrative_purpose', 'narrativePurpose')),
    protagonistState: stringValue(first(item, 'protagonist_state', 'protagonistState')),
  }
}

function parseDirectorShot(value: unknown): DirectorShot {
  const item = asRecord(value)
  return {
    id: stringValue(item.id),
    name: stringValue(item.name),
    actId: stringValue(first(item, 'act_id', 'actId')),
    frameStart: numberValue(first(item, 'frame_start', 'frameStart')),
    frameEnd: numberValue(first(item, 'frame_end', 'frameEnd')),
    storyPurpose: stringValue(first(item, 'story_purpose', 'storyPurpose')),
    protagonistState: stringValue(first(item, 'protagonist_state', 'protagonistState')),
    reviewFrames: Array.isArray(first(item, 'review_frames', 'reviewFrames'))
      ? (first(item, 'review_frames', 'reviewFrames') as unknown[]).map((frame) => numberValue(frame)).filter((frame) => frame > 0)
      : [],
  }
}

function parseDirectorReview(value: unknown): DirectorReview {
  const item = asRecord(value)
  const revision = asRecord(first(item, 'revision_metadata', 'revisionMetadata'))
  const reviewer = first(revision, 'reviewer') === 'human' ? 'human' : 'codex-assisted'
  return {
    schemaVersion: '1.0.0',
    shotId: stringValue(first(item, 'shot_id', 'shotId')),
    reviewFrame: numberValue(first(item, 'review_frame', 'reviewFrame')),
    focalReadability: directorAssessment(first(item, 'focal_readability', 'focalReadability')),
    depth: directorAssessment(item.depth),
    silhouette: directorAssessment(item.silhouette),
    colorHierarchy: directorAssessment(first(item, 'color_hierarchy', 'colorHierarchy')),
    visualDensity: directorAssessment(first(item, 'visual_density', 'visualDensity')),
    storyClarity: directorAssessment(first(item, 'story_clarity', 'storyClarity')),
    mobileReadability: directorAssessment(first(item, 'mobile_readability', 'mobileReadability')),
    findings: strings(item.findings),
    decision: directorDecision(item.decision),
    revisionMetadata: {
      revision: numberValue(revision.revision, 1),
      reviewer,
      note: stringValue(revision.note),
    },
  }
}

export function parseDirectorWorkspace(value: unknown): DirectorWorkspace | null {
  if (value === null || value === undefined) return null
  const item = asRecord(value)
  const storyPlan = asRecord(first(item, 'story_plan', 'storyPlan'))
  const shotPlan = asRecord(first(item, 'shot_plan', 'shotPlan'))
  const reviews = asRecord(item.reviews)
  const analysisJobId = stringValue(first(item, 'analysis_job_id', 'analysisJobId'))
  const acts = records(first(storyPlan, 'acts')).map(parseDirectorAct)
  const shots = records(first(shotPlan, 'shots')).map(parseDirectorShot)
  if (!analysisJobId || acts.length !== 7 || shots.length < 7 || acts.some((act) => !act.id || !act.name) || shots.some((shot) => !shot.id || !shot.actId || shot.reviewFrames.length === 0)) {
    throw new Error('Director workspace schema is invalid.')
  }
  return {
    analysisJobId,
    updatedAt: stringValue(first(item, 'updated_at', 'updatedAt')),
    storyPlan: {
      schemaVersion: stringValue(first(storyPlan, 'schema_version', 'schemaVersion')),
      acts,
    },
    shotPlan: {
      schemaVersion: stringValue(first(shotPlan, 'schema_version', 'schemaVersion')),
      shots,
    },
    reviews: records(first(reviews, 'reviews')).map(parseDirectorReview),
  }
}

function parseLog(value: unknown, index: number): LogEntry {
  const item = asRecord(value)
  const level = normalizeToken(item.level)
  return {
    sequence: numberValue(item.sequence, index + 1),
    timestamp: stringValue(item.timestamp, new Date().toISOString()),
    level: level === 'debug' || level === 'warning' || level === 'error' ? level : 'info',
    message: stringValue(first(item, 'message', 'line')),
    technicalDetails: nullableString(first(item, 'technical_details', 'technicalDetails')),
  }
}

function parseCalibrationCandidate(value: unknown): CalibrationCandidate {
  const item = asRecord(value)
  const id = stringValue(first(item, 'id', 'candidate_id', 'candidateId'))
  const projectedBytes = nullableNumber(first(item, 'projected_storage_bytes', 'projectedStorageBytes'))
  return {
    id,
    profileId: nullableString(first(item, 'profile_id', 'profileId')) ?? id,
    displayName: stringValue(first(item, 'display_name', 'displayName', 'name'), stringValue(item.resolution, id || 'Calibration candidate')),
    resolution: stringValue(item.resolution, 'Unknown'),
    samples: numberValue(item.samples),
    expectedSeconds: nullableNumber(first(item, 'expected_seconds', 'expectedSeconds'))
      ?? (nullableNumber(first(item, 'expected_hours', 'expectedHours')) === null ? null : numberValue(first(item, 'expected_hours', 'expectedHours')) * 3600),
    conservativeSeconds: nullableNumber(first(item, 'conservative_seconds', 'conservativeSeconds'))
      ?? (nullableNumber(first(item, 'conservative_hours', 'conservativeHours')) === null ? null : numberValue(first(item, 'conservative_hours', 'conservativeHours')) * 3600),
    storageGiB: nullableNumber(first(item, 'storage_gib', 'storageGiB')) ?? (projectedBytes === null ? null : projectedBytes / (1024 ** 3)),
    qualityVerdict: stringValue(first(item, 'quality_verdict', 'qualityVerdict', 'verdict', 'quality_result', 'qualityResult'), stringValue(item.status, 'Not reviewed')),
    caveat: nullableString(first(item, 'caveat', 'quality_notes', 'qualityNotes')),
    recommendedRole: nullableString(first(item, 'recommended_role', 'recommendedRole')),
    stillUrls: strings(first(item, 'still_urls', 'stillUrls')),
  }
}

function parseCalibration(value: unknown): CalibrationSummary {
  const item = asRecord(value)
  const status = normalizeToken(item.status)
  const rawError = first(item, 'recoverable_error', 'recoverableError', 'error')
  const recommended = asRecord(first(item, 'recommended_candidate', 'recommendedCandidate'))
  const candidates = records(first(item, 'candidates', 'finalists')).map(parseCalibrationCandidate)
  const ramBytes = nullableNumber(first(item, 'ram_bytes', 'ramBytes'))
  return {
    id: stringValue(first(item, 'id', 'calibration_id', 'calibrationId')),
    status: status === 'planned' || status === 'running' || status === 'review' || status === 'complete' || status === 'failed'
      ? status
      : 'planned',
    completedAt: nullableString(first(item, 'completed_at', 'completedAt')),
    machineName: stringValue(first(item, 'machine_name', 'machineName', 'machine_id', 'machineId'), 'Local machine'),
    gpuName: nullableString(first(item, 'gpu_name', 'gpuName', 'gpu_model', 'gpuModel')),
    cpuName: nullableString(first(item, 'cpu_name', 'cpuName', 'cpu_model', 'cpuModel')),
    ramGiB: nullableNumber(first(item, 'ram_gib', 'ramGiB')) ?? (ramBytes === null ? null : ramBytes / (1024 ** 3)),
    recommendedProfileId: nullableString(first(item, 'recommended_profile_id', 'recommendedProfileId')) ?? nullableString(recommended.id),
    verdict: nullableString(item.verdict) ?? nullableString(first(recommended, 'quality_result', 'qualityResult')),
    candidates,
    recoverableError: rawError === undefined || rawError === null ? null : parseStructuredError(rawError, 'Calibration needs attention'),
  }
}

function parseCloud(value: unknown): CloudReadiness {
  const item = asRecord(value)
  const status = normalizeToken(item.status)
  return {
    providerName: stringValue(first(item, 'provider_name', 'providerName', 'provider'), 'NVIDIA Brev Cloud Rendering'),
    status: status === 'ready' || status === 'offline_ready' ? 'ready' : status === 'setup_required' || status === 'unavailable' ? status : 'unknown',
    offlinePreparationAvailable: booleanValue(first(item, 'offline_preparation_available', 'offlinePreparationAvailable')),
    sanitizedPackageStatus: stringValue(first(item, 'sanitized_package_status', 'sanitizedPackageStatus'), 'Not created'),
    cliReady: booleanValue(first(item, 'cli_ready', 'cliReady', 'brev_cli_installed', 'brevCliInstalled')),
    liveProvisioningVerified: booleanValue(first(item, 'live_provisioning_verified', 'liveProvisioningVerified')),
    liveFleetVerified: booleanValue(first(item, 'live_fleet_verified', 'liveFleetVerified')),
    automaticTeardownVerified: booleanValue(first(item, 'automatic_teardown_verified', 'automaticTeardownVerified')),
    cloudEncodeVerified: booleanValue(first(item, 'cloud_encode_verified', 'cloudEncodeVerified', 'cloud_encode_download_verified', 'cloudEncodeDownloadVerified')),
    checklist: records(item.checklist).map((entry, index) => ({
      id: stringValue(entry.id, `cloud-check-${index + 1}`),
      label: stringValue(entry.label, `Setup step ${index + 1}`),
      complete: booleanValue(entry.complete),
      detail: nullableString(entry.detail),
    })).concat(strings(first(item, 'setup_checklist', 'setupChecklist')).map((label, index) => ({
      id: `cloud-check-${index + 1}`,
      label,
      complete: false,
      detail: null,
    }))),
  }
}

function parseCloudPackage(value: unknown): CloudPackageResult {
  const item = asRecord(value)
  const status = normalizeToken(item.status)
  const available = booleanValue(item.available)
  const accepted = booleanValue(item.accepted)
  return {
    packageId: nullableString(first(item, 'package_id', 'packageId')),
    status: status === 'created' || status === 'validated' || status === 'failed'
      ? status
      : accepted ? 'created' : available ? 'unknown' : 'failed',
    message: stringValue(first(item, 'message', 'detail'), 'The local cloud package operation completed.'),
    outputPath: nullableString(first(item, 'output_path', 'outputPath')),
  }
}

function parsePerformance(value: unknown): PerformanceStatus {
  const item = asRecord(value)
  return {
    supported: booleanValue(first(item, 'supported', 'available')),
    enabled: booleanValue(first(item, 'enabled', 'active')),
    acPower: typeof first(item, 'ac_power', 'acPower', 'on_ac_power', 'onAcPower') === 'boolean' ? booleanValue(first(item, 'ac_power', 'acPower', 'on_ac_power', 'onAcPower')) : null,
    previousPowerPlan: nullableString(first(item, 'previous_power_plan', 'previousPowerPlan', 'previous_power_plan_guid', 'previousPowerPlanGuid')),
    currentPowerPlan: nullableString(first(item, 'current_power_plan', 'currentPowerPlan', 'current_power_plan_guid', 'currentPowerPlanGuid')),
    gpuTemperatureC: nullableNumber(first(item, 'gpu_temperature_c', 'gpuTemperatureC')),
    restoreStatus: nullableString(first(item, 'restore_status', 'restoreStatus', 'detail')),
  }
}

function parseSettings(value: unknown, performanceValue?: unknown): MissionSettings {
  const item = asRecord(value)
  const theme = normalizeToken(item.theme)
  const performance = performanceValue === undefined ? parsePerformance({
    available: first(item, 'performance_mode_available', 'performanceModeAvailable'),
    active: first(item, 'performance_mode_enabled', 'performanceModeEnabled'),
    detail: first(item, 'performance_mode_detail', 'performanceModeDetail'),
  }) : parsePerformance(performanceValue)
  return {
    theme: theme === 'light' || theme === 'dark' ? theme : 'system',
    preferredDrive: nullableString(first(item, 'preferred_drive', 'preferredDrive')),
    outputDefault: nullableString(first(item, 'output_default', 'outputDefault', 'default_output_root', 'defaultOutputRoot')),
    simpleMode: booleanValue(first(item, 'simple_mode', 'simpleMode'), true),
    fakeRendererAvailable: booleanValue(first(item, 'fake_renderer_available', 'fakeRendererAvailable')),
    performanceDetail: nullableString(first(item, 'performance_mode_detail', 'performanceModeDetail')),
    performance,
  }
}

function parseEncodeCandidate(value: unknown): EncodeCandidate {
  const item = asRecord(value)
  const outputKinds = strings(first(item, 'output_kinds', 'outputKinds', 'enabled_output_kinds', 'enabledOutputKinds'))
    .map(normalizeToken)
    .filter((kind): kind is 'delivery' | 'master' => kind === 'delivery' || kind === 'master')
  return {
    jobId: stringValue(first(item, 'job_id', 'jobId')),
    displayName: stringValue(first(item, 'display_name', 'displayName'), 'Verified frame sequence'),
    outputPath: stringValue(first(item, 'output_path', 'outputPath')),
    frameCount: numberValue(first(item, 'frame_count', 'frameCount')),
    totalFrames: numberValue(first(item, 'total_frames', 'totalFrames')),
    verified: booleanValue(item.verified),
    outputKinds,
    videoOutputPath: nullableString(first(item, 'video_output_path', 'videoOutputPath')),
    audioMuxAvailable: booleanValue(first(item, 'audio_mux_available', 'audioMuxAvailable')),
  }
}

function parseEncodeJob(value: unknown): EncodeJob {
  const item = asRecord(value)
  const rawStatus = normalizeToken(item.status)
  const status = ['idle', 'queued', 'encoding', 'verifying', 'complete', 'failed'].includes(rawStatus)
    ? rawStatus as EncodeJob['status']
    : 'failed'
  const currentKindValue = normalizeToken(first(item, 'current_kind', 'currentKind'))
  const currentKind = currentKindValue === 'delivery' || currentKindValue === 'master' ? currentKindValue : null
  const outputPathsRecord = asRecord(first(item, 'output_paths', 'outputPaths'))
  const outputPaths = Object.fromEntries(
    Object.entries(outputPathsRecord).filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
  )
  const completedKinds = strings(first(item, 'completed_kinds', 'completedKinds'))
    .map(normalizeToken)
    .filter((kind): kind is 'delivery' | 'master' => kind === 'delivery' || kind === 'master')
  const outputKinds = strings(first(item, 'output_kinds', 'outputKinds'))
    .map(normalizeToken)
    .filter((kind): kind is 'delivery' | 'master' => kind === 'delivery' || kind === 'master')
  const preferredOutput = currentKind
    ? outputPaths[currentKind] ?? null
    : outputPaths.delivery ?? outputPaths.master ?? null
  return {
    id: stringValue(item.id),
    renderJobId: stringValue(first(item, 'render_job_id', 'renderJobId')),
    status,
    progress: numberValue(item.progress),
    outputKinds,
    completedKinds,
    currentKind,
    currentFrame: nullableNumber(first(item, 'current_frame', 'currentFrame')),
    totalFrames: numberValue(first(item, 'total_frames', 'totalFrames')),
    fps: nullableNumber(item.fps),
    speed: nullableString(item.speed),
    etaSeconds: nullableNumber(first(item, 'eta_seconds', 'etaSeconds')),
    outputPaths,
    outputPath: preferredOutput,
    detail: stringValue(item.detail),
    error: item.error ? parseStructuredError(item.error) : null,
  }
}

export class MissionControlApiError extends Error {
  readonly structured: StructuredError
  readonly status: number

  constructor(structured: StructuredError, status = 0) {
    super(structured.summary)
    this.name = 'MissionControlApiError'
    this.structured = structured
    this.status = status
  }
}

export function errorFromUnknown(error: unknown, title = 'Action could not be completed'): StructuredError {
  if (error instanceof MissionControlApiError) return error.structured
  if (error instanceof Error) {
    return parseStructuredError({ title, summary: error.message, technical_details: error.stack, retryable: true }, title)
  }
  return parseStructuredError({ title, summary: 'The local service returned an unexpected error.', retryable: true }, title)
}

class HttpMissionControlClient implements MissionControlClient {
  constructor(private readonly baseUrl: string) {}

  async checkHealth(): Promise<void> {
    await this.request('/health')
  }

  private async request(path: string, init?: RequestInit): Promise<unknown> {
    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        credentials: 'same-origin',
        headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
        ...init,
      })
    } catch (error) {
      throw new MissionControlApiError(errorFromUnknown(error, 'Local service is unavailable'))
    }

    const contentType = response.headers.get('content-type') ?? ''
    const body = response.status === 204
      ? null
      : contentType.includes('application/json')
        ? await response.json().catch(() => null) as unknown
        : await response.text().catch(() => '')

    if (!response.ok) throw new MissionControlApiError(parseStructuredError(body, 'Local action failed'), response.status)
    return body
  }

  private post(path: string, body: unknown): Promise<unknown> {
    return this.request(path, { method: 'POST', body: JSON.stringify(body) })
  }

  async getSystemStatus(): Promise<SystemStatus> {
    return parseSystemStatus(await this.request('/system/status'))
  }

  async getSystemPaths(): Promise<SystemPaths> {
    return parseSystemPaths(await this.request('/system/paths'))
  }

  async listProjects(): Promise<ProjectSummary[]> {
    return records(await this.request('/projects'), 'projects').map(parseProject).filter((item): item is ProjectSummary => item !== null)
  }

  async listScenes(): Promise<SceneSummary[]> {
    return records(await this.request('/scenes'), 'scenes').map(parseScene).filter((item): item is SceneSummary => item !== null)
  }

  async listProfiles(): Promise<RenderProfileSummary[]> {
    return records(await this.request('/profiles'), 'profiles').map(parseProfile).filter((item): item is RenderProfileSummary => item !== null)
  }

  async listJobs(): Promise<RenderJob[]> {
    return records(await this.request('/jobs'), 'jobs').map(parseRenderJob)
  }

  async getDirectorWorkspace(): Promise<DirectorWorkspace | null> {
    return parseDirectorWorkspace(await this.request('/director/workspace'))
  }

  async putDirectorReview(analysisJobId: string, shotId: string, review: DirectorReview): Promise<DirectorWorkspace> {
    const workspace = parseDirectorWorkspace(await this.request(
      `/director/workspace/${encodeURIComponent(analysisJobId)}/reviews/${encodeURIComponent(shotId)}`,
      { method: 'PUT', body: JSON.stringify(review) },
    ))
    if (!workspace) throw new Error('Director workspace disappeared after saving the review.')
    return workspace
  }

  async listCalibrations(): Promise<CalibrationSummary[]> {
    return records(await this.request('/calibrations'), 'calibrations').map(parseCalibration)
  }

  async getCloudReadiness(): Promise<CloudReadiness> {
    return parseCloud(await this.request('/cloud/readiness'))
  }

  async prepareCloudPackage(profileId: string, sceneId: string, outputPath: string): Promise<CloudPackageResult> {
    return parseCloudPackage(await this.post('/cloud/package', {
      profile_id: profileId,
      scene_id: sceneId,
      output_directory: outputPath,
    }))
  }

  async validateCloudPackage(manifestPath: string): Promise<CloudPackageResult> {
    return parseCloudPackage(await this.post('/cloud/validate', { manifest_path: manifestPath }))
  }

  async getSettings(): Promise<MissionSettings> {
    const [settings, performance] = await Promise.all([
      this.request('/system/settings'),
      this.request('/performance/status'),
    ])
    return parseSettings(settings, performance)
  }

  async updateSettings(settings: Partial<MissionSettings>): Promise<MissionSettings> {
    if (settings.performance) {
      await this.post(settings.performance.enabled ? '/performance/enable' : '/performance/restore', settings.performance.enabled
        ? { operator_confirmed: true, use_high_performance_power_plan: true }
        : { operator_confirmed: true })
    }
    const body: Record<string, unknown> = {}
    if (settings.theme !== undefined) body.theme = settings.theme
    if (settings.preferredDrive !== undefined) body.preferred_drive = settings.preferredDrive
    if (settings.outputDefault !== undefined) body.default_output_root = settings.outputDefault
    if (Object.keys(body).length > 0) {
      await this.request('/system/settings', { method: 'PATCH', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } })
    }
    return this.getSettings()
  }

  async listEncodeCandidates(): Promise<EncodeCandidate[]> {
    const jobs = (await this.listJobs()).filter((job) => job.state === 'complete')
    return Promise.all(jobs.map(async (job) => {
      const readiness = asRecord(await this.request(`/encode/${encodeURIComponent(job.jobId)}/readiness`))
      return parseEncodeCandidate({
        job_id: job.jobId,
        display_name: job.projectName ?? job.profileName ?? 'Verified frame sequence',
        output_path: job.outputPath,
        frame_count: first(readiness, 'published_frames', 'publishedFrames'),
        total_frames: first(readiness, 'total_frames', 'totalFrames'),
        verified: readiness.ready,
        enabled_output_kinds: first(readiness, 'enabled_output_kinds', 'enabledOutputKinds'),
        audio_mux_available: readiness.ready,
      })
    }))
  }

  async selectFolder(initialPath?: string | null): Promise<FolderSelection> {
    const item = asRecord(await this.post('/system/select-folder', {
      initial_directory: initialPath ?? null,
      title: 'Choose a render output folder',
    }))
    return { cancelled: booleanValue(item.cancelled), path: nullableString(item.path) }
  }

  async inspectOutput(selection: RenderSelection): Promise<OutputInspection> {
    return parseOutputInspection(await this.post('/output/inspect', {
      path: selection.outputPath,
      profile_id: selection.profileId,
      scene_id: selection.sceneId,
    }))
  }

  async createOutputChild(selection: RenderSelection): Promise<OutputInspection> {
    return parseOutputInspection(await this.post('/output/create-child', {
      parent_directory: selection.outputPath,
      project_id: selection.projectId,
      profile_id: selection.profileId,
    }))
  }

  async preflight(selection: RenderSelection, renderer: 'production' | 'fake' = 'production'): Promise<PreflightResult> {
    return parsePreflight(await this.post('/render/preflight', { ...selectionBody(selection), renderer }))
  }

  async authorize(
    review: AuthorizationReview,
    confirmations: { configurationReviewed: true; fullRenderApproved: true },
  ): Promise<AuthorizationResult> {
    const body: Record<string, unknown> = {
      scene_id: review.sceneId,
      settings_and_hashes_reviewed: confirmations.configurationReviewed,
      production_render_authorized: confirmations.fullRenderApproved,
    }
    if (review.enabledOutputVariantIds.length > 0) {
      body.enabled_output_variant_ids = review.enabledOutputVariantIds
    }
    const item = asRecord(await this.post(`/profiles/${encodeURIComponent(review.profileId)}/authorize`, body))
    return {
      authorized: booleanValue(item.authorized),
      authorizationId: nullableString(first(item, 'authorization_id', 'authorizationId', 'token_sha256', 'tokenSha256')),
      authorizedAt: nullableString(first(item, 'authorized_at', 'authorizedAt')),
      sceneSha256: nullableString(first(item, 'scene_sha256', 'sceneSha256')),
      profileSha256: nullableString(first(item, 'profile_sha256', 'profileSha256')),
      token: nullableString(first(item, 'token', 'authorization_token', 'authorizationToken')),
      enabledOutputVariantIds: strings(first(item, 'enabled_output_variant_ids', 'enabledOutputVariantIds')),
    }
  }

  async dryRun(request: StartRenderRequest): Promise<DryRunResult> {
    const item = asRecord(await this.post('/render/dry-run', startBody({ ...request, fake: undefined })))
    const identity = asRecord(item.identity)
    return {
      ok: booleanValue(item.ok),
      projectId: stringValue(first(identity, 'project_id', 'projectId')),
      sceneId: stringValue(first(identity, 'scene_id', 'sceneId')),
      profileId: stringValue(first(identity, 'profile_id', 'profileId')),
      outputPath: stringValue(first(identity, 'output_directory', 'outputDirectory')),
      plan: asRecord(item.plan),
      logLines: strings(first(item, 'log_lines', 'logLines')),
    }
  }

  async startRender(request: StartRenderRequest): Promise<RenderJob> {
    return parseRenderJob(await this.post('/render/start', startBody(request)))
  }

  async getRenderJob(jobId: string): Promise<RenderJob> {
    return parseRenderJob(await this.request(`/render/${encodeURIComponent(jobId)}`))
  }

  async getRenderLogs(jobId: string, afterSequence = 0): Promise<LogEntry[]> {
    const query = new URLSearchParams({ afterSequence: String(afterSequence) })
    return records(await this.request(`/render/${encodeURIComponent(jobId)}/logs?${query.toString()}`), 'logs')
      .map(parseLog)
  }

  async requestStopAfterChunk(jobId: string): Promise<RenderJob> {
    return parseRenderJob(await this.post(`/render/${encodeURIComponent(jobId)}/stop-after-chunk`, {}))
  }

  async cancelStopRequest(jobId: string): Promise<RenderJob> {
    return parseRenderJob(await this.post(`/render/${encodeURIComponent(jobId)}/cancel-stop`, { operator_confirmed: true }))
  }

  async cancelRender(jobId: string): Promise<RenderJob> {
    const identity = await this.exactRenderIdentity(jobId, 'cancel')
    return parseRenderJob(await this.post(`/render/${encodeURIComponent(jobId)}/cancel`, {
      ...identity,
      operator_confirmed: true,
    }))
  }

  async retryFailedRender(jobId: string): Promise<RenderJob> {
    const identity = await this.exactRenderIdentity(jobId, 'retry')
    return parseRenderJob(await this.post(`/render/${encodeURIComponent(jobId)}/retry`, {
      ...identity,
      operator_confirmed: true,
    }))
  }

  async retryCurrentChunk(jobId: string): Promise<RenderJob> {
    const identity = await this.exactRenderIdentity(jobId, 'retry')
    return parseRenderJob(await this.post(`/render/${encodeURIComponent(jobId)}/retry-current-chunk`, {
      ...identity,
      operator_confirmed: true,
    }))
  }

  async resumeRender(jobId: string): Promise<RenderJob> {
    const identity = await this.exactRenderIdentity(jobId, 'resume')
    return parseRenderJob(await this.post(`/render/${encodeURIComponent(jobId)}/resume`, {
      ...identity,
    }))
  }

  private async exactRenderIdentity(
    jobId: string,
    operation: 'cancel' | 'resume' | 'retry',
  ): Promise<{ scene_sha256: string; profile_sha256: string }> {
    const job = await this.getRenderJob(jobId)
    if (!job.sceneSha256 || !job.profileSha256) {
      throw new MissionControlApiError(parseStructuredError({
        code: `${operation}_identity_missing`,
        title: 'Exact render identity is unavailable',
        summary: `Mission Control cannot ${operation} until the backend returns both the scene and profile hashes.`,
        recommended_action: 'Refresh the job and inspect its advanced identity details.',
        retryable: true,
        job_id: jobId,
      }))
    }
    return {
      scene_sha256: job.sceneSha256,
      profile_sha256: job.profileSha256,
    }
  }

  async createCalibrationPlan(): Promise<CalibrationSummary> {
    const scenes = await this.listScenes()
    const scene = scenes.find((item) => item.approved && item.status === 'verified') ?? scenes[0]
    if (!scene) {
      throw new MissionControlApiError(parseStructuredError({
        code: 'calibration_scene_missing',
        title: 'Approved scene not found',
        summary: 'A bounded calibration plan needs an approved scene.',
        recommended_action: 'Restore or select an approved scene, then try again.',
        retryable: true,
      }))
    }
    return parseCalibration(await this.post('/calibrations/plan', { scene_id: scene.id, goal: 'RECOMMENDED BALANCED' }))
  }

  async startCalibrationCandidate(calibrationId: string, candidateId: string): Promise<CalibrationSummary> {
    const result = asRecord(await this.post(`/calibrations/${encodeURIComponent(calibrationId)}/run-candidate`, {
      candidate_id: candidateId,
      confirmed_bounded_run: true,
    }))
    if (!booleanValue(result.accepted)) {
      throw new MissionControlApiError(parseStructuredError({
        code: 'calibration_execution_unavailable',
        title: 'Bounded calibration is not connected',
        summary: stringValue(first(result, 'detail', 'message'), 'The backend did not accept the bounded calibration run.'),
        recommended_action: 'Use the existing measured profiles or the validated PowerShell calibration workflow.',
        retryable: false,
      }))
    }
    return parseCalibration(await this.request(`/calibrations/${encodeURIComponent(calibrationId)}`))
  }

  startEncode(
    jobId: string,
    includeAudio: boolean,
    outputKinds: Array<'delivery' | 'master'>,
  ): Promise<EncodeJob> {
    return this.post(`/encode/${encodeURIComponent(jobId)}/start`, {
      output_kinds: outputKinds,
      include_audio: includeAudio,
      operator_confirmed: true,
    }).then(parseEncodeJob)
  }

  async getEncodeJob(jobId: string): Promise<EncodeJob> {
    return parseEncodeJob(await this.request(`/encode/${encodeURIComponent(jobId)}`))
  }

  async openPath(path: string): Promise<void> {
    const normalized = path.toLowerCase()
    const job = (await this.listJobs()).find((item) => {
      const output = item.outputPath?.toLowerCase()
      return output ? normalized === output || normalized.startsWith(`${output}\\`) || normalized.startsWith(`${output}/`) : false
    })
    if (!job) {
      throw new MissionControlApiError(parseStructuredError({
        code: 'open_path_job_missing',
        title: 'Output is not linked to a render job',
        summary: 'Mission Control only opens paths inside an authoritative job output folder.',
        recommended_action: 'Open the render from Jobs and try again.',
        retryable: true,
        related_path: path,
      }))
    }
    await this.post('/system/open-path', { job_id: job.jobId, path })
  }
}

function selectionBody(selection: RenderSelection): Record<string, unknown> {
  const body: Record<string, unknown> = {
    project_id: selection.projectId,
    scene_id: selection.sceneId,
    profile_id: selection.profileId,
    output_directory: selection.outputPath,
  }
  if (selection.enabledOutputVariantIds.length > 0) {
    body.enabled_output_variant_ids = selection.enabledOutputVariantIds
  }
  return body
}

function startBody(request: StartRenderRequest): Record<string, unknown> {
  const body: Record<string, unknown> = {
    ...selectionBody(request),
    renderer: request.renderer ?? 'production',
  }
  if (request.renderer === 'fake' && request.fake) body.fake = request.fake
  return body
}

export function createMissionControlClient(baseUrl = MISSION_CONTROL_API_BASE): MissionControlClient {
  return new HttpMissionControlClient(baseUrl)
}
