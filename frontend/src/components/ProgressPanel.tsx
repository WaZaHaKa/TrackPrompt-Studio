import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AudioWaveform,
  Ban,
  Check,
  Circle,
  Clock3,
  Cpu,
  FileCheck2,
  LoaderCircle,
  RefreshCcw,
  RotateCcw,
  Sparkles,
  WifiOff,
} from 'lucide-react'
import type { AnalysisJob, AnalysisStage } from '../types'
import { Button, InlineNotice } from './ui'

const CORE_STAGES: Array<{ key: AnalysisStage; label: string; detail: string }> = [
  { key: 'queued', label: 'Queued', detail: 'Waiting for a local analysis worker.' },
  { key: 'validating', label: 'Validating media', detail: 'Inspecting the real container and audio stream with ffprobe.' },
  { key: 'decoding', label: 'Decoding safely', detail: 'Creating private analysis signals while preserving stereo measurements.' },
  { key: 'inspecting_signal', label: 'Inspecting signal', detail: 'Checking level, silence, clipping, and usable signal.' },
  { key: 'analyzing_rhythm', label: 'Analyzing rhythm', detail: 'Comparing tempo cues, beat positions, meter, and groove.' },
  { key: 'analyzing_harmony', label: 'Analyzing harmony', detail: 'Estimating tonal center, mode, and harmonic character.' },
  { key: 'segmenting_structure', label: 'Mapping the arrangement', detail: 'Finding repeated regions, transitions, and the energy arc.' },
  { key: 'analyzing_production', label: 'Reading the production', detail: 'Measuring dynamics, spectral balance, width, and texture.' },
]

const DEEP_STAGES: Array<{ key: AnalysisStage; label: string; detail: string }> = [
  { key: 'separating_stems', label: 'Separating private stems', detail: 'Running an installed local separator; stems are temporary and never downloadable.' },
  { key: 'running_enhanced_taggers', label: 'Running enhanced taggers', detail: 'Refining instrumentation and section evidence with available local adapters.' },
  { key: 'transcribing_lyrics', label: 'Transcribing private vocals', detail: 'Running approximate sung-word recognition on the temporary vocal stem.' },
  { key: 'tagging_genre', label: 'Tagging genre', detail: 'Comparing bounded local windows with the reviewed CLAP taxonomy.' },
  { key: 'deriving_lyrical_themes', label: 'Deriving abstract themes', detail: 'Using the isolated local-only theme path without adding raw transcript text to prompt evidence.' },
]

const FINAL_STAGES: Array<{ key: AnalysisStage; label: string; detail: string }> = [
  { key: 'composing_prompt', label: 'Composing prompt', detail: 'Ranking supported facts and building an original deterministic prompt.' },
  { key: 'generating_candidates', label: 'Generating candidates', detail: 'Sampling the private local prompt writer with bounded structured evidence.' },
  { key: 'validating_candidates', label: 'Validating candidates', detail: 'Checking grounding, identity, lyrics, originality, locks, length, and diversity.' },
  { key: 'finalizing', label: 'Finalizing', detail: 'Saving the report and preparing the workspace.' },
]

const CANCELLATION_STAGE = {
  key: 'cancellation_requested' as const,
  label: 'Stopping safely',
  detail: 'Stopping the active local worker and removing private intermediate media.',
}

