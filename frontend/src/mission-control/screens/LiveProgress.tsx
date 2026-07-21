import {
  Activity,
  Ban,
  CheckCircle2,
  Clipboard,
  Download,
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
import type { ConnectionState, LogEntry, RenderJob } from '../types'

const runningStates = new Set(['starting', 'running', 'stop_requested', 'finishing_current_chunk', 'encoding', 'verifying'])

function appendVersion(url: string, job: RenderJob): string {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}frame=${job.previewFrame ?? job.lastCompletedFrame ?? 0}&sequence=${job.sequence}`
}

export function LiveProgress({
  job,
  connection,
  logs,
  busyAction,
  advanced,
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
  const running = runningStates.has(job.state)

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  useEffect(() => setPreviewFailed(false), [job.previewFrame, job.previewUrl, job.sequence])

  const filteredLogs = useMemo(() => {
    const query = logQuery.trim().toLowerCase()
    const source = logs.slice(-500)
    return query ? source.filter((entry) => entry.message.toLowerCase().includes(query) || entry.level.includes(query)) : source
  }, [logQuery, logs])

  const safeProgress = percent(job.publishedFrames, job.totalFrames)
  const exactCurrentFrame = job.currentFrame !== null && job.currentFrameStartedAt !== null
  const frameProgress = !exactCurrentFrame || job.frameStart === null
    ? safeProgress
    : percent((job.currentFrame ?? job.frameStart) - job.frameStart + 1, job.totalFrames)
  const previewBase = job.previewUrl ?? `${MISSION_CONTROL_API_BASE}/render/${encodeURIComponent(job.jobId)}/preview`
  const hasPreview = (job.previewFrame ?? job.lastCompletedFrame) !== null && !previewFailed
  const currentFrameElapsed = elapsedSince(job.currentFrameStartedAt, now)
  const lastOutputElapsed = elapsedSince(job.lastOutputAt, now)
  const activeStatus = running && job.rendererActive !== false
    ? exactCurrentFrame && job.currentFrame !== null
      ? `Rendering frame ${job.currentFrame.toLocaleString()}`
      : job.phase === 'render_frame' && job.chunksTotal > 0
        ? `Rendering chunk ${Math.min(job.chunksCompleted + 1, job.chunksTotal)} of ${job.chunksTotal}`
        : sentenceCase(job.phase)
    : sentenceCase(job.state)

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

      <section className="mc-live-hero">
        <div className="mc-live-hero__status">
          <div className="mc-live-hero__topline">
            <StatusBadge tone={job.state === 'complete' ? 'success' : job.state === 'failed' ? 'error' : job.state === 'paused_safely' || job.state === 'resumable' ? 'warning' : 'info'}>{sentenceCase(job.state)}</StatusBadge>
            <span>{sentenceCase(job.phase)}</span>
          </div>
          <div className="mc-big-progress">
            <strong>{Math.round(frameProgress)}<small>%</small></strong>
            <div>
              <span>{exactCurrentFrame && job.currentFrame !== null
                ? `Frame ${job.currentFrame.toLocaleString()} of ${job.totalFrames.toLocaleString()}`
                : `${job.publishedFrames.toLocaleString()} of ${job.totalFrames.toLocaleString()} safely published`}</span>
              <ProgressBar value={frameProgress} label="Current render progress" />
            </div>
          </div>
          <div className="mc-live-primary-metrics">
            <Metric label="Time remaining" value={job.estimatedCompletionAt ? formatDuration(Math.max(0, (new Date(job.estimatedCompletionAt).valueOf() - now) / 1000)) : 'Calculating…'} detail={`${sentenceCase(job.etaConfidence)} confidence`} />
            <Metric label="Expected finish" value={formatClock(job.estimatedCompletionAt)} detail={job.estimatedCompletionAt ? formatDateTime(job.estimatedCompletionAt) : undefined} />
            <Metric label="Current chunk" value={job.chunksTotal > 0 ? `${Math.min(job.chunksCompleted + 1, job.chunksTotal)} of ${job.chunksTotal}` : 'Preparing'} detail={job.chunkStart !== null && job.chunkEnd !== null ? `Frames ${job.chunkStart}–${job.chunkEnd}` : undefined} />
          </div>
          {activeStatus && running ? (
            <div className="mc-heartbeat" aria-live="polite">
              <Activity aria-hidden="true" />
              <div>
                <strong>Rendering is still active</strong>
                <span>{exactCurrentFrame && job.currentFrame !== null
                  ? `Frame ${job.currentFrame.toLocaleString()} · ${currentFrameElapsed} elapsed`
                  : job.chunkStart !== null && job.chunkEnd !== null
                    ? `Chunk frames ${job.chunkStart.toLocaleString()}–${job.chunkEnd.toLocaleString()} · exact frame activity is not reported`
                    : 'Renderer is preparing the next operation'}{job.lastOutputAt ? ` · last output ${lastOutputElapsed} ago` : ''}</span>
              </div>
            </div>
          ) : null}
        </div>

        <figure className="mc-preview-card">
          <div className="mc-preview-card__image">
            {hasPreview ? (
              <img src={appendVersion(previewBase, job)} alt={`Latest completed render frame ${job.previewFrame ?? job.lastCompletedFrame ?? ''}`} onError={() => setPreviewFailed(true)} />
            ) : (
              <div className="mc-preview-placeholder"><ImageIcon aria-hidden="true" /><span>{previewFailed ? 'Preview is temporarily unavailable' : 'Preview appears after the first complete frame'}</span></div>
            )}
          </div>
          <figcaption>
            <span><strong>Latest complete preview</strong><small>{job.previewFrame ?? job.lastCompletedFrame ? `Frame ${(job.previewFrame ?? job.lastCompletedFrame)?.toLocaleString()}` : 'Waiting for a structurally valid frame'}</small></span>
            <span>{job.lastOutputAt ? formatDateTime(job.lastOutputAt) : 'Not available'}</span>
          </figcaption>
        </figure>
      </section>

      <section className="mc-safety-progress">
        <div className="mc-safety-progress__item mc-safety-progress__item--flight">
          <Gauge aria-hidden="true" />
          <div><span>In progress</span><strong>{job.inFlightFrames.toLocaleString()} frames</strong><p>Rendered inside the active chunk but not yet validated and saved as recoverable.</p></div>
        </div>
        <div className="mc-safety-progress__item mc-safety-progress__item--safe">
          <ShieldCheck aria-hidden="true" />
          <div><span>Safe</span><strong>{job.publishedFrames.toLocaleString()} frames</strong><p>Validated and published. These frames will not need to be rendered again.</p></div>
        </div>
        <div className="mc-safety-progress__bar"><ProgressBar value={safeProgress} label="Validated and published frame progress" /><span>{Math.round(safeProgress)}% safely published</span></div>
      </section>

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
              <div><dt>Profile</dt><dd><code>{job.profileId ?? 'Unavailable'} · {job.profileSha256 ?? 'hash unavailable'}</code></dd></div>
              <div><dt>Renderer</dt><dd>{job.rendererActive === null ? 'Not reported' : job.rendererActive ? 'Active' : 'Not active'}</dd></div>
              <div><dt>Watcher</dt><dd>{job.watcherActive === null ? 'Not reported' : job.watcherActive ? 'Active' : 'Not active'}</dd></div>
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
