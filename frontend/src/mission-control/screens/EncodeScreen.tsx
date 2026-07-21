import { CheckCircle2, Film, FolderOpen, Music2, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { Button, EmptyState, Notice, ProgressBar, SectionHeading, StatusBadge } from '../components'
import { percent, sentenceCase } from '../format'
import type { EncodeCandidate, EncodeJob } from '../types'

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
  const [includeAudio, setIncludeAudio] = useState<Record<string, boolean>>({})
  return (
    <div className="mc-page">
      <SectionHeading
        eyebrow="Finish your render"
        title="Encode video"
        description="Turn a complete, verified frame sequence into a delivery video. Private audio is only read locally during the optional mux step."
      />
      {!capabilityAvailable ? (
        <Notice tone="warning" title="Encoding is not reported as available">
          <p>The local service has not enabled its verified FFmpeg encode workflow. No simulated encode will be shown.</p>
        </Notice>
      ) : null}

      {activeEncode ? (
        <section className="mc-card mc-encode-active" aria-live="polite">
          <div className="mc-card__heading">
            <div><span className="mc-eyebrow">Encode job</span><h2>{sentenceCase(activeEncode.status)}</h2></div>
            <StatusBadge tone={activeEncode.status === 'complete' ? 'success' : activeEncode.status === 'failed' ? 'error' : 'info'}>{Math.round(activeEncode.progress)}%</StatusBadge>
          </div>
          <ProgressBar value={activeEncode.progress} label="Encode progress" />
          {activeEncode.outputPath && activeEncode.status === 'complete' ? (
            <Button tone="primary" icon={<FolderOpen aria-hidden="true" />} onClick={() => onOpenOutput(activeEncode.outputPath ?? '')}>Open final output</Button>
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
          {candidates.map((candidate) => {
            const withAudio = includeAudio[candidate.jobId] ?? false
            return (
              <article key={candidate.jobId} className="mc-card mc-encode-card">
                <div className="mc-card__heading">
                  <div><span className="mc-eyebrow">Verified sequence</span><h2>{candidate.displayName}</h2></div>
                  <StatusBadge tone={candidate.verified ? 'success' : 'warning'}>{candidate.verified ? 'Verified' : 'Verification needed'}</StatusBadge>
                </div>
                <div className="mc-sequence-visual"><Film aria-hidden="true" /><span>{candidate.frameCount.toLocaleString()} / {candidate.totalFrames.toLocaleString()} frames</span></div>
                <ProgressBar value={percent(candidate.frameCount, candidate.totalFrames)} label={`${candidate.displayName} frame completeness`} />
                <ol className="mc-encode-steps">
                  <li className={candidate.verified ? 'is-complete' : ''}><CheckCircle2 aria-hidden="true" /><span><strong>Verify frame sequence</strong><small>Complete identities and image integrity</small></span></li>
                  <li><Film aria-hidden="true" /><span><strong>Encode video master</strong><small>Uses the repository’s exact FFmpeg workflow</small></span></li>
                  <li><ShieldCheck aria-hidden="true" /><span><strong>Verify final output</strong><small>Duration, streams, and frame contract</small></span></li>
                </ol>
                {candidate.audioMuxAvailable ? (
                  <label className="mc-checkbox-card">
                    <input type="checkbox" checked={withAudio} onChange={(event) => setIncludeAudio((current) => ({ ...current, [candidate.jobId]: event.target.checked }))} />
                    <Music2 aria-hidden="true" />
                    <span><strong>Include local private audio</strong><small>Audio never leaves this computer.</small></span>
                  </label>
                ) : null}
                <div className="mc-button-row">
                  <Button tone="primary" disabled={!capabilityAvailable || !candidate.verified} busy={busyJobId === candidate.jobId} onClick={() => onStartEncode(candidate.jobId, withAudio)}>
                    {withAudio ? 'Encode and add audio' : 'Encode video'}
                  </Button>
                  <Button tone="quiet" icon={<FolderOpen aria-hidden="true" />} onClick={() => onOpenOutput(candidate.outputPath)}>Open frames</Button>
                </div>
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}
