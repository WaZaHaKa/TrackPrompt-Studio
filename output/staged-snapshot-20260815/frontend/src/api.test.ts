import { afterEach, describe, expect, it, vi } from 'vitest'

import { getCapabilities } from './api'

describe('capability decoding', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('preserves the advertised visual cue and Blender contract', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      fastMode: { available: true, features: ['rhythm'] },
      deepMode: { available: false, willFallback: true, adapters: [] },
      ffmpeg: { available: true },
      ffprobe: { available: true },
      limits: {
        maxUploadMb: 200,
        maxDurationSeconds: 1200,
        jobTtlMinutes: 60,
        maxPendingJobs: 2,
      },
      visualCueExportAvailable: true,
      visualCueSheetSchemaVersion: '1.1.0',
      visualFeatureArtifactSchemaVersion: '1.0.0',
      blenderVisualizerPreset: 'abstract-geometry',
      networkFeaturesEnabled: false,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })))

    const capabilities = await getCapabilities()

    expect(capabilities.visualCueExportAvailable).toBe(true)
    expect(capabilities.visualCueSheetSchemaVersion).toBe('1.1.0')
    expect(capabilities.visualFeatureArtifactSchemaVersion).toBe('1.0.0')
    expect(capabilities.blenderVisualizerPreset).toBe('abstract-geometry')
  })
})
