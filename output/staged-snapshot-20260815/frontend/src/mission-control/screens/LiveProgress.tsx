import {
  Activity,
  Ban,
  CheckCircle2,
  Clipboard,
  Download,
  ExternalLink,
  FolderOpen,
  Gauge,
  Image as ImageIcon,
  PauseCircle,
  Play,
  RotateCw,
  Search,
  ShieldCheck,
  TerminalSquare,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { MISSION_CONTROL_API_BASE } from '../api'
import { AdvancedDetails, Button, ErrorCard, Metric, Notice, ProgressBar, SectionHeading, StatusBadge } from '../components'
import { elapsedSince, formatBytes, formatClock, formatDateTime, formatDuration, percent, sentenceCase } from '../format'
import type { ConnectionState, EtaEstimate, LogEntry, RenderJob, StageProgress } from '../types'

const runningStates = new Set(['starting', 'running', 'stop_requested', 'finishing_current_chunk', 'encoding', 'verifying'])

function appendVersion(url: string, job: RenderJob, frame: number | null, variantId: string | null): string {
  const separator = url.includes('?') ? '&' : '?'
  const variant = variantId ? `&output_variant_id=${encodeURIComponent(variantId)}` : ''
  return `${url}${separator}frame=${frame ?? 0}&sequence=${job.sequence}${variant}`
}

function etaSeconds(seconds: number | null | undefined, completionAt: string | null | undefined, now: number): number | null {
  if (seconds !== null && seconds !== undefined) return Math.max(0, seconds)
  if (!completionAt) return null
  const timestamp = new Date(completionAt).valueOf()
  return Number.isFinite(timestamp) ? Math.max(0, (timestamp - now) / 1000) : null
}

function etaDetail(estimate: EtaEstimate | null, fallbackConfidence: RenderJob['etaConfidence']): string {
  const confidence = estimate?.confidence ?? fallbackConfidence
  const freshness = estimate?.freshness ?? 'unknown'
  return `${sentenceCase(confidence)} confidence · ${sentenceCase(freshness)} estimate`
}

function stagePercent(stage: StageProgress): number | null {
  if (stage.progress !== null) return stage.progress * 100
  if (stage.completedUnits !== null && stage.totalUnits !== null && stage.totalUnits > 0) {
    return percent(stage.completedUnits, stage.totalUnits)
  }
  return null
}

function timelineSeconds(frame: number | null, frameStart: number | null, fps: number | null): number | null {
  if (
    frame === null
    || frameStart === null
    || fps === null
    || !Number.isFinite(frame)
    || !Number.isFinite(frameStart)
    || !Number.isFinite(fps)
    || fps <= 0
  ) {
    return null
  }
  return Math.max(0, (frame - frameStart) / fps)
}

function timelineDuration(frameStart: number | null, frameEnd: number | null, fps: number | null): number | null {
  if (
    frameStart === null
    || frameEnd === null
    || fps === null
    || !Number.isFinite(frameStart)
    || !Number.isFinite(frameEnd)
    || !Number.isFinite(fps)
    || fps <= 0
    || frameEnd < frameStart
  ) {
    return null
  }
  return (frameEnd - frameStart + 1) / fps
}

function formatTimeline(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return 'Not reported'
  const totalTenths = Math.round(seconds * 10)
  const hours = Math.floor(totalTenths / 36_000)
  const minutes = Math.floor((totalTenths % 36_000) / 600)
  const secondsWithTenths = (totalTenths % 600) / 10
  const secondsLabel = secondsWithTenths.toFixed(1).padStart(4, '0')
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${secondsLabel}`
    : `${String(minutes).padStart(2, '0')}:${secondsLabel}`
}

export function LiveProgress({
  job,
  connection,
  logs,
  busyAction,
  advanced,
  fallbackFps,
  onRefresh,
  onStopAfterChunk,
  onCancelStop,
  onResume,
  onOpenOutput,
  onEncode,
  onDismissError,
}: {
  job: RenderJob
  connection: ConnectionState
  logs: LogEntry[]
  busyAction: string | null
  advanced: boolean
  fallbackFps: number | null
  onRefresh: () => void
  onStopAfterChunk: () => void
  onCancelStop: () => void
  onResume: () => void
  onOpenOutput: () => void
  onEncode: () => void
  onDismissError: () => void
}) {
  const [now, setNow] = useState(Date.now())
  const [logsOpen, setLogsOpen] = useState(false)
  const [logQuery, setLogQuery] = useState('')
  const [previewFailed, setPreviewFailed] = useState(false)
  const enabledVariants = useMemo(
    () => (job.outputVariants ?? []).filter((variant) => variant.enabled),
    [job.outputVariants],
  )
  const [selectedVariantId, setSelectedVariantId] = useState<string | null>(
    job.activeVariantId ?? enabledVariants[0]?.id ?? null,
  )
  const selectedVariant = enabledVariants.find((variant) => variant.id === selectedVariantId)
    ?? enabledVariants.find((variant) => variant.id === job.activeVariantId)
    ?? enabledVariants[0]
    ?? null
  const running = runningStates.has(job.state)

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  useEffect(() => {
    setSelectedVariantId((current) => {
      if (current && enabledVariants.some((variant) => variant.id === current)) return current
      return enabledVariants.find((variant) => variant.id === job.activeVariantId)?.id
        ?? enabledVariants[0]?.id
        ?? null
    })
  }, [enabledVariants, job.activeVariantId])

  useEffect(
    () => setPreviewFailed(false),
    [job.previewFrame, job.previewUrl, job.sequence, selectedVariant?.id, selectedVariant?.previewFrame, selectedVariant?.previewUrl],
  )

  const filteredLogs = useMemo(() => {
    const query = logQuery.trim().toLowerCase()
    const source = logs.slice(-500)
    return query ? source.filter((entry) => entry.message.toLowerCase().includes(query) || entry.level.includes(query)) : source
  }, [logQuery, logs])

  const totalFrames = selectedVariant?.totalFrames || job.totalFrames
  const publishedFrames = selectedVariant?.publishedFrames ?? job.publishedFrames
  const validatedFrames = selectedVariant?.validatedFrames ?? job.validatedFrames
  const renderedFrames = selectedVariant?.renderedFrames ?? job.renderedFrames
  const inFlightFrames = selectedVariant?.inFlightFrames ?? job.inFlightFrames
  const selectedIsActive = !selectedVariant
    || enabledVariants.length === 1
    || job.activeVariantId === selectedVariant.id
  const currentFrame = selectedVariant?.currentFrame ?? (selectedIsActive ? job.currentFrame : null)
  const frameStart = selectedVariant?.frameStart ?? job.frameStart
  const latestRenderedFrame = selectedVariant?.latestRenderedFrame ?? (selectedIsActive ? job.latestRenderedFrame : null)
  const latestSafeFrame = selectedVariant?.latestSafeFrame
    ?? (selectedVariant ? null : job.lastCompletedFrame)
  const frameStartedAt = selectedVariant?.currentFrameStartedAt ?? (
    selectedIsActive
      ? job.currentFrameStartedAt
      : null
  )
  const lastOutputAt = selectedVariant?.lastOutputAt ?? (
    selectedIsActive
      ? job.lastOutputAt
      : null
  )
  const phase = selectedVariant?.phase ?? job.phase
  const variantState = selectedVariant?.state ?? job.state
  const activeChunkId = selectedVariant?.activeChunkId ?? job.activeChunkId
  const chunkStart = selectedVariant?.chunkStart ?? job.chunkStart
  const chunkEnd = selectedVariant?.chunkEnd ?? job.chunkEnd
  const chunksCompleted = selectedVariant?.chunksCompleted ?? job.chunksCompleted
  const chunksTotal = selectedVariant?.chunksTotal ?? job.chunksTotal
  const selectedEta = selectedVariant ? selectedVariant.eta : job.eta ?? null
  const stages = selectedVariant ? selectedVariant.stages : job.stages ?? []
  const workers = selectedVariant ? selectedVariant.workers : job.workers ?? []
  const activeWorkers = workers.filter((worker) => worker.active).length
    || (!selectedVariant && job.workerId && job.rendererActive !== false ? 1 : 0)
  const retryCount = selectedVariant?.retryCount ?? job.retryCount ?? 0
  const failureCount = selectedVariant?.failureCount ?? job.failureCount ?? 0
  const safeProgress = percent(publishedFrames, totalFrames)
  const exactCurrentFrame = currentFrame !== null && frameStartedAt !== null
  const frameProgress = !exactCurrentFrame || frameStart === null
    ? safeProgress
    : percent((currentFrame ?? frameStart) - frameStart + 1, totalFrames)
  const previewFrame = selectedVariant
    ? selectedVariant.previewFrame ?? selectedVariant.latestRenderedFrame
    : job.previewFrame ?? job.lastCompletedFrame
  const previewBase = selectedVariant?.previewUrl
    ?? (!selectedVariant ? job.previewUrl : null)
    ?? `${MISSION_CONTROL_API_BASE}/render/${encodeURIComponent(job.jobId)}/preview`
  const fullFrameUrl = selectedVariant?.fullFrameUrl ?? (!selectedVariant ? job.fullFrameUrl ?? null : null)
  const latestPreviewAt = selectedVariant ? selectedVariant.latestPreviewAt : job.latestPreviewAt
  const hasPreview = previewFrame !== null && !previewFailed
  const currentFrameElapsed = elapsedSince(frameStartedAt, now)
  const lastOutputElapsed = elapsedSince(lastOutputAt, now)
  const activeStatus = running && job.rendererActive !== false
    ? exactCurrentFrame && currentFrame !== null
      ? `Rendering frame ${currentFrame.toLocaleString()}`
      : phase === 'render_frame' && chunksTotal > 0
        ? `Rendering chunk ${Math.min(chunksCompleted + 1, chunksTotal)} of ${chunksTotal}`
        : sentenceCase(phase)
    : sentenceCase(variantState)
  const storyPosition = [job.currentActName, job.currentShotName].filter(Boolean).join(' · ')
  const p50Seconds = etaSeconds(selectedEta?.p50Seconds, selectedEta?.p50CompletionAt ?? job.estimatedCompletionAt, now)
  const p90Seconds = etaSeconds(selectedEta?.p90Seconds, selectedEta?.p90CompletionAt, now)
  const aggregateP50 = etaSeconds(job.aggregateEta?.p50Seconds, job.aggregateEta?.p50CompletionAt, now)
  const aggregateP90 = etaSeconds(job.aggregateEta?.p90Seconds, job.aggregateEta?.p90CompletionAt, now)
  const aggregateTotalFrames = enabledVariants.reduce((total, variant) => total + variant.totalFrames, 0)
  const aggregatePublishedFrames = enabledVariants.reduce((total, variant) => total + variant.publishedFrames, 0)
  const timelineFps = selectedVariant?.fps ?? fallbackFps
  const timelineFrame = currentFrame ?? latestRenderedFrame ?? latestSafeFrame
  const songTimestampSeconds = timelineSeconds(timelineFrame, frameStart, timelineFps)
  const songDurationSeconds = timelineDuration(
    frameStart,
    selectedVariant?.frameEnd ?? job.frameEnd,
    timelineFps,
  )
  const activeFrameMetricsApply = selectedIsActive || !selectedVariant
  const chunkFramesRemaining = (
    activeFrameMetricsApply
    && currentFrame !== null
    && chunkEnd !== null
    && currentFrame <= chunkEnd
  )
    ? chunkEnd - currentFrame + 1
    : null
  const chunkEtaP50 = (
    chunkFramesRemaining !== null
    && job.metrics.rollingMedianSeconds !== null
  )
    ? chunkFramesRemaining * job.metrics.rollingMedianSeconds
    : null
  const chunkEtaP90 = (
    chunkFramesRemaining !== null
    && job.metrics.p90Seconds !== null
  )
    ? chunkFramesRemaining * job.metrics.p90Seconds
    : null

  const copyLogs = async (): Promise<void> => {
    const text = filteredLogs.map((entry) => `${entry.timestamp} [${entry.level.toUpperCase()}] ${entry.message}`).join('\n')
    await navigator.clipboard?.writeText(text)
  }

  const downloadLogs = (): void => {
    const text = filteredLogs.map((entry) => `${entry.timestamp} [${entry.level.toUpperCase()}] ${entry.message}`).join('\n')
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `mission-control-${job.jobId}-logs.txt`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="mc-page mc-live-page">
      <SectionHeading
        eyebrow={job.projectName ?? 'Active render'}
        title={job.state === 'complete' ? 'Render complete' : activeStatus}
        description={job.state === 'complete'
          ? 'Every required frame has been validated and published safely.'
          : job.state === 'paused_safely' || job.state === 'resumable'
            ? 'The last chunk is safe. This exact scene and profile can resume when you are ready.'
            : 'The renderer runs in the local service and continues if this browser closes.'}
        actions={(
          <div className="mc-connection-chip" data-state={connection}>
            {connection === 'connected' ? <Wifi aria-hidden="true" /> : <WifiOff aria-hidden="true" />}
            <span>{sentenceCase(connection)}</span>
          </div>
        )}
      />

      {job.error ? <ErrorCard error={job.error} onRetry={job.canResume ? onResume : onRefresh} onDismiss={onDismissError} retryLabel={job.canResume ? 'Resume safely' : 'Refresh status'} /> : null}
      {job.warning ? <Notice tone="warning" title="Render warning"><p>{job.warning}</p></Notice> : null}
      {connection !== 'connected' ? (
        <Notice tone={connection === 'offline' ? 'warning' : 'info'} title={connection === 'offline' ? 'Live connection is offline' : 'Reconnecting to live updates'}>
          <p>Your render continues in the local service. Last known progress remains visible while Mission Control reconnects.</p>
        </Notice>
      ) : null}

      {enabledVariants.length > 0 ? (
        <section className="mc-output-variants" aria-labelledby="mc-output-variants-title">
          <div className="mc-output-variants__heading">
            <div>
              <span className="mc-eyebrow">Enabled output matrix</span>
              <h2 id="mc-output-variants-title">Output variants</h2>
            </div>
            {enabledVariants.length > 1 ? (
              <label className="mc-variant-select">
                <span>Preview and progress stream</span>
                <select
                  aria-label="Output variant"
                  value={selectedVariant?.id ?? ''}
                  onChange={(event) => setSelectedVariantId(event.target.value)}
                >
                  {enabledVariants.map((variant) => (
                    <option key={variant.id} value={variant.id}>{variant.displayName}</option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
          <div className="mc-variant-status-grid">
            {enabledVariants.map((variant) => (
              <article key={variant.id} className="mc-variant-status" data-selected={variant.id === selectedVariant?.id}>
                <div>
                  <strong>{variant.displayName}</strong>
                  <span>{variant.width > 0 && variant.height > 0 ? `${variant.width} × ${variant.height}` : 'Dimensions pending'}{variant.fps ? ` · ${variant.fps} FPS` : ''}</span>
                </div>
                <StatusBadge tone={variant.state === 'failed' ? 'error' : variant.state === 'complete' ? 'success' : 'info'}>
                  {variant.required ? 'Required' : 'Optional'} · {sentenceCase(variant.state ?? job.state)}
                </StatusBadge>
                <p>{variant.publishedFrames.toLocaleString()} of {variant.totalFrames.toLocaleString()} safe · {variant.renderedFrames.toLocaleString()} rendered</p>
              </article>
            ))}
          </div>
          {enabledVariants.length > 1 && aggregateTotalFrames > 0 ? (
            <div className="mc-aggregate-progress">
              <div>
                <strong>Enabled-matrix progress</strong>
                <span>{aggregatePublishedFrames.toLocaleString()} of {aggregateTotalFrames.toLocaleString()} frames safe across {enabledVariants.length} variants</span>
              </div>
              <ProgressBar value={percent(aggregatePublishedFrames, aggregateTotalFrames)} label="Aggregate enabled output progress" />
            </div>
          ) : null}
        </section>
      ) : null}

      <section className="mc-live-hero">
        <div className="mc-live-hero__status">
          <div className="mc-live-hero__topline">
            <StatusBadge tone={variantState === 'complete' ? 'success' : variantState === 'failed' ? 'error' : variantState === 'paused_safely' || variantState === 'resumable' ? 'warning' : 'info'}>{sentenceCase(variantState)}</StatusBadge>
            <span>{selectedVariant ? `${selectedVariant.displayName} · ` : ''}{sentenceCase(phase)}</span>
          </div>
          <div className="mc-big-progress">
            <strong>{Math.round(frameProgress)}<small>%</small></strong>
            <div>
              <span>{exactCurrentFrame && currentFrame !== null
                ? `Frame ${currentFrame.toLocaleString()} of ${totalFrames.toLocaleString()}`
                : `${publishedFrames.toLocaleString()} of ${totalFrames.toLocaleString()} safely published`}</span>
              <ProgressBar value={frameProgress} label="Current render progress" />
            </div>
          </div>
          <div className="mc-live-primary-metrics">
            <Metric label="Render ETA P50" value={p50Seconds === null ? 'Calibrating…' : formatDuration(p50Seconds)} detail={etaDetail(selectedEta, job.etaConfidence)} />
            <Metric label="Render ETA P90" value={p90Seconds === null ? 'Calibrating…' : formatDuration(p90Seconds)} detail={selectedEta?.lastEstimateAt ? `Updated ${formatDateTime(selectedEta.lastEstimateAt)}` : 'Conservative bound pending'} />
            <Metric
              label="Expected finish"
              value={formatClock(selectedEta?.p90CompletionAt ?? selectedEta?.p50CompletionAt ?? job.estimatedCompletionAt)}
              detail={selectedEta?.p90CompletionAt ? `P90 · ${formatDateTime(selectedEta.p90CompletionAt)}` : selectedEta?.p50CompletionAt ? `P50 · ${formatDateTime(selectedEta.p50CompletionAt)}` : job.estimatedCompletionAt ? formatDateTime(job.estimatedCompletionAt) : undefined}
            />
            <Metric label="Current chunk" value={chunksTotal > 0 ? `${Math.min(chunksCompleted + 1, chunksTotal)} of ${chunksTotal}` : 'Preparing'} detail={activeChunkId ?? (chunkStart !== null && chunkEnd !== null ? `Frames ${chunkStart}–${chunkEnd}` : undefined)} />
            <Metric label="Story position" value={storyPosition || 'Not reported'} detail={job.currentShotId ?? undefined} />
            <Metric
              label="Song timestamp"
              value={formatTimeline(songTimestampSeconds)}
              detail={songDurationSeconds === null ? `${timelineFps ?? 'Unknown'} FPS` : `of ${formatTimeline(songDurationSeconds)} · ${timelineFps} FPS`}
            />
            <Metric
              label="Current shot ETA"
              value="Indeterminate"
              detail={job.currentShotId
                ? 'The backend reports the shot identity, but not its frame bounds or a shot-scoped forecast.'
                : 'Waiting for the backend to report the current shot and its bounds.'}
            />
            <Metric
              label="Current chunk ETA"
              value={chunkEtaP50 === null ? 'Calibrating…' : `P50 ${formatDuration(chunkEtaP50)}`}
              detail={chunkEtaP90 === null
                ? 'Waiting for chunk bounds and rolling frame-time samples.'
                : `P90 ${formatDuration(chunkEtaP90)} · ${chunkFramesRemaining?.toLocaleString()} frames remaining`}
            />
            <Metric label="Active workers" value={activeWorkers > 0 ? activeWorkers.toLocaleString() : 'Not reported'} detail={`${retryCount.toLocaleString()} retries · ${failureCount.toLocaleString()} failures`} />
          </div>
          {activeStatus && running ? (
            <div className="mc-heartbeat" aria-live="polite">
              <Activity aria-hidden="true" />
              <div>
                <strong>Rendering is still active</strong>
                <span>{exactCurrentFrame && currentFrame !== null
                  ? `Frame ${currentFrame.toLocaleString()} · ${currentFrameElapsed} elapsed`
                  : chunkStart !== null && chunkEnd !== null
                    ? `Chunk frames ${chunkStart.toLocaleString()}–${chunkEnd.toLocaleString()} · exact frame activity is not reported`
                    : 'Renderer is preparing the next operation'}{lastOutputAt ? ` · last output ${lastOutputElapsed} ago` : ''}</span>
              </div>
            </div>
          ) : null}
        </div>

        <figure className="mc-preview-card">
          <div
            className="mc-preview-card__image"
            style={selectedVariant?.width && selectedVariant.height ? { aspectRatio: `${selectedVariant.width} / ${selectedVariant.height}` } : undefined}
          >
            {hasPreview ? (
              <img
                src={appendVersion(previewBase, job, previewFrame, selectedVariant?.id ?? null)}
                alt={`Latest completed ${selectedVariant?.displayName ?? 'render'} frame ${previewFrame ?? ''}`}
                onError={() => setPreviewFailed(true)}
              />
            ) : (
              <div className="mc-preview-placeholder"><ImageIcon aria-hidden="true" /><span>{previewFailed ? 'Preview is temporarily unavailable' : 'Preview appears after the first complete frame'}</span></div>
            )}
          </div>
          <figcaption>
            <span><strong>Latest completed frame</strong><small>{previewFrame !== null ? `Frame ${previewFrame.toLocaleString()}` : 'Waiting for a structurally valid frame'}</small></span>
            <span>{latestPreviewAt ? formatDateTime(latestPreviewAt) : 'Not available'}</span>
            {fullFrameUrl ? (
              <a className="mc-frame-link" href={fullFrameUrl} target="_blank" rel="noreferrer">
                Open exact full-resolution frame <ExternalLink aria-hidden="true" />
              </a>
            ) : null}
          </figcaption>
        </figure>
      </section>

      <section className="mc-safety-progress">
        <div className="mc-safety-progress__item mc-safety-progress__item--flight">
          <Gauge aria-hidden="true" />
          <div><span>Rendered, not yet safe</span><strong>{inFlightFrames.toLocaleString()} frames</strong><p>Written inside the active chunk, but not yet validated and published as recoverable.</p><small>{latestRenderedFrame === null ? `${renderedFrames.toLocaleString()} rendered in total` : `Latest rendered frame ${latestRenderedFrame.toLocaleString()}`}</small></div>
        </div>
        <div className="mc-safety-progress__item mc-safety-progress__item--safe">
          <ShieldCheck aria-hidden="true" />
          <div><span>Safe, preserved on resume</span><strong>{publishedFrames.toLocaleString()} frames</strong><p>Validated and published. These frames will not need to be rendered again.</p><small>{latestSafeFrame === null ? `${validatedFrames.toLocaleString()} validated in total` : `Latest safe frame ${latestSafeFrame.toLocaleString()}`}</small></div>
        </div>
        <div className="mc-safety-progress__bar"><ProgressBar value={safeProgress} label="Validated and published frame progress" /><span>{Math.round(safeProgress)}% safely published</span></div>
      </section>

      {job.aggregateEta ? (
        <section className="mc-card mc-aggregate-eta" aria-labelledby="mc-aggregate-eta-title">
          <div className="mc-card__heading">
            <div><span className="mc-eyebrow">Exact enabled output matrix</span><h2 id="mc-aggregate-eta-title">Aggregate job ETA</h2></div>
            <StatusBadge tone={job.aggregateEta.state === 'degraded' ? 'warning' : job.aggregateEta.state === 'stable' ? 'success' : 'info'}>
              {sentenceCase(job.aggregateEta.state)}
            </StatusBadge>
          </div>
          <div className="mc-eta-metrics">
            <Metric label="Aggregate ETA P50" value={aggregateP50 === null ? 'Calibrating…' : formatDuration(aggregateP50)} />
            <Metric label="Aggregate ETA P90" value={aggregateP90 === null ? 'Calibrating…' : formatDuration(aggregateP90)} />
            <Metric label="Confidence" value={sentenceCase(job.aggregateEta.confidence)} detail={`${sentenceCase(job.aggregateEta.freshness)} estimate`} />
            <Metric label="Last estimate" value={job.aggregateEta.lastEstimateAt ? formatClock(job.aggregateEta.lastEstimateAt) : 'Not available'} detail={job.aggregateEta.lastEstimateAt ? formatDateTime(job.aggregateEta.lastEstimateAt) : undefined} />
          </div>
        </section>
      ) : null}

      {stages.length > 0 ? (
        <section className="mc-card mc-stage-progress" aria-labelledby="mc-stage-progress-title">
          <div className="mc-card__heading"><div><span className="mc-eyebrow">{selectedVariant?.displayName ?? 'Render job'}</span><h2 id="mc-stage-progress-title">Stage progress and ETA</h2></div></div>
          <ol>
            {stages.map((stage) => {
              const progressValue = stagePercent(stage)
              const stageP50 = etaSeconds(stage.eta?.p50Seconds, stage.eta?.p50CompletionAt, now)
              const stageP90 = etaSeconds(stage.eta?.p90Seconds, stage.eta?.p90CompletionAt, now)
              return (
                <li key={stage.id}>
                  <div className="mc-stage-progress__heading">
                    <div><strong>{stage.label}</strong><span>{sentenceCase(stage.state)}</span></div>
                    <span>{progressValue === null ? 'Indeterminate' : `${Math.round(progressValue)}%`}</span>
                  </div>
                  {progressValue === null ? <div className="mc-stage-progress__indeterminate">Calibrating or waiting for measurable work</div> : <ProgressBar value={progressValue} label={`${stage.label} progress`} />}
                  <div className="mc-stage-progress__facts">
                    <span>{stage.completedUnits !== null && stage.totalUnits !== null ? `${stage.completedUnits.toLocaleString()} / ${stage.totalUnits.toLocaleString()} ${stage.throughputUnit ?? 'units'}` : 'Units not yet known'}</span>
                    <span>P50 {stageP50 === null ? 'calibrating' : formatDuration(stageP50)}</span>
                    <span>P90 {stageP90 === null ? 'calibrating' : formatDuration(stageP90)}</span>
                    <span>{stage.throughput === null ? 'Throughput pending' : `${stage.throughput.toLocaleString()} ${stage.throughputUnit ?? 'units'}/s`}</span>
                  </div>
                </li>
              )
            })}
          </ol>
        </section>
      ) : null}

      <div className="mc-live-controls">
        {job.state === 'running' && job.safeStopStatus !== 'requested' && job.safeStopStatus !== 'finishing_chunk' ? (
          <Button icon={<PauseCircle aria-hidden="true" />} busy={busyAction === 'stop'} onClick={onStopAfterChunk}>Stop after current chunk</Button>
        ) : null}
        {job.state === 'stop_requested' || job.state === 'finishing_current_chunk' || job.safeStopStatus === 'requested' || job.safeStopStatus === 'finishing_chunk' ? (
          <Button icon={<Ban aria-hidden="true" />} busy={busyAction === 'cancel-stop'} onClick={onCancelStop}>Cancel stop request</Button>
        ) : null}
        {(job.state === 'paused_safely' || job.state === 'resumable') && job.canResume ? (
          <Button tone="primary" icon={<Play aria-hidden="true" />} busy={busyAction === 'resume'} onClick={onResume}>Resume exact render</Button>
        ) : null}
        {job.state !== 'complete' && job.state !== 'cancelled' ? (
          <>
            <Button
              tone="danger"
              icon={<Ban aria-hidden="true" />}
              disabled
              aria-describedby="mc-cancel-render-unavailable"
            >
              Cancel render
            </Button>
            <Button
              icon={<RotateCw aria-hidden="true" />}
              disabled
              aria-describedby="mc-retry-chunk-unavailable"
            >
              Retry failed chunk
            </Button>
            <div className="mc-live-control-limitations" role="note" aria-label="Unavailable render actions">
              <p id="mc-cancel-render-unavailable"><strong>Cancel render is unavailable.</strong> The current backend exposes only a safe stop after the active chunk and cancellation of that stop request.</p>
              <p id="mc-retry-chunk-unavailable"><strong>Targeted chunk retry is unavailable.</strong> The current backend exposes exact resume for a resumable job, but no failed-chunk retry endpoint.</p>
            </div>
          </>
        ) : null}
        {job.outputPath ? <Button tone="quiet" icon={<FolderOpen aria-hidden="true" />} onClick={onOpenOutput}>Open output folder</Button> : null}
        <Button tone="quiet" icon={<TerminalSquare aria-hidden="true" />} onClick={() => setLogsOpen((value) => !value)}>{logsOpen ? 'Hide logs' : 'Open logs'}</Button>
        <Button tone="quiet" icon={<RotateCw aria-hidden="true" />} busy={busyAction === 'refresh'} onClick={onRefresh}>Refresh</Button>
        {job.state === 'complete' && job.canEncode ? <Button tone="primary" icon={<CheckCircle2 aria-hidden="true" />} busy={busyAction === 'encode-candidates'} onClick={onEncode}>Encode video</Button> : null}
      </div>

      {advanced ? (
        <section className="mc-card mc-advanced-metrics">
          <div className="mc-card__heading"><div><span className="mc-eyebrow">Advanced metrics</span><h2>Renderer health</h2></div></div>
          <div className="mc-metric-grid">
            <Metric label="Current frame" value={formatDuration(job.metrics.currentSecondsPerFrame, true)} />
            <Metric label="Rolling median" value={formatDuration(job.metrics.rollingMedianSeconds, true)} />
            <Metric label="Rolling mean" value={formatDuration(job.metrics.rollingMeanSeconds, true)} />
            <Metric label="P90" value={formatDuration(job.metrics.p90Seconds, true)} />
            <Metric label="Storage used" value={formatBytes(job.metrics.currentStorageBytes)} />
            <Metric label="Projected storage" value={formatBytes(job.metrics.projectedStorageBytes)} />
            <Metric label="Free storage" value={formatBytes(job.metrics.freeStorageBytes)} />
            <Metric label="GPU utilization" value={job.metrics.gpuUtilizationPercent === null ? 'Not available' : `${job.metrics.gpuUtilizationPercent}%`} />
            <Metric label="VRAM" value={formatBytes(job.metrics.vramUsedBytes)} />
            <Metric label="GPU temperature" value={job.metrics.gpuTemperatureC === null ? 'Not available' : `${job.metrics.gpuTemperatureC} °C`} />
            <Metric label="CPU" value={job.metrics.cpuUtilizationPercent === null ? 'Not available' : `${job.metrics.cpuUtilizationPercent}%`} />
            <Metric label="RAM" value={formatBytes(job.metrics.ramUsedBytes)} />
          </div>
          <AdvancedDetails summary="Exact render identity">
            <dl className="mc-technical-list">
              <div><dt>Job ID</dt><dd><code>{job.jobId}</code></dd></div>
              <div><dt>Scene</dt><dd><code>{job.sceneId ?? 'Unavailable'} · {job.sceneSha256 ?? 'hash unavailable'}</code></dd></div>
              <div><dt>Output variant</dt><dd><code>{selectedVariant?.id ?? 'Legacy single output'}{selectedVariant?.outputVariantSha256 ? ` · ${selectedVariant.outputVariantSha256}` : ''}</code></dd></div>
              <div><dt>Dimensions</dt><dd>{selectedVariant?.width && selectedVariant.height ? `${selectedVariant.width} × ${selectedVariant.height}${selectedVariant.fps ? ` · ${selectedVariant.fps} FPS` : ''}` : 'Not reported'}</dd></div>
              <div><dt>Composition</dt><dd><code>{selectedVariant?.compositionProfileId ?? 'Not reported'}{selectedVariant?.compositionProfileSha256 ? ` · ${selectedVariant.compositionProfileSha256}` : ''}</code></dd></div>
              <div><dt>Profile</dt><dd><code>{selectedVariant?.profileId ?? job.profileId ?? 'Unavailable'} · {selectedVariant?.profileSha256 ?? job.profileSha256 ?? 'hash unavailable'}</code></dd></div>
              <div><dt>Renderer</dt><dd>{job.rendererActive === null ? 'Not reported' : job.rendererActive ? 'Active' : 'Not active'}</dd></div>
              <div><dt>Watcher</dt><dd>{job.watcherActive === null ? 'Not reported' : job.watcherActive ? 'Active' : 'Not active'}</dd></div>
              <div><dt>Telemetry worker</dt><dd><code>{job.workerId ?? 'Not reported'}</code></dd></div>
              <div><dt>Latest renderer event</dt><dd>{job.rendererEventType ? `${sentenceCase(job.rendererEventType)} · ${job.rendererEventSequence ?? 'sequence unavailable'}` : 'Not reported'}</dd></div>
              <div><dt>Renderer status</dt><dd>{job.rendererStatus ? sentenceCase(job.rendererStatus) : 'Not reported'}</dd></div>
              <div><dt>Latest rendered frame</dt><dd>{latestRenderedFrame?.toLocaleString() ?? 'Not reported'}</dd></div>
              <div><dt>Workers / retries / failures</dt><dd>{activeWorkers.toLocaleString()} / {retryCount.toLocaleString()} / {failureCount.toLocaleString()}</dd></div>
              <div><dt>Event sequence</dt><dd>{job.sequence.toLocaleString()}</dd></div>
            </dl>
          </AdvancedDetails>
        </section>
      ) : null}

      {logsOpen ? (
        <section className="mc-card mc-log-panel">
          <div className="mc-card__heading">
            <div><span className="mc-eyebrow">Bounded activity stream</span><h2>Render logs</h2></div>
            <div className="mc-button-row"><Button tone="quiet" icon={<Clipboard aria-hidden="true" />} onClick={() => { void copyLogs() }}>Copy</Button><Button tone="quiet" icon={<Download aria-hidden="true" />} onClick={downloadLogs}>Download</Button></div>
          </div>
          <label className="mc-search-field"><Search aria-hidden="true" /><span className="mc-sr-only">Search logs</span><input type="search" value={logQuery} onChange={(event) => setLogQuery(event.target.value)} placeholder="Search activity and errors" /></label>
          <div className="mc-log-list" role="log" aria-live="polite">
            {filteredLogs.length > 0 ? filteredLogs.slice(-150).map((entry) => (
              <div key={`${entry.sequence}-${entry.timestamp}`} data-level={entry.level}>
                <time>{new Date(entry.timestamp).toLocaleTimeString()}</time><span>{entry.level}</span><p>{entry.message}</p>
                {advanced && entry.technicalDetails ? <pre>{entry.technicalDetails}</pre> : null}
              </div>
            )) : <p className="mc-muted">No matching log entries.</p>}
          </div>
        </section>
      ) : null}
    </div>
  )
}
