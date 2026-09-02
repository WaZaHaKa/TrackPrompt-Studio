import { describe, expect, it } from 'vitest'
import { parseLocalVideoReadiness } from './localVideoApi'

describe('local video API', () => {
  it('parses safe local readiness without accepting local paths', () => {
    const value = parseLocalVideoReadiness({
      providerId: 'local-comfyui',
      configured: true,
      reachable: true,
      localEndpoint: 'http://127.0.0.1:8188',
      comfyuiVersion: 'test',
      nodeCount: 12,
      devices: [{
        name: 'RTX 3060',
        type: 'cuda',
        vramTotalBytes: 12_884_901_888,
        vramFreeBytes: 10_000_000_000,
      }],
      missingNodeRoles: [],
      discoveredModelNames: ['wan2.2-high.gguf'],
      setupRequired: false,
      localApiContacted: true,
      error: null,
    })
    expect(value.providerId).toBe('local-comfyui')
    expect(value.devices[0]?.vramTotalBytes).toBe(12_884_901_888)
    expect(JSON.stringify(value)).not.toContain('C:\\')
  })

  it('rejects a non-local provider identity', () => {
    expect(() => parseLocalVideoReadiness({
      providerId: 'cloud', configured: true, reachable: true, localEndpoint: 'https://example.com',
      comfyuiVersion: null, nodeCount: 0, devices: [], missingNodeRoles: [],
      discoveredModelNames: [], setupRequired: false, localApiContacted: true, error: null,
    })).toThrow(/unsupported/)
  })
})
