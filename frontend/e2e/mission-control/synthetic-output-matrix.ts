/// <reference lib="dom" />

import type { Page } from '@playwright/test'

const JOB_ID = 'synthetic-output-matrix-job'
const HORIZONTAL_ID = 'horizontal-16x9-1080p'
const VERTICAL_ID = 'vertical-9x16-1080p'

export type SyntheticOutputMatrixMode = 'dual' | 'horizontal-only'

export interface SyntheticVariantProgressOverride {
  currentFrame?: number
  latestRenderedFrame?: number
  latestSafeFrame?: number
  renderedFrames?: number
  inFlightFrames?: number
  validatedFrames?: number
  publishedFrames?: number
  previewFrame?: number
  etaP50Seconds?: number
  etaP90Seconds?: number
}

export interface SyntheticEventOptions {
  sequence: number
  warning?: string | null
  activeVariantId?: typeof HORIZONTAL_ID | typeof VERTICAL_ID
  horizontal?: SyntheticVariantProgressOverride
  vertical?: SyntheticVariantProgressOverride
}

interface SyntheticVariantState {
  currentFrame: number
  latestRenderedFrame: number
  latestSafeFrame: number
  renderedFrames: number
  inFlightFrames: number
  validatedFrames: number
  publishedFrames: number
  previewFrame: number
  etaP50Seconds: number
  etaP90Seconds: number
}

export interface SyntheticOutputMatrixFixture {
  initialJob: Record<string, unknown>
  event: (options: SyntheticEventOptions) => Record<string, unknown>
}

const HORIZONTAL_INITIAL: SyntheticVariantState = {
  currentFrame: 15,
  latestRenderedFrame: 14,
  latestSafeFrame: 12,
  renderedFrames: 14,
  inFlightFrames: 2,
  validatedFrames: 12,
  publishedFrames: 12,
  previewFrame: 14,
  etaP50Seconds: 3_600,
  etaP90Seconds: 5_400,
}

const VERTICAL_INITIAL: SyntheticVariantState = {
  currentFrame: 10,
  latestRenderedFrame: 9,
  latestSafeFrame: 8,
  renderedFrames: 9,
  inFlightFrames: 1,
  validatedFrames: 8,
  publishedFrames: 8,
  previewFrame: 9,
  etaP50Seconds: 7_200,
  etaP90Seconds: 9_000,
}

function eta(p50Seconds: number, p90Seconds: number): Record<string, unknown> {
  return {
    state: 'stable',
    freshness: 'fresh',
    confidence: 'high',
    p50_remaining_seconds: p50Seconds,
    p90_remaining_seconds: p90Seconds,
    last_estimate_at: '2026-07-23T09:00:00Z',
    sample_count: 24,
  }
}

function worker(id: string, currentFrame: number): Record<string, unknown> {
  return {
    id,
    status: 'active',
    active: true,
    current_task_id: `${id}-chunk`,
    current_frame: currentFrame,
    retry_count: 0,
    failure_count: 0,
    last_heartbeat_at: '2026-07-23T09:00:00Z',
  }
}

