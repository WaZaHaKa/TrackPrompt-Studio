import {
  MISSION_CONTROL_API_BASE,
  MissionControlApiError,
  parseStructuredError,
} from './api'

export interface LocalVideoError {
  code: string
  summary: string
  action: string | null
  retryable: boolean
}

export interface LocalVideoProjectSummary {
  projectId: string
  title: string
  status: string
  currentRevisionId: string | null
  updatedAt: string | null
  audioHashPrefix: string | null
  durationSeconds: number | null
  selectedTier: string | null
}

export interface LocalVideoReadiness {
  providerId: 'local-comfyui'
  configured: boolean
  reachable: boolean
  localEndpoint: string
  comfyuiVersion: string | null
  nodeCount: number
  devices: Array<{
    name: string
    type: string
    vramTotalBytes: number | null
    vramFreeBytes: number | null
  }>
  missingNodeRoles: string[]
  discoveredModelNames: string[]
  setupRequired: boolean
  localApiContacted: boolean
  providerState: string
  qualificationState: string
  ggufNodeAvailable: boolean
  fluxWorkflowAvailable: boolean
  wanWorkflowAvailable: boolean
  modelsAvailable: boolean
  qualificationCompleted: boolean
  selectedTier: string | null
  postProcessingReady: boolean
  productionReady: boolean
  statusMessage: string
  error: LocalVideoError | null
}

export interface LocalVideoWorkflow {
  schemaVersion: '1.0.0'
  workflowId: string
  capability: string
  workflowSha256: string
  semanticRoles: Record<string, string[]>
  missingRoles: string[]
  sourceUrl: string | null
  sourceRevision: string | null
  installedAt: string
}

export interface LocalVideoQualification {
  cacheKey: string
  selectedTier: string | null
  cached: boolean
  completedAt: string | null
  candidates: Array<{
    tier: string
    state: 'pending' | 'running' | 'passed' | 'failed' | 'skipped'
    reason: string | null
    peakVramBytes: number | null
    peakSystemMemoryBytes: number | null
    elapsedSeconds: number | null
  }>
  error: LocalVideoError | null
}

