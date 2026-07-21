import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRenderEventSubscriber } from './events'

interface FakeSource {
  onopen: ((event: Event) => void) | null
  onmessage: ((event: MessageEvent) => void) | null
  onerror: ((event: Event) => void) | null
  close: ReturnType<typeof vi.fn>
  listeners: Map<string, (event: Event) => void>
}

function fakeSource(): FakeSource {
  const listeners = new Map<string, (event: Event) => void>()
  return {
    onopen: null,
    onmessage: null,
    onerror: null,
    close: vi.fn(),
    listeners,
  }
}

function asEventSource(source: FakeSource): EventSource {
  return Object.assign(source, {
    addEventListener: (type: string, listener: EventListenerOrEventListenerObject) => {
      listenersSet(source, type, listener)
    },
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
    url: '',
    withCredentials: true,
    readyState: 1,
    CONNECTING: 0,
    OPEN: 1,
    CLOSED: 2,
  }) as EventSource
}

function listenersSet(source: FakeSource, type: string, listener: EventListenerOrEventListenerObject): void {
  source.listeners.set(type, (event) => {
    if (typeof listener === 'function') listener(event)
    else listener.handleEvent(event)
  })
}

afterEach(() => {
  vi.useRealTimers()
  window.localStorage.clear()
})

describe('render event subscriber', () => {
  it('uses replay query aliases, ignores duplicate sequences, and reconnects from the last event', async () => {
    vi.useFakeTimers()
    const sources: FakeSource[] = []
    const urls: string[] = []
    const factory = (url: string): EventSource => {
      urls.push(url)
      const source = fakeSource()
      sources.push(source)
      return asEventSource(source)
    }
    const onEvent = vi.fn()
    const onConnection = vi.fn()
    const subscription = createRenderEventSubscriber('/api/mission-control', factory).subscribe({
      jobId: 'job-test',
      afterSequence: 4,
      onConnection,
      onEvent,
      onError: vi.fn(),
    })

    expect(urls[0]).toContain('jobId=job-test')
    expect(urls[0]).toContain('afterSequence=4')
    sources[0]?.onopen?.(new Event('open'))
    const event = new MessageEvent('render', { data: JSON.stringify({
      schemaVersion: '1.0.0',
      sequence: 5,
      timestamp: '2026-07-21T10:00:00Z',
      jobId: 'job-test',
      projectId: 'project',
      state: 'RUNNING',
      phase: 'RENDER_FRAME',
      sceneId: 'scene',
      sceneSha256: 'A'.repeat(64),
      profileId: 'profile',
      profileSha256: 'B'.repeat(64),
      frameStart: 1,
      frameEnd: 10,
      currentFrame: 5,
      renderedFrameCount: 5,
      inflightFrameCount: 1,
      validatedFrameCount: 4,
      publishedFrameCount: 4,
      totalFrameCount: 10,
      chunksCompleted: 0,
      chunksTotal: 2,
      safeStopStatus: 'none',
    }) })
    sources[0]?.listeners.get('render')?.(event)
    sources[0]?.listeners.get('render')?.(event)
    expect(onEvent).toHaveBeenCalledTimes(1)
    expect(subscription.getLastSequence()).toBe(5)

    sources[0]?.onerror?.(new Event('error'))
    expect(onConnection).toHaveBeenLastCalledWith('reconnecting')
    await vi.advanceTimersByTimeAsync(751)
    expect(urls[1]).toContain('afterSequence=5')
    subscription.close()
  })
})
