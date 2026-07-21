export type MissionSection =
  | 'home'
  | 'render'
  | 'profiles'
  | 'calibration'
  | 'jobs'
  | 'encode'
  | 'cloud'
  | 'settings'

export type ConnectionState = 'connected' | 'reconnecting' | 'offline'

export type RenderState =
  | 'idle'
  | 'validating'
  | 'authorization_required'
  | 'ready'
  | 'starting'
  | 'running'
  | 'stop_requested'
  | 'finishing_current_chunk'
  | 'paused_safely'
  | 'resumable'
  | 'encoding'
  | 'verifying'
  | 'complete'
  | 'failed'
  | 'cancelled'

export type RenderPhase =
  | 'scene_load'
  | 'render_frame'
  | 'write_frame'
  | 'validate_frame'
  | 'validate_chunk'
  | 'publish_chunk'
  | 'waiting_for_storage'
  | 'encode_master'
  | 'encode_delivery'
  | 'mux_audio'
  | 'final_verify'
  | 'idle'

export type CheckStatus = 'pass' | 'warning' | 'fail' | 'pending'

export interface MissionCapabilities {
  nativeFolderPicker: boolean
  realtimeEvents: 'sse' | 'websocket' | 'polling' | 'unavailable'
  renderExecution: boolean
  encode: boolean
  performanceMode: boolean
  cloudPreparation: boolean
  cloudLive: boolean
  demoMode: boolean
}

export interface SystemStatus {
  serviceName: string
  version: string
  ready: boolean
  instanceId: string | null
  startedAt: string | null
  machineName: string | null
  blenderReady: boolean
  ffmpegReady: boolean
  rendererBusy: boolean
  activeJobId: string | null
  capabilities: MissionCapabilities
  warnings: string[]
}

export interface SystemPaths {
  blenderPath: string | null
  ffmpegPath: string | null
  profileRoot: string | null
  outputDefault: string | null
  calibrationRoot: string | null
  preferredDrive: string | null
}

export interface ProjectSummary {
  id: string
  displayName: string
  description: string | null
  recommendedSceneId: string | null
  recommendedProfileId: string | null
  current: boolean
  thumbnailUrl: string | null
}

export interface SceneSummary {
  id: string
  projectId: string
  displayName: string
  approved: boolean
  status: 'verified' | 'changed' | 'missing' | 'unknown'
  sha256: string | null
  path: string | null
  thumbnailUrl: string | null
  frameStart: number
  frameEnd: number
  totalFrames: number
  fps: number
}

export interface RenderProfileSummary {
  id: string
  displayName: string
  width: number
  height: number
  fps: number
  expectedSeconds: number | null
  conservativeSeconds: number | null
  storageGiB: number | null
  minimumFreeGiB: number | null
  qualityRole: string
  qualityDescription: string | null
  calibrated: boolean
  authorizationStatus: 'authorized' | 'required' | 'invalid' | 'unknown'
  recommended: boolean
  localRecommendation: string | null
  lastUsedAt: string | null
  savedFileSha256: string | null
  path: string | null
}

export interface OutputConflictIdentity {
  projectId: string | null
  sceneId: string | null
  profileId: string | null
  sceneSha256: string | null
  profileSha256: string | null
}

export type OutputClassification =
  | 'empty'
  | 'compatible_resume'
  | 'incompatible_render'
  | 'unrelated_files'
  | 'hidden_entries'
  | 'parent_suitable'
  | 'missing'
  | 'unknown'

export interface OutputInspection {
  path: string
  classification: OutputClassification
  usable: boolean
  resumable: boolean
  entries: string[]
  message: string
  suggestedChildName: string | null
  conflictingIdentity: OutputConflictIdentity | null
}

export interface PreflightCheck {
  id: string
  label: string
  status: CheckStatus
  summary: string
  technicalDetails: string | null
}

export interface PreflightResult {
  ready: boolean
  authorizationRequired: boolean
  checks: PreflightCheck[]
  sceneSha256: string | null
  profileSha256: string | null
  exactOperation: string
  rawDetails: unknown
}

export interface AuthorizationReview {
  projectId: string
  sceneId: string
  profileId: string
  outputPath: string
  expectedSeconds: number | null
  totalFrames: number
  storageGiB: number | null
  exactOperation: string
}