export interface LocalVideoProject {
  projectId: string
  title: string
  revisionId: string | null
  packageDigest: string | null
  audioHashPrefix: string | null
  audioDurationSeconds: number | null
  stage: string
  statusMessage: string
  completedUnits: number
  totalUnits: number
  elapsedSeconds: number
  etaSeconds: number | null
  currentShotId: string | null
  provider: LocalVideoReadiness | null
  qualification: LocalVideoQualification | null
  timeline: Array<{
    shotId: string
    order: number
    startSeconds: number
    endSeconds: number
    durationSeconds: number
    boundarySource: string
  }>
  shots: Array<{
    shotId: string
    order: number
    state: string
    stage: string
    progress: number | null
    attempt: number
    alternate: boolean
    outputSha256: string | null
    error: LocalVideoError | null
  }>
  analysisArchived: boolean
  canStart: boolean
  canResume: boolean
  canCancel: boolean
  finalQcPassed: boolean
  outputAvailable: boolean
  error: LocalVideoError | null
  updatedAt: string
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error(`${label} must be an object`)
  return value as Record<string, unknown>
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

function nullableText(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function parseError(value: unknown): LocalVideoError | null {
  if (value === null || value === undefined) return null
  const item = object(value, 'local video error')
  return {
    code: text(item.code, 'error code'),
    summary: text(item.summary, 'error summary'),
    action: nullableText(item.action),
    retryable: booleanValue(item.retryable, 'error retryable'),
  }
}

export function parseLocalVideoReadiness(value: unknown): LocalVideoReadiness {
  const item = object(value, 'local provider readiness')
  const provider = text(item.providerId, 'provider ID')
  if (provider !== 'local-comfyui') throw new Error('local provider ID is unsupported')
  return {
    providerId: provider,
    configured: booleanValue(item.configured, 'provider configured'),
    reachable: booleanValue(item.reachable, 'provider reachable'),
    localEndpoint: text(item.localEndpoint, 'local endpoint'),
    comfyuiVersion: nullableText(item.comfyuiVersion),
    nodeCount: numberValue(item.nodeCount, 'node count'),
    devices: array(item.devices, 'provider devices').map((raw) => {
      const device = object(raw, 'provider device')
      return {
        name: text(device.name, 'device name'),
        type: text(device.type, 'device type'),
        vramTotalBytes: nullableNumber(device.vramTotalBytes),
        vramFreeBytes: nullableNumber(device.vramFreeBytes),
      }
    }),
    missingNodeRoles: array(item.missingNodeRoles, 'missing node roles').map((entry) => text(entry, 'node role')),
    discoveredModelNames: array(item.discoveredModelNames, 'model names').map((entry) => text(entry, 'model name')),
    setupRequired: booleanValue(item.setupRequired, 'setup required'),
    localApiContacted: booleanValue(item.localApiContacted, 'local API contacted'),
    providerState: item.providerState === undefined ? 'comfyui_missing' : text(item.providerState, 'provider state'),
    qualificationState: item.qualificationState === undefined ? 'qualification_not_run' : text(item.qualificationState, 'qualification state'),
    ggufNodeAvailable: item.ggufNodeAvailable === undefined ? false : booleanValue(item.ggufNodeAvailable, 'GGUF node available'),
    fluxWorkflowAvailable: item.fluxWorkflowAvailable === undefined ? false : booleanValue(item.fluxWorkflowAvailable, 'FLUX workflow available'),
    wanWorkflowAvailable: item.wanWorkflowAvailable === undefined ? false : booleanValue(item.wanWorkflowAvailable, 'Wan workflow available'),
    modelsAvailable: item.modelsAvailable === undefined ? false : booleanValue(item.modelsAvailable, 'models available'),
    qualificationCompleted: item.qualificationCompleted === undefined ? false : booleanValue(item.qualificationCompleted, 'qualification completed'),
    selectedTier: nullableText(item.selectedTier),
    postProcessingReady: item.postProcessingReady === undefined ? false : booleanValue(item.postProcessingReady, 'post-processing ready'),
    productionReady: item.productionReady === undefined ? false : booleanValue(item.productionReady, 'production ready'),
    statusMessage: item.statusMessage === undefined ? 'Local ComfyUI is not ready' : text(item.statusMessage, 'provider status message'),
    error: parseError(item.error),
  }
}

function parseSummary(value: unknown): LocalVideoProjectSummary {
  const item = object(value, 'local project summary')
  return {
    projectId: text(item.projectId, 'project ID'),
    title: text(item.title, 'project title'),
    status: text(item.status, 'project status'),
    currentRevisionId: nullableText(item.currentRevisionId),
    updatedAt: nullableText(item.updatedAt),
    audioHashPrefix: nullableText(item.audioHashPrefix),
    durationSeconds: nullableNumber(item.durationSeconds),
    selectedTier: nullableText(item.selectedTier),
  }
}

function parseQualification(value: unknown): LocalVideoQualification {
  const item = object(value, 'local qualification')
  return {
    cacheKey: text(item.cacheKey, 'qualification cache key'),
    selectedTier: nullableText(item.selectedTier),
    cached: booleanValue(item.cached, 'qualification cached'),
    completedAt: nullableText(item.completedAt),
    candidates: array(item.candidates, 'qualification candidates').map((raw) => {
      const candidate = object(raw, 'qualification candidate')
      const state = text(candidate.state, 'candidate state') as LocalVideoQualification['candidates'][number]['state']
      if (!['pending', 'running', 'passed', 'failed', 'skipped'].includes(state)) throw new Error('candidate state is unsupported')
      return {
        tier: text(candidate.tier, 'candidate tier'),
        state,
        reason: nullableText(candidate.reason),
        peakVramBytes: nullableNumber(candidate.peakVramBytes),
        peakSystemMemoryBytes: nullableNumber(candidate.peakSystemMemoryBytes),
        elapsedSeconds: nullableNumber(candidate.elapsedSeconds),
      }
    }),
    error: parseError(item.error),
  }
}

function parseProject(value: unknown): LocalVideoProject {
  const item = object(value, 'local video project')
  return {
    projectId: text(item.projectId, 'project ID'),
    title: text(item.title, 'project title'),
    revisionId: nullableText(item.revisionId),
    packageDigest: nullableText(item.packageDigest),
    audioHashPrefix: nullableText(item.audioHashPrefix),
    audioDurationSeconds: nullableNumber(item.audioDurationSeconds),
    stage: text(item.stage, 'project stage'),
    statusMessage: text(item.statusMessage, 'status message'),
    completedUnits: numberValue(item.completedUnits, 'completed units'),
    totalUnits: numberValue(item.totalUnits, 'total units'),
    elapsedSeconds: numberValue(item.elapsedSeconds, 'elapsed seconds'),
    etaSeconds: nullableNumber(item.etaSeconds),
    currentShotId: nullableText(item.currentShotId),
    provider: item.provider === null ? null : parseLocalVideoReadiness(item.provider),
    qualification: item.qualification === null ? null : parseQualification(item.qualification),
    timeline: array(item.timeline, 'timeline').map((raw) => {
      const scene = object(raw, 'timeline scene')
      return {
        shotId: text(scene.shotId, 'shot ID'),
        order: numberValue(scene.order, 'shot order'),
        startSeconds: numberValue(scene.startSeconds, 'shot start'),
        endSeconds: numberValue(scene.endSeconds, 'shot end'),
        durationSeconds: numberValue(scene.durationSeconds, 'shot duration'),
        boundarySource: text(scene.boundarySource, 'boundary source'),
      }
    }),
    shots: array(item.shots, 'shots').map((raw) => {
      const shot = object(raw, 'shot')
      return {
        shotId: text(shot.shotId, 'shot ID'),
        order: numberValue(shot.order, 'shot order'),
        state: text(shot.state, 'shot state'),
        stage: text(shot.stage, 'shot stage'),
        progress: nullableNumber(shot.progress),
        attempt: numberValue(shot.attempt, 'shot attempt'),
        alternate: booleanValue(shot.alternate, 'shot alternate'),
        outputSha256: nullableText(shot.outputSha256),
        error: parseError(shot.error),
      }
    }),
    analysisArchived: booleanValue(item.analysisArchived, 'analysis archived'),
    canStart: booleanValue(item.canStart, 'can start'),
    canResume: booleanValue(item.canResume, 'can resume'),
    canCancel: booleanValue(item.canCancel, 'can cancel'),
    finalQcPassed: booleanValue(item.finalQcPassed, 'final QC passed'),
    outputAvailable: booleanValue(item.outputAvailable, 'output available'),
    error: parseError(item.error),
    updatedAt: text(item.updatedAt, 'updated at'),
  }
}

function parseWorkflow(value: unknown): LocalVideoWorkflow {
  const item = object(value, 'local workflow')
  return {
    schemaVersion: '1.0.0',
    workflowId: text(item.workflowId, 'workflow ID'),
    capability: text(item.capability, 'workflow capability'),
    workflowSha256: text(item.workflowSha256, 'workflow hash'),
    semanticRoles: Object.fromEntries(Object.entries(object(item.semanticRoles, 'semantic roles')).map(([key, raw]) => [
      key,
      array(raw, `semantic role ${key}`).map((entry) => text(entry, 'semantic node ID')),
    ])),
    missingRoles: array(item.missingRoles, 'missing roles').map((entry) => text(entry, 'missing role')),
    sourceUrl: nullableText(item.sourceUrl),
    sourceRevision: nullableText(item.sourceRevision),
    installedAt: text(item.installedAt, 'workflow installed at'),
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
    throw new MissionControlApiError(parseStructuredError(error, 'Local ComfyUI service is unavailable'))
  }
  const contentType = response.headers.get('content-type') ?? ''
  const body: unknown = response.status === 204
    ? null
    : contentType.includes('application/json')
      ? await response.json().catch(() => null) as unknown
      : await response.text().catch(() => '')
  if (!response.ok) throw new MissionControlApiError(parseStructuredError(body, 'Local video action failed'), response.status)
  return body
}

const post = (path: string, body?: unknown): Promise<unknown> => request(path, {
  method: 'POST',
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const localVideoClient = {
  async projects(): Promise<LocalVideoProjectSummary[]> {
    return array(await request('/video/local/projects'), 'local video projects').map(parseSummary)
  },
  async get(projectId: string): Promise<LocalVideoProject> {
    return parseProject(await request(`/video/local/projects/${encodeURIComponent(projectId)}`))
  },
  async prepare(projectId: string): Promise<LocalVideoProject> {
    return parseProject(await post('/video/local/projects/prepare', { projectId, analysisId: null }))
  },
  async readiness(): Promise<LocalVideoReadiness> {
    return parseLocalVideoReadiness(await request('/video/local/provider/readiness'))
  },
  async workflows(): Promise<LocalVideoWorkflow[]> {
    return array(await request('/video/local/workflows'), 'local workflows').map(parseWorkflow)
  },
  async qualify(projectId: string, workflowId: string): Promise<LocalVideoQualification> {
    return parseQualification(await post(`/video/local/projects/${encodeURIComponent(projectId)}/qualify`, { workflowId }))
  },
  async importQualification(projectId: string): Promise<LocalVideoQualification> {
    return parseQualification(await post(`/video/local/projects/${encodeURIComponent(projectId)}/qualification/import`))
  },
}
