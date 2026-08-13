import {
  MISSION_CONTROL_API_BASE,
  MissionControlApiError,
  parseStructuredError,
} from './api'

export type VideoJobState =
  | 'planned' | 'authorized' | 'smoke_submitted' | 'generating' | 'partial'
  | 'review_ready' | 'timeline_ready' | 'exported' | 'assembling' | 'complete'
  | 'blocked_budget' | 'blocked_provider_access' | 'blocked_provider_quota'
  | 'failed' | 'cancelled'

export type VideoShotState =
  | 'planned' | 'reserved' | 'submitted' | 'running' | 'succeeded'
  | 'filtered' | 'failed' | 'downloaded' | 'verified'

export type VideoReviewState = 'pending' | 'accepted' | 'rejected'

export interface VideoError {
  code: string
  summary: string
  retryable: boolean
  httpStatus: number | null
  providerStatus: string | null
  providerErrorCode: string | null
  diagnosticId: string | null
}

export interface VideoAnalysisSource {
  analysisJobId: string
  displayName: string
  storyPlanAvailable: boolean
  shotPlanAvailable: boolean
  retainedAudioAvailable: boolean
}

export interface VideoProfile {
  id: 'fast-1080p' | 'quality-1080p' | 'quality-4k'
  displayName: string
  modelId: string
  resolution: string
  durationSeconds: number
  fps: number
  sampleCount: number
  default: boolean
  optional: boolean
  baseEstimatedUsd: number
  conservativeEstimatedUsd: number
  maxSpendUsd: number
  available: boolean
  availabilityNote: string | null
}

export interface VideoPackage {
  projectId: string
  title: string
  shotCount: number
  profiles: VideoProfile[]
}

export interface VideoCatalog {
  analyses: VideoAnalysisSource[]
  packages: VideoPackage[]
  pricingSnapshotDate: string
  providerNetworkContacted: false
}

export interface VideoShot {
  shotId: string
  chapterId: string
  order: number
  title: string
  prompt: string
  negativePrompt: string
  seed: number
  state: VideoShotState
  reviewState: VideoReviewState
  reviewNote: string | null
  attemptCount: number
  reservedCostUsd: number
  error: VideoError | null
  clipUrl: string | null
  variationIndex: number
  continuityGroupIds: string[]
  previousShotId: string | null
  continuationMode: string
  referenceAssetId: string | null
}

export interface VideoArtifacts {
  timelineReady: boolean
  davinciPackageReady: boolean
  previewReady: boolean
  fcpxmlUrl: string | null
  fcp7XmlUrl: string | null
  edlUrl: string | null
  editSheetUrl: string | null
  markersUrl: string | null
  previewUrl: string | null
}

export interface VideoJob {
  jobId: string
  analysisJobId: string
  projectId: string
  title: string
  state: VideoJobState
  planDigest: string
  profile: Record<string, unknown>
  cost: {
    baseEstimatedUsd: number
    conservativeEstimatedUsd: number
    maxSpendUsd: number
    pricingSnapshotDate: string
    rateUsdPerOutputSecond: number
  }
  sourceArtifacts: Record<string, string>
  authorizationPhrase: string
  authorizationExpiresAt: string | null
  audioMasterBound: boolean
  shots: VideoShot[]
  progressPercent: number
  verifiedShotCount: number
  totalShotCount: number
  reservedCostUsd: number
  remainingAuthorizedUsd: number
  requestPreviewUrl: string
  consistencyNotice: string
  continuity: {
    masterSeed?: number
    seedLocked?: boolean
    seedDerivation?: string
    characterProfiles?: Array<Record<string, unknown>>
    visualAnchors?: Record<string, unknown>
    groups?: Array<Record<string, unknown>>
  }
  artifacts: VideoArtifacts
  error: VideoError | null
  createdAt: string
  updatedAt: string
}

export interface VideoRequestPreview {
  jobId: string
  planDigest: string
  requests: Array<Record<string, unknown>>
}

export interface VideoDoctorResult {
  ok: boolean
  networkContacted: boolean
  generationSubmitted: false
  checks: Array<{ id: string; status: 'pass' | 'fail' | 'unknown'; code: string; detail: string }>
}

type RecordValue = Record<string, unknown>

function record(value: unknown, label: string): RecordValue {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`${label} must be an object`)
  }
  return value as RecordValue
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`)
  return value
}

function text(value: unknown, label: string): string {
  if (typeof value !== 'string') throw new Error(`${label} must be a string`)
  return value
}

function numberValue(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${label} must be a number`)
  return value
}

function booleanValue(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') throw new Error(`${label} must be a boolean`)
  return value
}

function nullableText(value: unknown, label: string): string | null {
  if (value === null) return null
  return text(value, label)
}