export interface AuthorizationResult {
  authorized: boolean
  authorizationId: string | null
  authorizedAt: string | null
  sceneSha256: string | null
  profileSha256: string | null
  token: string | null
}

export interface StructuredError {
  code: string
  title: string
  summary: string
  likelyCause: string | null
  recommendedAction: string | null
  retryable: boolean
  context: Record<string, unknown>
  technicalDetails: string | null
  relatedPath: string | null
  timestamp: string
  jobId: string | null
}

export interface RenderMetrics {
  currentSecondsPerFrame: number | null
  rollingMedianSeconds: number | null
  rollingMeanSeconds: number | null
  p90Seconds: number | null
  currentStorageBytes: number | null
  projectedStorageBytes: number | null
  freeStorageBytes: number | null
  gpuUtilizationPercent: number | null
  vramUsedBytes: number | null
  gpuTemperatureC: number | null
  cpuUtilizationPercent: number | null
  ramUsedBytes: number | null
}

export interface RenderEvent {
  schemaVersion: string
  sequence: number
  timestamp: string
  jobId: string
  projectId: string | null
  state: RenderState
  phase: RenderPhase
  sceneId: string | null
  sceneSha256: string | null
  profileId: string | null
  profileSha256: string | null
  frameStart: number | null
  frameEnd: number | null
  currentFrame: number | null
  lastCompletedFrame: number | null
  renderedFrames: number
  inFlightFrames: number
  validatedFrames: number
  publishedFrames: number
  totalFrames: number
  chunkStart: number | null
  chunkEnd: number | null
  currentChunkProgress: number | null
  chunksCompleted: number
  chunksTotal: number
  estimatedCompletionAt: string | null
  etaConfidence: 'low' | 'medium' | 'high' | 'unknown'
  metrics: RenderMetrics
  previewUrl: string | null
  previewFrame: number | null
  latestLogLine: string | null
  warning: string | null
  error: StructuredError | null
  safeStopStatus: 'none' | 'requested' | 'finishing_chunk' | 'paused' | 'cancelled' | 'unknown'
  rendererActive: boolean | null
  watcherActive: boolean | null
  currentFrameStartedAt: string | null
  lastOutputAt: string | null
}

export interface RenderJob extends RenderEvent {
  createdAt: string
  updatedAt: string
  outputPath: string | null
  projectName: string | null
  sceneName: string | null
  profileName: string | null
  canResume: boolean
  canEncode: boolean
  dryRun: boolean
}

export interface LogEntry {
  sequence: number
  timestamp: string
  level: 'debug' | 'info' | 'warning' | 'error'
  message: string
  technicalDetails: string | null
}

export interface CalibrationCandidate {
  id: string
  profileId: string | null
  displayName: string
  resolution: string
  samples: number
  expectedSeconds: number | null
  conservativeSeconds: number | null
  storageGiB: number | null
  qualityVerdict: string
  caveat: string | null
  recommendedRole: string | null
  stillUrls: string[]
}

export interface CalibrationSummary {
  id: string
  status: 'planned' | 'running' | 'review' | 'complete' | 'failed'
  completedAt: string | null
  machineName: string
  gpuName: string | null
  cpuName: string | null
  ramGiB: number | null
  recommendedProfileId: string | null
  verdict: string | null
  candidates: CalibrationCandidate[]
  recoverableError: StructuredError | null
}

export interface CloudReadiness {
  providerName: string
  status: 'ready' | 'setup_required' | 'unavailable' | 'unknown'
  offlinePreparationAvailable: boolean
  sanitizedPackageStatus: string
  cliReady: boolean
  liveProvisioningVerified: boolean
  liveFleetVerified: boolean
  automaticTeardownVerified: boolean
  cloudEncodeVerified: boolean
  checklist: Array<{ id: string; label: string; complete: boolean; detail: string | null }>
}

export interface CloudPackageResult {
  packageId: string | null
  status: 'created' | 'validated' | 'failed' | 'unknown'
  message: string
  outputPath: string | null
}

export interface PerformanceStatus {
  supported: boolean
  enabled: boolean
  acPower: boolean | null
  previousPowerPlan: string | null
  currentPowerPlan: string | null
  gpuTemperatureC: number | null
  restoreStatus: string | null
}

export interface MissionSettings {
  theme: 'system' | 'light' | 'dark'
  preferredDrive: string | null
  outputDefault: string | null
  simpleMode: boolean
  fakeRendererAvailable: boolean
  performanceDetail: string | null
  performance: PerformanceStatus
}