function variant(
  id: typeof HORIZONTAL_ID | typeof VERTICAL_ID,
  enabled: boolean,
  state: SyntheticVariantState,
): Record<string, unknown> {
  const horizontal = id === HORIZONTAL_ID
  const displayName = horizontal ? 'Horizontal 16:9' : 'Vertical 9:16'
  const width = horizontal ? 1_920 : 1_080
  const height = horizontal ? 1_080 : 1_920
  const workerId = horizontal ? 'worker-horizontal' : 'worker-vertical'
  return {
    id,
    display_name: displayName,
    enabled,
    required: horizontal,
    dimensions: {
      width,
      height,
      fps: 30,
      aspect_ratio: horizontal ? '16:9' : '9:16',
    },
    deliverable_role: horizontal ? 'primary_delivery' : 'social_derivative',
    composition_profile: {
      id: horizontal ? 'andromeda-horizontal-v2' : 'andromeda-vertical-v2',
      sha256: horizontal
        ? '1111111111111111111111111111111111111111111111111111111111111111'
        : '2222222222222222222222222222222222222222222222222222222222222222',
      mode: horizontal ? 'authored-horizontal' : 'authored-vertical',
    },
    render_profile: {
      id: horizontal ? 'andromeda-horizontal-final' : 'andromeda-vertical-proof',
      sha256: horizontal
        ? '3333333333333333333333333333333333333333333333333333333333333333'
        : '4444444444444444444444444444444444444444444444444444444444444444',
    },
    state: 'running',
    phase: 'render_frame',
    progress: {
      frame_start: 1,
      frame_end: 100,
      total_frames: 100,
      current_frame: state.currentFrame,
      current_frame_started_at: '2026-07-23T09:00:00Z',
      last_output_at: '2026-07-23T09:00:01Z',
      latest_rendered_frame: state.latestRenderedFrame,
      latest_safe_frame: state.latestSafeFrame,
      rendered_frames: state.renderedFrames,
      in_flight_frames: state.inFlightFrames,
      validated_frames: state.validatedFrames,
      published_frames: state.publishedFrames,
      active_chunk_id: horizontal ? 'horizontal-0001-0020' : 'vertical-0001-0020',
      chunk_start: 1,
      chunk_end: 20,
      current_chunk_progress: state.currentFrame / 20,
      chunks_completed: 0,
      chunks_total: 5,
      preview_url: `/api/mission-control/render/${JOB_ID}/preview?output_variant_id=${id}`,
      full_frame_url: `/api/mission-control/render/${JOB_ID}/frame?output_variant_id=${id}`,
      workers: [worker(workerId, state.currentFrame)],
    },
    preview_frame: state.previewFrame,
    latest_preview_at: '2026-07-23T09:00:01Z',
    eta: eta(state.etaP50Seconds, state.etaP90Seconds),
  }
}