function enumValue<T extends string>(value: unknown, allowed: readonly T[], label: string): T {
  const candidate = text(value, label)
  if (!allowed.includes(candidate as T)) throw new Error(`${label} is unsupported`)
  return candidate as T
}

function parseError(value: unknown, label: string): VideoError | null {
  if (value === null) return null
  const item = record(value, label)
  return {
    code: text(item.code, `${label}.code`),
    summary: text(item.summary, `${label}.summary`),
    retryable: booleanValue(item.retryable, `${label}.retryable`),
    httpStatus: item.httpStatus === null ? null : numberValue(item.httpStatus, `${label}.httpStatus`),
    providerStatus: nullableText(item.providerStatus, `${label}.providerStatus`),
    providerErrorCode: nullableText(item.providerErrorCode, `${label}.providerErrorCode`),
    diagnosticId: nullableText(item.diagnosticId, `${label}.diagnosticId`),
  }
}

const JOB_STATES: readonly VideoJobState[] = [
  'planned', 'authorized', 'smoke_submitted', 'generating', 'partial', 'review_ready',
  'timeline_ready', 'exported', 'assembling', 'complete', 'blocked_budget',
  'blocked_provider_access', 'blocked_provider_quota', 'failed', 'cancelled',
]
const SHOT_STATES: readonly VideoShotState[] = [
  'planned', 'reserved', 'submitted', 'running', 'succeeded', 'filtered',
  'failed', 'downloaded', 'verified',
]
const REVIEW_STATES: readonly VideoReviewState[] = ['pending', 'accepted', 'rejected']

export function parseVideoCatalog(value: unknown): VideoCatalog {
  const item = record(value, 'video catalog')
  return {
    analyses: array(item.analyses, 'video catalog analyses').map((raw, index) => {
      const source = record(raw, `analysis ${index}`)
      return {
        analysisJobId: text(source.analysisJobId, 'analysisJobId'),
        displayName: text(source.displayName, 'analysis displayName'),
        storyPlanAvailable: booleanValue(source.storyPlanAvailable, 'storyPlanAvailable'),
        shotPlanAvailable: booleanValue(source.shotPlanAvailable, 'shotPlanAvailable'),
        retainedAudioAvailable: booleanValue(source.retainedAudioAvailable, 'retainedAudioAvailable'),
      }
    }),
    packages: array(item.packages, 'video catalog packages').map((raw, packageIndex) => {
      const pack = record(raw, `package ${packageIndex}`)
      return {
        projectId: text(pack.projectId, 'package projectId'),
        title: text(pack.title, 'package title'),
        shotCount: numberValue(pack.shotCount, 'package shotCount'),
        profiles: array(pack.profiles, 'package profiles').map((profileRaw, profileIndex) => {
          const profile = record(profileRaw, `profile ${profileIndex}`)
          return {
            id: enumValue(profile.id, ['fast-1080p', 'quality-1080p', 'quality-4k'] as const, 'profile id'),
            displayName: text(profile.displayName, 'profile displayName'),
            modelId: text(profile.modelId, 'profile modelId'),
            resolution: text(profile.resolution, 'profile resolution'),
            durationSeconds: numberValue(profile.durationSeconds, 'profile durationSeconds'),
            fps: numberValue(profile.fps, 'profile fps'),
            sampleCount: numberValue(profile.sampleCount, 'profile sampleCount'),
            default: booleanValue(profile.default, 'profile default'),
            optional: booleanValue(profile.optional, 'profile optional'),
            baseEstimatedUsd: numberValue(profile.baseEstimatedUsd, 'profile baseEstimatedUsd'),
            conservativeEstimatedUsd: numberValue(profile.conservativeEstimatedUsd, 'profile conservativeEstimatedUsd'),
            maxSpendUsd: numberValue(profile.maxSpendUsd, 'profile maxSpendUsd'),
            available: booleanValue(profile.available, 'profile available'),
            availabilityNote: nullableText(profile.availabilityNote, 'profile availabilityNote'),
          }
        }),
      }
    }),
    pricingSnapshotDate: text(item.pricingSnapshotDate, 'pricingSnapshotDate'),
    providerNetworkContacted: false,
  }
}

