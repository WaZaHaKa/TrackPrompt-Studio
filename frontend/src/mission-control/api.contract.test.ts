import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMissionControlClient, parseRenderJob } from './api'
import { testProfile, testProject, testScene } from './testFixtures'

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function requestBody(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || !('body' in value) || typeof value.body !== 'string') {
    throw new Error('Expected a JSON request body.')
  }
  const parsed: unknown = JSON.parse(value.body)
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('Expected a JSON object body.')
  return parsed as Record<string, unknown>
}

afterEach(() => vi.unstubAllGlobals())

describe('Mission Control API contract', () => {
  it('reconstructs resumable progress from the camel-case persisted job contract', () => {
    const job = parseRenderJob({
      id: 'job-paused',
      renderer: 'fake',
      state: 'PAUSED_SAFELY',
      phase: 'PUBLISH_CHUNK',
      identity: {
        projectId: testProject.id,
        sceneId: testScene.id,
        sceneSha256: testScene.sha256,
        profileId: testProfile.id,
        profileSha256: testProfile.savedFileSha256,
        outputDirectory: 'C:\\renders',
      },
      createdAt: '2026-07-21T08:00:00Z',
      updatedAt: '2026-07-21T10:00:00Z',
      frameStart: 1,
      frameEnd: 13029,
      renderedFrameCount: 600,
      inflightFrameCount: 0,
      validatedFrameCount: 600,
      publishedFrameCount: 600,
      totalFrameCount: 13029,
      chunksCompleted: 1,
      chunksTotal: 22,
      estimatedCompletionTime: '2026-07-21T15:00:00Z',
      safeStopStatus: 'paused',
    })

    expect(job).toMatchObject({
      state: 'paused_safely',
      renderedFrames: 600,
      validatedFrames: 600,
      publishedFrames: 600,
      totalFrames: 13029,
      canResume: true,
      estimatedCompletionAt: '2026-07-21T15:00:00Z',
    })
  })

  it('maps saved profile hours, storage, resolution, and authorization from the backend model', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response([{
      id: testProfile.id,
      projectId: testProject.id,
      sceneId: testScene.id,
      displayName: testProfile.displayName,
      path: testProfile.path,
      savedFileSha256: testProfile.savedFileSha256,
      sceneSha256: testScene.sha256,
      resolution: { width: 1280, height: 720, label: '720p' },
      fps: 30,
      frameStart: 1,
      frameEnd: 13029,
      totalFrames: 13029,
      framesPerChunk: 600,
      expectedHours: 5.045,
      conservativeHours: 5.891,
      plannedFrameSequenceGib: 10,
      minimumLaunchFreeGib: 24,
      qualityRole: 'recommended',
      qualityVerdict: 'PASS WITH DOCUMENTED CAVEAT',
      calibrated: true,
      authorizationStatus: 'AUTHORIZATION_REQUIRED',
      authorized: false,
      authorizationIssues: ['No local record'],
      recommended: true,
    }]))
    vi.stubGlobal('fetch', fetchMock)

    const [profile] = await createMissionControlClient('/api/mission-control').listProfiles()

    expect(profile).toMatchObject({
      width: 1280,
      height: 720,
      expectedSeconds: 5.045 * 3600,
      conservativeSeconds: 5.891 * 3600,
      storageGiB: 10,
      minimumFreeGiB: 24,
      authorizationStatus: 'required',
    })
  })

  it('sends exact output, preflight, authorization, and fake dry-run fields', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ path: 'C:\\renders', exists: true, usable: true, classification: 'empty_directory', entries: [], conflictingEntries: [], issues: [] }))
      .mockResolvedValueOnce(response({ ready: false, authorizationRequired: true, identity: { projectId: testProject.id, sceneId: testScene.id, sceneSha256: testScene.sha256, profileId: testProfile.id, profileSha256: testProfile.savedFileSha256, outputDirectory: 'C:\\renders' }, checks: [] }))
      .mockResolvedValueOnce(response({ authorized: true, profileId: testProfile.id, sceneId: testScene.id, profileSha256: testProfile.savedFileSha256, sceneSha256: testScene.sha256, authorizationToken: 'token', tokenSha256: 'B'.repeat(64), recordPath: 'record.json', authorizedAt: '2026-07-21T10:00:00Z' }))
      .mockResolvedValueOnce(response({ ok: true, identity: { projectId: testProject.id, sceneId: testScene.id, sceneSha256: testScene.sha256, profileId: testProfile.id, profileSha256: testProfile.savedFileSha256, outputDirectory: 'C:\\renders' }, plan: { renderer: 'fake' }, logLines: ['No process started.'] }))
    vi.stubGlobal('fetch', fetchMock)
    const client = createMissionControlClient('/api/mission-control')
    const selection = { projectId: testProject.id, sceneId: testScene.id, profileId: testProfile.id, outputPath: 'C:\\renders' }

    await client.inspectOutput(selection)
    await client.preflight(selection, 'fake')
    await client.authorize({ ...selection, expectedSeconds: 100, totalFrames: 13029, storageGiB: 10, exactOperation: 'Full production render' }, { configurationReviewed: true, fullRenderApproved: true })
    const dryRun = await client.dryRun({ ...selection, authorizationId: null, performanceMode: false, renderer: 'fake' })

    const bodies = fetchMock.mock.calls.map((call) => requestBody(call[1]))
    expect(bodies[0]).toEqual({ path: 'C:\\renders', profile_id: testProfile.id, scene_id: testScene.id })
    expect(bodies[1]).toEqual({ project_id: testProject.id, scene_id: testScene.id, profile_id: testProfile.id, output_directory: 'C:\\renders', renderer: 'fake' })
    expect(bodies[2]).toEqual({ scene_id: testScene.id, settings_and_hashes_reviewed: true, production_render_authorized: true })
    expect(bodies[3]).toEqual({ project_id: testProject.id, scene_id: testScene.id, profile_id: testProfile.id, output_directory: 'C:\\renders', renderer: 'fake' })
    expect(dryRun).toMatchObject({ ok: true, outputPath: 'C:\\renders', plan: { renderer: 'fake' } })
  })

  it('replays an exact identity when resuming and confirms cancel-stop', async () => {
    const job = {
      id: 'job-test',
      renderer: 'fake',
      state: 'RESUMABLE',
      identity: { projectId: testProject.id, sceneId: testScene.id, sceneSha256: testScene.sha256, profileId: testProfile.id, profileSha256: testProfile.savedFileSha256, outputDirectory: 'C:\\renders' },
      createdAt: '2026-07-21T08:00:00Z',
      updatedAt: '2026-07-21T10:00:00Z',
      frameStart: 1,
      frameEnd: 13029,
      renderedFrameCount: 600,
      inflightFrameCount: 0,
      validatedFrameCount: 600,
      publishedFrameCount: 600,
      totalFrameCount: 13029,
      chunksCompleted: 1,
      chunksTotal: 22,
      safeStopStatus: 'paused',
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(job))
      .mockResolvedValueOnce(response({ ...job, state: 'RUNNING', safeStopStatus: 'none' }))
      .mockResolvedValueOnce(response({ ...job, state: 'RUNNING', safeStopStatus: 'none' }))
    vi.stubGlobal('fetch', fetchMock)
    const client = createMissionControlClient('/api/mission-control')

    await client.resumeRender('job-test')
    await client.cancelStopRequest('job-test')

    expect(requestBody(fetchMock.mock.calls[1]?.[1])).toEqual({
      scene_sha256: testScene.sha256,
      profile_sha256: testProfile.savedFileSha256,
    })
    expect(requestBody(fetchMock.mock.calls[2]?.[1])).toEqual({ operator_confirmed: true })
  })
})