function useElapsed(startedAt: string, running: boolean): string {
  const started = useMemo(() => new Date(startedAt).getTime(), [startedAt])
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!running) return
    const interval = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [running])
  const totalSeconds = Math.max(0, Math.floor((now - started) / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return minutes > 0 ? `${minutes}:${seconds.toString().padStart(2, '0')}` : `${seconds}s`
}

interface ProgressPanelProps {
  job: AnalysisJob
  streamConnected: boolean
  streamRecovering: boolean
  cancelling: boolean
  actionError?: string
  onCancel: () => void
  onRefresh: () => void
  onStartOver: () => void
}

export function ProgressPanel({
  job,
  streamConnected,
  streamRecovering,
  cancelling,
  actionError,
  onCancel,
  onRefresh,
  onStartOver,
}: ProgressPanelProps) {
  const terminal = ['failed', 'cancelled', 'expired'].includes(job.status)
  const awaitingResult = job.status === 'completed'
  const stages = job.mode === 'deep'
    ? [...CORE_STAGES, ...DEEP_STAGES, ...FINAL_STAGES]
    : [...CORE_STAGES, ...FINAL_STAGES]
  const reportedStageIndex = stages.findIndex((stage) => stage.key === job.stage)
  const lastKnownStageIndex = useRef(0)
  if (reportedStageIndex >= 0) lastKnownStageIndex.current = reportedStageIndex
  const cancellationRequested = job.stage === 'cancellation_requested'
  const stageIndex = awaitingResult
    ? stages.length - 1
    : cancellationRequested
      ? lastKnownStageIndex.current
      : Math.max(0, reportedStageIndex)
  const active = cancellationRequested ? CANCELLATION_STAGE : (stages[stageIndex] ?? stages[0])
  const elapsed = useElapsed(job.createdAt, !terminal && !awaitingResult)

  return (
    <main className="progress-page" id="main-content">
      <section className="progress-hero">
        <div className={`analysis-orbit ${terminal ? 'analysis-orbit--stopped' : ''}`} aria-hidden="true">
          <span /><span /><span />
          {terminal ? <Ban /> : <AudioWaveform />}
        </div>
        <div>
          <span className="eyebrow"><Activity aria-hidden="true" /> Local analysis in progress</span>
          <h1>{terminal ? (job.status === 'cancelled' ? 'Analysis cancelled' : 'Analysis needs attention') : awaitingResult ? 'Opening your workspace.' : 'Listening closely.'}</h1>
          <p>{terminal ? (job.error?.message ?? job.message) : awaitingResult ? 'The report is complete and its saved result is being loaded.' : active?.detail}</p>
        </div>
      </section>

      <section className="progress-card" aria-labelledby="progress-title">
        <div className="progress-card__header">
          <div>
            <span className="step-pill">
              {job.requestedMode === 'deep' && job.mode === 'fast' ? 'DEEP → FAST FALLBACK' : job.mode.toUpperCase()}
            </span>
            <h2 id="progress-title">{terminal ? 'Job stopped' : active?.label}</h2>
            <p aria-live="polite">{job.message}</p>
          </div>
          <div className="elapsed"><Clock3 aria-hidden="true" /><span><small>Elapsed</small><strong>{elapsed}</strong></span></div>
        </div>

        {typeof job.progress === 'number' ? (
          <div className="real-progress" aria-label={`Server-reported progress ${Math.round(job.progress)} percent`}>
            <span style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }} />
          </div>
        ) : null}

        {streamRecovering && !terminal && !awaitingResult ? (
          <InlineNotice tone="warning">Live updates were interrupted. Reconnecting and checking the saved job state…</InlineNotice>
        ) : null}
        {actionError ? <InlineNotice tone="error">{actionError}</InlineNotice> : null}
        {!streamConnected && !streamRecovering && !terminal && !awaitingResult ? (
          <InlineNotice tone="info"><WifiOff aria-hidden="true" /> Progress is being recovered from the local job record.</InlineNotice>
        ) : null}

        <ol className="stage-list" aria-label="Analysis stages">
          {stages.map((stage, index) => {
            const complete = !terminal && index < stageIndex
            const current = !terminal && index === stageIndex
            const displayedStage = current && cancellationRequested ? CANCELLATION_STAGE : stage
            const Icon = complete ? Check : current ? LoaderCircle : Circle
            return (
              <li key={stage.key} className={`${complete ? 'stage--complete' : ''} ${current ? 'stage--active' : ''}`} aria-current={current ? 'step' : undefined}>
                <span className="stage-list__icon"><Icon className={current ? 'spin' : ''} aria-hidden="true" /></span>
                <span><strong>{displayedStage.label}</strong><small>{displayedStage.detail}</small></span>
                {complete ? <span className="stage-list__state">Done</span> : current ? <span className="stage-list__state">Active</span> : null}
              </li>
            )
          })}
        </ol>

        {terminal ? (
          <div className="recovery-actions">
            <InlineNotice tone={job.status === 'cancelled' ? 'info' : 'error'}>
              <strong>{job.status === 'cancelled' ? 'No result was saved.' : (job.error?.code ?? 'Analysis stopped')}</strong>
              <span>{job.error?.message ?? 'The local job did not complete. You can check its state or choose another file.'}</span>
            </InlineNotice>
            <Button icon={<RefreshCcw aria-hidden="true" />} onClick={onRefresh}>Check saved state</Button>
            <Button variant="primary" icon={<RotateCcw aria-hidden="true" />} onClick={onStartOver}>Choose another track</Button>
          </div>
        ) : awaitingResult ? (
          <div className="progress-actions">
            <div className="worker-note"><FileCheck2 aria-hidden="true" /><span><strong>Report saved locally</strong><small>Loading the validated result and prompt package.</small></span></div>
            <Button icon={<RefreshCcw aria-hidden="true" />} onClick={onRefresh}>Open workspace</Button>
          </div>
        ) : (
          <div className="progress-actions">
            <div className="worker-note"><Cpu aria-hidden="true" /><span><strong>Running on your machine</strong><small>You can cancel between stages; no completion time is invented.</small></span></div>
            <Button variant="ghost" busy={cancelling} onClick={onCancel}>{cancelling ? 'Cancelling…' : 'Cancel analysis'}</Button>
          </div>
        )}
      </section>

      <div className="progress-footnotes">
        <span><FileCheck2 aria-hidden="true" /> Media inspected by content, not extension</span>
        <span><Sparkles aria-hidden="true" /> Only supported facts enter the prompt</span>
      </div>
    </main>
  )
}