export interface EncodeCandidate {
  jobId: string
  displayName: string
  outputPath: string
  frameCount: number
  totalFrames: number
  verified: boolean
  videoOutputPath: string | null
  audioMuxAvailable: boolean
}

export interface EncodeJob {
  id: string
  renderJobId: string
  status: 'queued' | 'encoding' | 'muxing' | 'verifying' | 'complete' | 'failed'
  progress: number
  outputPath: string | null
  error: StructuredError | null
}

export interface DashboardSnapshot {
  system: SystemStatus
  paths: SystemPaths
  projects: ProjectSummary[]
  scenes: SceneSummary[]
  profiles: RenderProfileSummary[]
  jobs: RenderJob[]
  calibrations: CalibrationSummary[]
  cloud: CloudReadiness | null
  settings: MissionSettings | null
  encodeCandidates: EncodeCandidate[]
}

export interface RenderSelection {
  projectId: string
  sceneId: string
  profileId: string
  outputPath: string
}

export interface StartRenderRequest extends RenderSelection {
  authorizationId: string | null
  performanceMode: boolean
  renderer?: 'production' | 'fake'
  fake?: {
    totalFrames?: number
    framesPerChunk?: number
    stepDelaySeconds?: number
    failAtFrame?: number
    storageWarningAtFrame?: number
    longFrameAt?: number
  }
}

export interface DryRunResult {
  ok: boolean
  projectId: string
  sceneId: string
  profileId: string
  outputPath: string
  plan: Record<string, unknown>
  logLines: string[]
}

export interface FolderSelection {
  cancelled: boolean
  path: string | null
}

export interface MissionControlClient {
  checkHealth: () => Promise<void>
  getSystemStatus: () => Promise<SystemStatus>
  getSystemPaths: () => Promise<SystemPaths>
  listProjects: () => Promise<ProjectSummary[]>
  listScenes: () => Promise<SceneSummary[]>
  listProfiles: () => Promise<RenderProfileSummary[]>
  listJobs: () => Promise<RenderJob[]>
  listCalibrations: () => Promise<CalibrationSummary[]>
  getCloudReadiness: () => Promise<CloudReadiness>
  prepareCloudPackage: (profileId: string, sceneId: string, outputPath: string) => Promise<CloudPackageResult>
  validateCloudPackage: (packageId: string) => Promise<CloudPackageResult>
  getSettings: () => Promise<MissionSettings>
  updateSettings: (settings: Partial<MissionSettings>) => Promise<MissionSettings>
  listEncodeCandidates: () => Promise<EncodeCandidate[]>
  selectFolder: (initialPath?: string | null) => Promise<FolderSelection>
  inspectOutput: (selection: RenderSelection) => Promise<OutputInspection>
  createOutputChild: (selection: RenderSelection) => Promise<OutputInspection>
  preflight: (selection: RenderSelection, renderer?: 'production' | 'fake') => Promise<PreflightResult>
  authorize: (review: AuthorizationReview, confirmations: { configurationReviewed: true; fullRenderApproved: true }) => Promise<AuthorizationResult>
  dryRun: (request: StartRenderRequest) => Promise<DryRunResult>
  startRender: (request: StartRenderRequest) => Promise<RenderJob>
  getRenderJob: (jobId: string) => Promise<RenderJob>
  getRenderLogs: (jobId: string, afterSequence?: number) => Promise<LogEntry[]>
  requestStopAfterChunk: (jobId: string) => Promise<RenderJob>
  cancelStopRequest: (jobId: string) => Promise<RenderJob>
  resumeRender: (jobId: string) => Promise<RenderJob>
  createCalibrationPlan: () => Promise<CalibrationSummary>
  startCalibrationCandidate: (calibrationId: string, candidateId: string) => Promise<CalibrationSummary>
  startEncode: (jobId: string, includeAudio: boolean) => Promise<EncodeJob>
  openPath: (path: string) => Promise<void>
}

export interface RenderEventSubscription {
  close: () => void
  getLastSequence: () => number
}

export interface RenderEventSubscriber {
  subscribe: (options: {
    jobId: string
    afterSequence: number
    onConnection: (state: ConnectionState) => void
    onEvent: (event: RenderEvent) => void
    onError: (error: StructuredError) => void
  }) => RenderEventSubscription
}
