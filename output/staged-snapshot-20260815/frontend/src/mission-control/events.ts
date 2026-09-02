import { MISSION_CONTROL_API_BASE, parseRenderEvent, parseStructuredError } from './api'
import type { RenderEventSubscriber, RenderEventSubscription, StructuredError } from './types'

export type EventSourceFactory = (url: string) => EventSource

interface StoredSequence {
  jobId: string
  sequence: number
}

const SEQUENCE_KEY = 'wzhk.mission-control.last-event'

export function readStoredEventSequence(jobId: string): number {
  try {
    const raw = window.localStorage.getItem(SEQUENCE_KEY)
    if (!raw) return 0
    const parsed: unknown = JSON.parse(raw)
    if (
      typeof parsed === 'object' && parsed !== null &&
      'jobId' in parsed && parsed.jobId === jobId &&
      'sequence' in parsed && typeof parsed.sequence === 'number' && Number.isFinite(parsed.sequence)
    ) return parsed.sequence
  } catch {
    // A corrupt preference must never prevent reconnecting to the authoritative job.
  }
  return 0
}

export function storeEventSequence(value: StoredSequence): void {
  try {
    window.localStorage.setItem(SEQUENCE_KEY, JSON.stringify(value))
  } catch {
    // Private browsing and storage quotas can disable persistence; live events still work.
  }
}

export function createRenderEventSubscriber(
  baseUrl = MISSION_CONTROL_API_BASE,
  eventSourceFactory: EventSourceFactory = (url) => new EventSource(url, { withCredentials: true }),
): RenderEventSubscriber {
  return {
    subscribe(options): RenderEventSubscription {
      let closed = false
      let source: EventSource | null = null
      let retryTimer: number | undefined
      let retries = 0
      let lastSequence = Math.max(options.afterSequence, readStoredEventSequence(options.jobId))

      const reportTransportError = (summary: string, technicalDetails?: string): void => {
        const error: StructuredError = parseStructuredError({
          code: 'event_stream_error',
          title: 'Live updates interrupted',
          summary,
          likely_cause: 'The local service may be restarting or the computer may have resumed from sleep.',
          recommended_action: 'Keep this page open. Mission Control will reconnect automatically.',
          retryable: true,
          technical_details: technicalDetails,
          job_id: options.jobId,
        })
        options.onError(error)
      }

      const handlePayload = (payload: string): void => {
        try {
          const value: unknown = JSON.parse(payload)
          const event = parseRenderEvent(value)
          if (!event.jobId || event.jobId !== options.jobId || event.sequence <= lastSequence) return
          lastSequence = event.sequence
          storeEventSequence({ jobId: options.jobId, sequence: lastSequence })
          options.onEvent(event)
        } catch (error) {
          reportTransportError(
            'A live update could not be read. The renderer is unaffected.',
            error instanceof Error ? error.message : undefined,
          )
        }
      }

      const scheduleReconnect = (): void => {
        if (closed || retryTimer !== undefined) return
        retries += 1
        options.onConnection(retries >= 4 || navigator.onLine === false ? 'offline' : 'reconnecting')
        const delay = Math.min(10_000, 750 * (2 ** Math.min(retries - 1, 4)))
        retryTimer = window.setTimeout(() => {
          retryTimer = undefined
          connect()
        }, delay)
      }

      const connect = (): void => {
        if (closed) return
        source?.close()
        const query = new URLSearchParams({
          jobId: options.jobId,
          afterSequence: String(lastSequence),
        })
        source = eventSourceFactory(`${baseUrl}/events?${query.toString()}`)
        source.onopen = () => {
          retries = 0
          options.onConnection('connected')
        }
        source.onmessage = (event) => {
          if (typeof event.data === 'string') handlePayload(event.data)
        }
        for (const eventName of ['render', 'render_event', 'heartbeat']) {
          source.addEventListener(eventName, (event) => {
            if (event instanceof MessageEvent && typeof event.data === 'string') handlePayload(event.data)
          })
        }
        source.onerror = () => {
          source?.close()
          source = null
          reportTransportError('Live updates are reconnecting. Your render continues in the local service.')
          scheduleReconnect()
        }
      }

      const handleOnline = (): void => {
        if (closed || source) return
        if (retryTimer !== undefined) window.clearTimeout(retryTimer)
        retryTimer = undefined
        retries = 0
        options.onConnection('reconnecting')
        connect()
      }

      const handleOffline = (): void => options.onConnection('offline')
      window.addEventListener('online', handleOnline)
      window.addEventListener('offline', handleOffline)
      options.onConnection('reconnecting')
      connect()

      return {
        close: () => {
          closed = true
          source?.close()
          source = null
          if (retryTimer !== undefined) window.clearTimeout(retryTimer)
          window.removeEventListener('online', handleOnline)
          window.removeEventListener('offline', handleOffline)
        },
        getLastSequence: () => lastSequence,
      }
    },
  }
}
