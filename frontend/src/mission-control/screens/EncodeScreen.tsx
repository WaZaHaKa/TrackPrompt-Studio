import { CheckCircle2, Film, FolderOpen, Music2, ShieldCheck } from 'lucide-react'
import { Button, EmptyState, Notice, ProgressBar, SectionHeading, StatusBadge } from '../components'
import { percent, sentenceCase } from '../format'
import type { EncodeCandidate, EncodeJob } from '../types'

function duration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return 'Calculating'
  const rounded = Math.ceil(seconds)
  const hours = Math.floor(rounded / 3600)
  const minutes = Math.floor((rounded % 3600) / 60)
  const remainder = rounded % 60
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m ${remainder}s`
}

export function EncodeScreen({
  candidates,
  capabilityAvailable,
  activeEncode,
  busyJobId,
  onStartEncode,
  onOpenOutput,
}: {
  candidates: EncodeCandidate[]
  capabilityAvailable: boolean
  activeEncode: EncodeJob | null
  busyJobId: string | null
  onStartEncode: (jobId: string, includeAudio: boolean) => void
  onOpenOutput: (path: string) => void
}) {
  return (
    <div className="mc-page">
      <SectionHeading
        eyebrow="Finish your render"
        title="Encode video"
        description="Turn a complete, verified frame sequence into delivery and master videos. Private audio is read only from this computer."
      />
      {!capabilityAvailable ? (
        <Notice tone="warning" title="Encoding is not available">
          <p>The local service cannot launch the reviewed FFmpeg workflow. Check the FFmpeg path in Settings.</p>
        </Notice>
      ) : null}

      {activeEncode && activeEncode.status !== 'idle' ? (
        <section className="mc-card mc-encode-active" aria-live="polite">
          <div className="mc-card__heading">
            <div>
              <span className="mc-eyebrow">Encode job</span>
              <h2>{activeEncode.currentKind ? `${sentenceCase(activeEncode.currentKind)} — ${sentenceCase(activeEncode.status)}` : sentenceCase(activeEncode.status)}</h2>
            </div>
            <StatusBadge tone={activeEncode.status === 'complete' ? 'success' : activeEncode.status === 'failed' ? 'error' : 'info'}>{Math.round(activeEncode.progress)}%</StatusBadge>
          </div>
          <ProgressBar value={activeEncode.progress} label="Overall encode progress" />
          <p className="mc-muted">{activeEncode.detail}</p>
          {activeEncode.currentKind ? (
            <div className="mc-encode-telemetry">
              <span><strong>{(activeEncode.currentFrame ?? 0).toLocaleString()}</strong> / {activeEncode.totalFrames.toLocaleString()} frames</span>
              <span><strong>{activeEncode.fps?.toFixed(1) ?? '—'}</strong> fps</span>
              <span><strong>{activeEncode.speed ?? '—'}</strong> speed</span>
              <span><strong>{duration(activeEncode.etaSeconds)}</strong> remaining</span>
            </div>
          ) : null}
          <ol className="mc-encode-steps">
            {activeEncode.outputKinds.map((kind) => (
              <li key={kind} className={activeEncode.completedKinds.includes(kind) ? 'is-complete' : activeEncode.currentKind === kind ? 'is-active' : ''}>
                <CheckCircle2 aria-hidden="true" />
                <span>
                  <strong>{sentenceCase(kind)}</strong>
                  <small>{activeEncode.completedKinds.includes(kind) ? 'Verified and published' : activeEncode.currentKind === kind ? sentenceCase(activeEncode.status) : 'Queued'}</small>
                </span>
              </li>
            ))}
          </ol>
          {activeEncode.error ? <Notice tone="error" title={activeEncode.error.title}><p>{activeEncode.error.summary}</p></Notice> : null}
          {activeEncode.status === 'complete' ? (
            <div className="mc-button-row">
              {Object.entries(activeEncode.outputPaths).map(([kind, path]) => (
                <Button key={kind} tone={kind === 'delivery' ? 'primary' : 'quiet'} icon={<FolderOpen aria-hidden="true" />} onClick={() => onOpenOutput(path)}>Open {kind}</Button>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {candidates.length === 0 ? (
        <EmptyState
          icon={<Film aria-hidden="true" />}
          title="No complete frame sequence yet"
          description="A render appears here only after every required frame has been validated and published by the local service."
        />
      ) : (
        <div className="mc-encode-grid">
          {candidates.map((candidate) => (
            <article key={candidate.jobId} className="mc-card mc-encode-card">
              <div className="mc-card__heading">
                <div><span className="mc-eyebrow">Verified sequence</span><h2>{candidate.displayName}</h2></div>
                <StatusBadge tone={candidate.verified ? 'success' : 'warning'}>{candidate.verified ? 'Verified' : 'Verification needed'}</StatusBadge>
              </div>
              <div className="mc-sequence-visual"><Film aria-hidden="true" /><span>{candidate.frameCount.toLocaleString()} / {candidate.totalFrames.toLocaleString()} frames</span></div>
              <ProgressBar value={percent(candidate.frameCount, candidate.totalFrames)} label={`${candidate.displayName} frame completeness`} />
              <ol className="mc-encode-steps">
                <li className={candidate.verified ? 'is-complete' : ''}><CheckCircle2 aria-hidden="true" /><span><strong>Verify frame sequence</strong><small>Complete identities and image integrity</small></span></li>
                <li><Film aria-hidden="true" /><span><strong>Encode delivery MP4</strong><small>H.264 High, CRF 16, approved local audio</small></span></li>
                <li><Film aria-hidden="true" /><span><strong>Encode ProRes master</strong><small>ProRes 422 HQ with lossless PCM audio</small></span></li>
                <li><ShieldCheck aria-hidden="true" /><span><strong>Verify final outputs</strong><small>Duration, streams, frames, audio, and color contract</small></span></li>
              </ol>
              {candidate.audioMuxAvailable ? (
                <div className="mc-checkbox-card">
                  <Music2 aria-hidden="true" />
                  <span><strong>Approved local audio included</strong><small>Audio never leaves this computer.</small></span>
                </div>
              ) : null}
              <div className="mc-button-row">
                <Button tone="primary" disabled={!capabilityAvailable || !candidate.verified} busy={busyJobId === candidate.jobId} onClick={() => onStartEncode(candidate.jobId, true)}>
                  Encode delivery + master
                </Button>
                <Button tone="quiet" icon={<FolderOpen aria-hidden="true" />} onClick={() => onOpenOutput(candidate.outputPath)}>Open frames</Button>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  )
}
