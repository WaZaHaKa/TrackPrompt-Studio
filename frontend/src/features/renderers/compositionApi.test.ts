import { afterEach, describe, expect, it, vi } from 'vitest'

import { getWzhkSpectrumWorkspace } from '../../api'

const jobId = '11111111-1111-4111-8111-111111111111'
const workspace = {
  schemaVersion: '4.0.0',
  rendererId: 'wzhk-spectrum',
  jobId,
  state: 'COMPLETE',
  workspaceRelativePath: `wzhk-spectrum/jobs/${jobId}`,
  contractValid: true,
  brandingApplied: true,
  vendorUnchanged: true,
  generatedWorkspaceHash: 'a'.repeat(64),
  vendorSourceHash: 'b'.repeat(64),
  vendorCommit: '553aa755ef0cc394259fb1a55560f1b31864d2e0',
  logoResolved: true,
  masterAudioResolved: true,
  mode: 'production',
  backgroundMode: 'generative-geometry',
  visualQaRequired: true,
  contractSummary: {
    artist: 'DJ WaZaHaKa', title: 'Scattered', bpm: 120, meter: '4/4',
    totalBars: 96, gridDurationSeconds: 192, masterDurationSeconds: 196.619796,
    tailDurationSeconds: 4.619796, width: 1920, height: 1080, fps: 60,
  },
}

function respond(compositionRevision: unknown): void {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    ...workspace, compositionRevision,
  }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
}

describe('Spectrum composition revision decoding', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('preserves the new revision without promoting automated completion to aesthetic approval', async () => {
    respond('scattered-geometry-first-3.7')
    const result = await getWzhkSpectrumWorkspace(jobId)
    expect(result.compositionRevision).toBe('scattered-geometry-first-3.7')
    expect(result.state).toBe('COMPLETE')
    expect(result.visualQaRequired).toBe(true)
  })

  it.each([undefined, null])('keeps legacy workspaces readable with revision %s', async (revision) => {
    respond(revision)
    expect((await getWzhkSpectrumWorkspace(jobId)).compositionRevision).toBeNull()
  })

  it.each(['unknown-revision', 3.7, true, {}])('rejects an invalid composition revision %s', async (revision) => {
    respond(revision)
    await expect(getWzhkSpectrumWorkspace(jobId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