export function parseVideoJob(value: unknown): VideoJob {
  const item = record(value, 'video job')
  const cost = record(item.cost, 'video job cost')
  const artifacts = record(item.artifacts, 'video job artifacts')
  const sourceArtifacts = record(item.sourceArtifacts, 'source artifacts')
  return {
    jobId: text(item.jobId, 'jobId'),
    analysisJobId: text(item.analysisJobId, 'analysisJobId'),
    projectId: text(item.projectId, 'projectId'),
    title: text(item.title, 'title'),
    state: enumValue(item.state, JOB_STATES, 'job state'),
    planDigest: text(item.planDigest, 'planDigest'),
    profile: record(item.profile, 'profile'),
    cost: {
      baseEstimatedUsd: numberValue(cost.baseEstimatedUsd, 'baseEstimatedUsd'),
      conservativeEstimatedUsd: numberValue(cost.conservativeEstimatedUsd, 'conservativeEstimatedUsd'),
      maxSpendUsd: numberValue(cost.maxSpendUsd, 'maxSpendUsd'),
      pricingSnapshotDate: text(cost.pricingSnapshotDate, 'pricingSnapshotDate'),
      rateUsdPerOutputSecond: numberValue(cost.rateUsdPerOutputSecond, 'rateUsdPerOutputSecond'),
    },
    sourceArtifacts: Object.fromEntries(Object.entries(sourceArtifacts).map(([key, entry]) => [key, text(entry, key)])),
    authorizationPhrase: text(item.authorizationPhrase, 'authorizationPhrase'),
    authorizationExpiresAt: nullableText(item.authorizationExpiresAt, 'authorizationExpiresAt'),
    audioMasterBound: booleanValue(item.audioMasterBound, 'audioMasterBound'),
    shots: array(item.shots, 'shots').map((raw, index) => {
      const shot = record(raw, `shot ${index}`)
      return {
        shotId: text(shot.shotId, 'shotId'),
        chapterId: text(shot.chapterId, 'chapterId'),
        order: numberValue(shot.order, 'shot order'),
        title: text(shot.title, 'shot title'),
        prompt: text(shot.prompt, 'shot prompt'),
        negativePrompt: text(shot.negativePrompt, 'negative prompt'),
        seed: numberValue(shot.seed, 'shot seed'),
        state: enumValue(shot.state, SHOT_STATES, 'shot state'),
        reviewState: enumValue(shot.reviewState, REVIEW_STATES, 'review state'),
        reviewNote: nullableText(shot.reviewNote, 'review note'),
        attemptCount: numberValue(shot.attemptCount, 'attempt count'),
        reservedCostUsd: numberValue(shot.reservedCostUsd, 'reserved cost'),
        error: parseError(shot.error, 'shot error'),
        clipUrl: nullableText(shot.clipUrl, 'clip URL'),
        variationIndex: numberValue(shot.variationIndex, 'variation index'),
        continuityGroupIds: array(shot.continuityGroupIds, 'continuity group IDs').map((entry) => text(entry, 'continuity group ID')),
        previousShotId: nullableText(shot.previousShotId, 'previous shot ID'),
        continuationMode: text(shot.continuationMode, 'continuation mode'),
        referenceAssetId: nullableText(shot.referenceAssetId, 'reference asset ID'),
      }
    }),
    progressPercent: numberValue(item.progressPercent, 'progressPercent'),
    verifiedShotCount: numberValue(item.verifiedShotCount, 'verifiedShotCount'),
    totalShotCount: numberValue(item.totalShotCount, 'totalShotCount'),
    reservedCostUsd: numberValue(item.reservedCostUsd, 'reservedCostUsd'),
    remainingAuthorizedUsd: numberValue(item.remainingAuthorizedUsd, 'remainingAuthorizedUsd'),
    requestPreviewUrl: text(item.requestPreviewUrl, 'requestPreviewUrl'),
    consistencyNotice: text(item.consistencyNotice, 'consistencyNotice'),
    continuity: record(item.continuity, 'continuity'),
    artifacts: {
      timelineReady: booleanValue(artifacts.timelineReady, 'timelineReady'),
      davinciPackageReady: booleanValue(artifacts.davinciPackageReady, 'davinciPackageReady'),
      previewReady: booleanValue(artifacts.previewReady, 'previewReady'),
      fcpxmlUrl: nullableText(artifacts.fcpxmlUrl, 'fcpxmlUrl'),
      fcp7XmlUrl: nullableText(artifacts.fcp7XmlUrl, 'fcp7XmlUrl'),
      edlUrl: nullableText(artifacts.edlUrl, 'edlUrl'),
      editSheetUrl: nullableText(artifacts.editSheetUrl, 'editSheetUrl'),
      markersUrl: nullableText(artifacts.markersUrl, 'markersUrl'),
      previewUrl: nullableText(artifacts.previewUrl, 'previewUrl'),
    },
    error: parseError(item.error, 'job error'),
    createdAt: text(item.createdAt, 'createdAt'),
    updatedAt: text(item.updatedAt, 'updatedAt'),
  }
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(`${MISSION_CONTROL_API_BASE}${path}`, {
      credentials: 'same-origin',
      headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
      ...init,
    })
  } catch (error) {
    throw new MissionControlApiError(parseStructuredError(error, 'Local service is unavailable'))
  }
  const contentType = response.headers.get('content-type') ?? ''
  const body: unknown = response.status === 204
    ? null
    : contentType.includes('application/json')
      ? await response.json().catch(() => null) as unknown
      : await response.text().catch(() => '')
  if (!response.ok) throw new MissionControlApiError(parseStructuredError(body, 'Video action failed'), response.status)
  return body
}

