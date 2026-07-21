import type {
  AuthorizationResult,
  AuthorizationReview,
  CalibrationCandidate,
  CalibrationSummary,
  CheckStatus,
  CloudPackageResult,
  CloudReadiness,
  DryRunResult,
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
  StartRenderRequest,
  StructuredError,
  SystemPaths,
  SystemStatus,
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
  'stop_requested', 'finishing_current_chunk', 'paused_safely', 'resumable',
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
  const ready = normalizeToken(item.status) === 'ready'
  return {
    serviceName: stringValue(first(item, 'service_name', 'serviceName'), 'WZHK Media Mission Control'),
    version: stringValue(item.version, 'unknown'),
    ready,
    instanceId: nullableString(first(item, 'instance_id', 'instanceId')),
    startedAt: nullableString(first(item, 'started_at', 'startedAt')),
    machineName: nullableString(first(item, 'machine_name', 'machineName')),
    blenderReady: normalizeToken(blender?.status) === 'pass',
    ffmpegReady: false,
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
  return {
    id,
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

export function parseRenderEvent(value: unknown): RenderEvent {
  const item = asRecord(value)
  const identity = asRecord(item.identity)
  const rawError = first(item, 'error', 'structured_error', 'structuredError')
  const safeStop = normalizeToken(first(item, 'safe_stop_status', 'safeStopStatus'))
  const previewReference = nullableString(first(item, 'preview_url', 'previewUrl', 'latest_frame_preview_reference', 'latest_frame_preview', 'latestFramePreview'))
  const previewMatch = previewReference?.match(/frame[_-]?(\d{1,9})\.png/i)
  const previewUrl = previewReference && (/^https?:\/\//i.test(previewReference) || previewReference.startsWith('/api/'))
    ? previewReference
    : null
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
    previewFrame: nullableNumber(first(item, 'preview_frame', 'previewFrame', 'latest_preview_frame', 'latestPreviewFrame'))
      ?? (previewMatch?.[1] ? Number.parseInt(previewMatch[1], 10) : null),
    latestLogLine: nullableString(first(item, 'latest_log_line', 'latestLogLine')),
    warning: nullableString(item.warning),
    error: rawError === null || rawError === undefined ? null : parseStructuredError(rawError, 'Render issue'),
    safeStopStatus: safeStop === 'none' || safeStop === 'requested' || safeStop === 'finishing_chunk' || safeStop === 'paused' || safeStop === 'cancelled'
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
  return {
    jobId: stringValue(first(item, 'job_id', 'jobId')),
    displayName: stringValue(first(item, 'display_name', 'displayName'), 'Verified frame sequence'),
    outputPath: stringValue(first(item, 'output_path', 'outputPath')),
    frameCount: numberValue(first(item, 'frame_count', 'frameCount')),
    totalFrames: numberValue(first(item, 'total_frames', 'totalFrames')),
    verified: booleanValue(item.verified),
    videoOutputPath: nullableString(first(item, 'video_output_path', 'videoOutputPath')),
    audioMuxAvailable: booleanValue(first(item, 'audio_mux_available', 'audioMuxAvailable')),
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
        audio_mux_available: false,
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
    const item = asRecord(await this.post(`/profiles/${encodeURIComponent(review.profileId)}/authorize`, {
      scene_id: review.sceneId,
      settings_and_hashes_reviewed: confirmations.configurationReviewed,
      production_render_authorized: confirmations.fullRenderApproved,
    }))
    return {
      authorized: booleanValue(item.authorized),
      authorizationId: nullableString(first(item, 'authorization_id', 'authorizationId', 'token_sha256', 'tokenSha256')),
      authorizedAt: nullableString(first(item, 'authorized_at', 'authorizedAt')),
      sceneSha256: nullableString(first(item, 'scene_sha256', 'sceneSha256')),
      profileSha256: nullableString(first(item, 'profile_sha256', 'profileSha256')),
      token: nullableString(first(item, 'token', 'authorization_token', 'authorizationToken')),
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

  async resumeRender(jobId: string): Promise<RenderJob> {
    const job = await this.getRenderJob(jobId)
    if (!job.sceneSha256 || !job.profileSha256) {
      throw new MissionControlApiError(parseStructuredError({
        code: 'resume_identity_missing',
        title: 'Exact render identity is unavailable',
        summary: 'Mission Control cannot resume until the backend returns both the scene and profile hashes.',
        recommended_action: 'Refresh the job and inspect its advanced identity details.',
        retryable: true,
        job_id: jobId,
      }))
    }
    return parseRenderJob(await this.post(`/render/${encodeURIComponent(jobId)}/resume`, {
      scene_sha256: job.sceneSha256,
      profile_sha256: job.profileSha256,
    }))
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

  startEncode(jobId: string, includeAudio: boolean): Promise<EncodeJob> {
    void jobId
    void includeAudio
    return Promise.reject(new MissionControlApiError(parseStructuredError({
      code: 'encode_execution_unavailable',
      title: 'Encoding is not connected yet',
      summary: 'The backend verifies frame-sequence readiness but does not expose an encode start action.',
      recommended_action: 'Keep the verified sequence and use the validated encode workflow until the server adapter is connected.',
      retryable: false,
    })))
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

function selectionBody(selection: RenderSelection): Record<string, string> {
  return {
    project_id: selection.projectId,
    scene_id: selection.sceneId,
    profile_id: selection.profileId,
    output_directory: selection.outputPath,
  }
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
