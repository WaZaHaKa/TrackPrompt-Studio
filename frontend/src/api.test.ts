import { afterEach, describe, expect, it, vi } from 'vitest'

import { getCapabilities, resolveVisualizerConfig } from './api'
import { cloneDefaultSpaceJourneyParameters } from './visualizer'

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('capability decoding', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('preserves the legacy field and decodes the additive visualizer contract', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      fastMode: { available: true, features: ['rhythm'] },
      deepMode: { available: false, willFallback: true, adapters: [] },
      ffmpeg: { available: true },
      ffprobe: { available: true },
      limits: {
        maxUploadMb: 200,
        maxDurationSeconds: 1200,
        maxPendingJobs: 2,
      },
      visualCueExportAvailable: true,
      visualCueSheetSchemaVersion: '1.1.0',
      visualFeatureArtifactSchemaVersion: '1.0.0',
      blenderVisualizerPreset: 'abstract-geometry',
      blenderVisualizerDefaultPreset: 'abstract-geometry',
      blenderVisualizerPresets: ['space-journey', 'unsupported-future-preset'],
      blenderVisualizerConfigSchemaVersion: '1.0.0',
      networkFeaturesEnabled: false,
      retentionPolicy: 'explicit-delete-only',
      automaticAnalysisDeletionEnabled: false,
    })))

    const capabilities = await getCapabilities()

    expect(capabilities.visualCueExportAvailable).toBe(true)
    expect(capabilities.visualCueSheetSchemaVersion).toBe('1.1.0')
    expect(capabilities.visualFeatureArtifactSchemaVersion).toBe('1.0.0')
    expect(capabilities.blenderVisualizerPreset).toBe('abstract-geometry')
    expect(capabilities.blenderVisualizerDefaultPreset).toBe('abstract-geometry')
    expect(capabilities.blenderVisualizerPresets).toEqual(['abstract-geometry', 'space-journey'])
    expect(capabilities.blenderVisualizerConfigSchemaVersion).toBe('1.0.0')
    expect(capabilities.retentionPolicy).toBe('explicit-delete-only')
    expect(capabilities.automaticAnalysisDeletionEnabled).toBe(false)
  })

  it('falls back to Abstract Geometry for a legacy capability response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      fastMode: { available: true, features: [] },
      deepMode: { available: false, willFallback: true, adapters: [] },
      ffmpeg: { available: true },
      ffprobe: { available: true },
      limits: {},
      blenderVisualizerPreset: 'abstract-geometry',
    })))

    const capabilities = await getCapabilities()

    expect(capabilities.blenderVisualizerDefaultPreset).toBe('abstract-geometry')
    expect(capabilities.blenderVisualizerPresets).toEqual(['abstract-geometry'])
    expect(capabilities.blenderVisualizerConfigSchemaVersion).toBe('unknown')
  })
})

describe('visualizer configuration resolution', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts a typed request and decodes the fully resolved Space Journey response', async () => {
    const parameters = cloneDefaultSpaceJourneyParameters()
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      schemaVersion: '1.0.0',
      preset: 'space-journey',
      parameters,
      seed: 84291,
      defaultedParameters: ['fogDepth'],
      warnings: ['Preview fog was kept within the supported range.'],
    }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await resolveVisualizerConfig({
      schemaVersion: '1.0.0',
      preset: 'space-journey',
      parameters: { cameraDistance: 18 },
      seed: 84291,
    })

    expect(fetchMock).toHaveBeenCalledWith('/api/visualizer/config/resolve', expect.objectContaining({
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        schemaVersion: '1.0.0',
        preset: 'space-journey',
        parameters: { cameraDistance: 18 },
        seed: 84291,
      }),
    }))
    expect(result).toEqual({
      schemaVersion: '1.0.0',
      preset: 'space-journey',
      parameters,
      seed: 84291,
      defaultedParameters: ['fogDepth'],
      warnings: ['Preview fog was kept within the supported range.'],
    })
  })

  it('rejects malformed or out-of-range resolved values', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      schemaVersion: '1.0.0',
      preset: 'space-journey',
      parameters: { ...cloneDefaultSpaceJourneyParameters(), cameraDistance: 100 },
      seed: 84291,
      defaultedParameters: [],
      warnings: [],
    })))

    await expect(resolveVisualizerConfig({
      schemaVersion: '1.0.0',
      preset: 'space-journey',
    })).rejects.toMatchObject({
      code: 'invalid_response',
      message: 'The server returned out-of-range visualizer parameters.',
    })
  })

  it.each([
    {
      response: {
        schemaVersion: '1.0.0',
        preset: 'abstract-geometry',
        parameters: {},
        seed: 84291,
        defaultedParameters: [],
        warnings: [],
      },
      message: 'The server returned a different visualizer preset than requested.',
    },
    {
      response: {
        schemaVersion: '1.0.0',
        preset: 'space-journey',
        parameters: cloneDefaultSpaceJourneyParameters(),
        seed: 7,
        defaultedParameters: [],
        warnings: [],
      },
      message: 'The server returned a different visualizer seed than requested.',
    },
  ])('rejects a resolved identity that differs from the request', async ({ response, message }) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(response)))

    await expect(resolveVisualizerConfig({
      schemaVersion: '1.0.0',
      preset: 'space-journey',
      seed: 84291,
    })).rejects.toMatchObject({ code: 'invalid_response', message })
  })

  it('rejects a resolved explicit parameter that differs from the request', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      schemaVersion: '1.0.0',
      preset: 'space-journey',
      parameters: { ...cloneDefaultSpaceJourneyParameters(), cameraDistance: 19 },
      seed: 84291,
      defaultedParameters: [],
      warnings: [],
    })))

    await expect(resolveVisualizerConfig({
      schemaVersion: '1.0.0',
      preset: 'space-journey',
      parameters: { cameraDistance: 18 },
      seed: 84291,
    })).rejects.toMatchObject({
      code: 'invalid_response',
      message: 'The server returned a different cameraDistance parameter than requested.',
    })
  })
})