const post = (path: string, body?: unknown): Promise<unknown> => request(path, {
  method: 'POST',
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const videoGenerationClient = {
  async selectAudio(initialDirectory: string | null = null): Promise<string | null> {
    const item = record(await post('/system/select-file', {
      initialDirectory,
      title: 'Select the original local audio master',
    }), 'audio selection')
    const selected = booleanValue(item.selected, 'audio selected')
    return selected ? text(item.path, 'audio path') : null
  },
  async selectReferenceImage(initialDirectory: string | null = null): Promise<string | null> {
    const item = record(await post('/system/select-file', {
      initialDirectory,
      title: 'Select a private JPEG or PNG continuity reference',
    }), 'reference image selection')
    const selected = booleanValue(item.selected, 'reference image selected')
    return selected ? text(item.path, 'reference image path') : null
  },
  async catalog(): Promise<VideoCatalog> {
    return parseVideoCatalog(await request('/video/catalog'))
  },
  async jobs(): Promise<VideoJob[]> {
    return array(await request('/video/jobs'), 'video jobs').map(parseVideoJob)
  },
  async get(jobId: string): Promise<VideoJob> {
    return parseVideoJob(await request(`/video/plans/${encodeURIComponent(jobId)}`))
  },
  async createPlan(input: {
    analysisJobId: string
    projectId: string
    profileId: VideoProfile['id']
    gcpProjectId: string
    gcsBucket: string
    audioPath: string | null
    masterSeed: number
    seedLocked: boolean
    referenceImagePath: string | null
  }): Promise<VideoJob> {
    return parseVideoJob(await post('/video/plans', input))
  },
  async requests(jobId: string): Promise<VideoRequestPreview> {
    const item = record(await request(`/video/plans/${encodeURIComponent(jobId)}/requests`), 'request preview')
    return {
      jobId: text(item.jobId, 'request jobId'),
      planDigest: text(item.planDigest, 'request planDigest'),
      requests: array(item.requests, 'requests').map((entry, index) => record(entry, `request ${index}`)),
    }
  },
  async authorize(jobId: string, confirmation: string): Promise<VideoJob> {
    return parseVideoJob(await post(`/video/plans/${encodeURIComponent(jobId)}/authorize`, { confirmation }))
  },
  async action(jobId: string, action: 'start' | 'resume' | 'cancel' | 'resolve' | 'export' | 'assemble'): Promise<VideoJob> {
    return parseVideoJob(await post(`/video/jobs/${encodeURIComponent(jobId)}/${action}`))
  },
  async retry(jobId: string, shotId: string, mode: 'same_setup' | 'new_variation'): Promise<VideoJob> {
    return parseVideoJob(await post(`/video/jobs/${encodeURIComponent(jobId)}/shots/${encodeURIComponent(shotId)}/retry`, { mode }))
  },
  async chainReference(jobId: string, shotId: string, sourceShotId: string): Promise<VideoJob> {
    return parseVideoJob(await post(`/video/jobs/${encodeURIComponent(jobId)}/shots/${encodeURIComponent(shotId)}/chain-reference`, { sourceShotId }))
  },
  async review(jobId: string, shotId: string, decision: 'accepted' | 'rejected'): Promise<VideoJob> {
    return parseVideoJob(await post(`/video/jobs/${encodeURIComponent(jobId)}/shots/${encodeURIComponent(shotId)}/review`, { decision }))
  },
  async doctor(gcpProjectId: string, gcsBucket: string): Promise<VideoDoctorResult> {
    const item = record(await post('/video/doctor', { gcpProjectId, gcsBucket, region: 'us-central1' }), 'doctor')
    return {
      ok: booleanValue(item.ok, 'doctor ok'),
      networkContacted: booleanValue(item.networkContacted, 'networkContacted'),
      generationSubmitted: false,
      checks: array(item.checks, 'doctor checks').map((raw, index) => {
        const check = record(raw, `doctor check ${index}`)
        return {
          id: text(check.id, 'doctor check id'),
          status: enumValue(check.status, ['pass', 'fail', 'unknown'] as const, 'doctor status'),
          code: text(check.code, 'doctor code'),
          detail: text(check.detail, 'doctor detail'),
        }
      }),
    }
  },
  openOutput(jobId: string): Promise<unknown> {
    return post(`/video/jobs/${encodeURIComponent(jobId)}/open`)
  },
}