function buildPayload(
  mode: SyntheticOutputMatrixMode,
  options: SyntheticEventOptions,
): Record<string, unknown> {
  const horizontalDefaults = mode === 'horizontal-only'
    ? { ...HORIZONTAL_INITIAL, etaP50Seconds: 1_800, etaP90Seconds: 2_700 }
    : HORIZONTAL_INITIAL
  const horizontal = { ...horizontalDefaults, ...options.horizontal }
  const vertical = { ...VERTICAL_INITIAL, ...options.vertical }
  const dual = mode === 'dual'
  const activeVariantId = dual && options.activeVariantId === VERTICAL_ID
    ? VERTICAL_ID
    : HORIZONTAL_ID
  const active = activeVariantId === VERTICAL_ID ? vertical : horizontal
  const enabledSafeFrames = horizontal.publishedFrames + (dual ? vertical.publishedFrames : 0)
  const enabledRenderedFrames = horizontal.renderedFrames + (dual ? vertical.renderedFrames : 0)
  const enabledInFlightFrames = horizontal.inFlightFrames + (dual ? vertical.inFlightFrames : 0)
  const enabledTotalFrames = dual ? 200 : 100
  const aggregateP50Seconds = dual ? 10_800 : horizontal.etaP50Seconds
  const aggregateP90Seconds = dual ? 14_400 : horizontal.etaP90Seconds

  return {
    schema_version: '2.0.0',
    sequence: options.sequence,
    timestamp: '2026-07-23T09:00:02Z',
    job_id: JOB_ID,
    project_id: 'TRIP-TO-ANDROMEDA',
    state: 'running',
    phase: 'render_frame',
    scene_id: 'SPACE-JOURNEY',
    scene_sha256: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    profile_id: 'TRIP-TO-ANDROMEDA-720P-HYPER-OPTIMIZED',
    profile_sha256: 'BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB',
    frame_start: 1,
    frame_end: 100,
    current_frame: active.currentFrame,
    latest_rendered_frame: active.latestRenderedFrame,
    renderer_event_type: 'frame_written',
    renderer_event_sequence: options.sequence,
    renderer_status: 'rendering',
    worker_id: activeVariantId === HORIZONTAL_ID ? 'worker-horizontal' : 'worker-vertical',
    current_act_id: 'andromeda-finish-line',
    current_act_name: 'Arrival',
    current_shot_id: 'arrival-reveal',
    current_shot_name: 'The galaxy answers',
    last_completed_frame: active.latestRenderedFrame,
    rendered_frames: enabledRenderedFrames,
    in_flight_frames: enabledInFlightFrames,
    validated_frames: enabledSafeFrames,
    published_frames: enabledSafeFrames,
    total_frames: enabledTotalFrames,
    chunk_start: 1,
    chunk_end: 20,
    current_chunk_progress: active.currentFrame / 20,
    chunks_completed: 0,
    chunks_total: 5,
    estimated_completion_at: '2026-07-23T13:00:00Z',
    eta_confidence: 'high',
    metrics: {
      current_seconds_per_frame: 1.25,
      rolling_median_seconds: 1.2,
      rolling_mean_seconds: 1.24,
      p90_seconds: 1.5,
      current_storage_bytes: 1_000_000,
      projected_storage_bytes: 20_000_000,
      free_storage_bytes: 100_000_000_000,
      gpu_utilization_percent: 92,
      vram_used_bytes: 7_000_000_000,
      gpu_temperature_c: 64,
      cpu_utilization_percent: 38,
      ram_used_bytes: 12_000_000_000,
    },
    preview_frame: active.previewFrame,
    latest_preview_at: '2026-07-23T09:00:01Z',
    latest_log_line: options.warning ?? `Published ordered frame ${active.latestSafeFrame} for ${activeVariantId}.`,
    warning: options.warning ?? null,
    error: null,
    safe_stop_status: 'none',
    renderer_active: true,
    watcher_active: true,
    current_frame_started_at: '2026-07-23T09:00:00Z',
    last_output_at: '2026-07-23T09:00:01Z',
    active_variant_id: activeVariantId,
    output_variants: [
      variant(HORIZONTAL_ID, true, horizontal),
      variant(VERTICAL_ID, dual, vertical),
    ],
    aggregate_eta: eta(aggregateP50Seconds, aggregateP90Seconds),
    workers: [
      worker('worker-horizontal', horizontal.currentFrame),
      ...(dual ? [worker('worker-vertical', vertical.currentFrame)] : []),
    ],
    retry_count: 0,
    failure_count: 0,
    created_at: '2026-07-23T08:59:00Z',
    updated_at: '2026-07-23T09:00:02Z',
    output_path: 'C:\\synthetic\\andromeda-v2',
    project_name: 'Trip to Andromeda',
    scene_name: 'Andromeda Story V2',
    profile_name: 'Andromeda output matrix',
    can_resume: false,
    can_encode: false,
    dry_run: true,
  }
}

export function makeSyntheticOutputMatrixFixture(
  mode: SyntheticOutputMatrixMode,
): SyntheticOutputMatrixFixture {
  return {
    initialJob: buildPayload(mode, { sequence: mode === 'dual' ? 100 : 200 }),
    event: (options) => buildPayload(mode, options),
  }
}

export async function installSyntheticEventSource(page: Page): Promise<void> {
  await page.addInitScript({
    content: `
      (() => {
        const instances = [];
        let current = null;

        class SyntheticEventSource extends EventTarget {
          static CONNECTING = 0;
          static OPEN = 1;
          static CLOSED = 2;

          constructor(url, options = {}) {
            super();
            this.url = String(url);
            this.withCredentials = options.withCredentials === true;
            this.readyState = SyntheticEventSource.CONNECTING;
            this.onopen = null;
            this.onmessage = null;
            this.onerror = null;
            instances.push(this);
            current = this;
            window.setTimeout(() => {
              if (this.readyState === SyntheticEventSource.CLOSED) return;
              this.readyState = SyntheticEventSource.OPEN;
              const event = new Event('open');
              if (typeof this.onopen === 'function') this.onopen(event);
              this.dispatchEvent(event);
            }, 0);
          }

          close() {
            this.readyState = SyntheticEventSource.CLOSED;
          }

          emit(payload, eventName = 'render_event') {
            if (this.readyState !== SyntheticEventSource.OPEN) {
              throw new Error('Synthetic EventSource is not open.');
            }
            const event = new MessageEvent(eventName, { data: JSON.stringify(payload) });
            if (eventName === 'message' && typeof this.onmessage === 'function') this.onmessage(event);
            this.dispatchEvent(event);
          }

          fail() {
            if (this.readyState === SyntheticEventSource.CLOSED) return;
            const event = new Event('error');
            if (typeof this.onerror === 'function') this.onerror(event);
          }
        }

        window.EventSource = SyntheticEventSource;
        window.__trackpromptSyntheticSse = {
          push(payload) {
            if (!current) throw new Error('Synthetic EventSource has not connected.');
            current.emit(payload);
          },
          disconnect() {
            if (!current) throw new Error('Synthetic EventSource has not connected.');
            current.fail();
          },
          connectionUrls() {
            return instances.map((instance) => instance.url);
          },
        };
      })();
    `,
  })
}

export async function installSyntheticMissionControlRoutes(
  page: Page,
  fixture: SyntheticOutputMatrixFixture,
): Promise<void> {
  await page.route('**/api/mission-control/**', async (route) => {
    const url = new URL(route.request().url())
    const endpoint = url.pathname.replace('/api/mission-control', '')

    if (endpoint === '/system/status') {
      const response = await route.fetch()
      const current = await response.json() as Record<string, unknown>
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...current,
          active_job_id: JOB_ID,
          renderer_busy: true,
        }),
      })
      return
    }

    if (endpoint === '/jobs') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([fixture.initialJob]) })
      return
    }

    if (endpoint === `/render/${JOB_ID}/logs`) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    if (endpoint === `/render/${JOB_ID}/preview` || endpoint === `/render/${JOB_ID}/frame`) {
      const variantId = url.searchParams.get('output_variant_id') ?? HORIZONTAL_ID
      const vertical = variantId === VERTICAL_ID
      const width = vertical ? 1_080 : 1_920
      const height = vertical ? 1_920 : 1_080
      const color = vertical ? '#7c3aed' : '#0891b2'
      const body = [
        `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
        `<rect width="${width}" height="${height}" fill="${color}"/>`,
        `<text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="white" font-size="64">${variantId}</text>`,
        '</svg>',
      ].join('')
      await route.fulfill({ status: 200, contentType: 'image/svg+xml', body })
      return
    }

    await route.continue()
  })
}

export async function pushSyntheticRenderEvent(
  page: Page,
  event: Record<string, unknown>,
): Promise<void> {
  await page.evaluate((payload) => {
    const controller = (window as unknown as {
      __trackpromptSyntheticSse?: { push: (value: Record<string, unknown>) => void }
    }).__trackpromptSyntheticSse
    if (!controller) throw new Error('Synthetic EventSource controller is unavailable.')
    controller.push(payload)
  }, event)
}

export async function disconnectSyntheticRenderEvents(page: Page): Promise<void> {
  await page.evaluate(() => {
    const controller = (window as unknown as {
      __trackpromptSyntheticSse?: { disconnect: () => void }
    }).__trackpromptSyntheticSse
    if (!controller) throw new Error('Synthetic EventSource controller is unavailable.')
    controller.disconnect()
  })
}

export async function syntheticEventSourceUrls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const controller = (window as unknown as {
      __trackpromptSyntheticSse?: { connectionUrls: () => string[] }
    }).__trackpromptSyntheticSse
    if (!controller) throw new Error('Synthetic EventSource controller is unavailable.')
    return controller.connectionUrls()
  })
}

export const syntheticOutputVariantIds = {
  horizontal: HORIZONTAL_ID,
  vertical: VERTICAL_ID,
} as const
