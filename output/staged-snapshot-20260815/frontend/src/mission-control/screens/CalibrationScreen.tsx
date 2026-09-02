import { BarChart3, Check, Cpu, Image, Play, Sparkles } from 'lucide-react'
import { Button, EmptyState, ErrorCard, Notice, SectionHeading, StatusBadge } from '../components'
import { formatDateTime, formatDuration, formatGiB } from '../format'
import type { CalibrationSummary } from '../types'

export function CalibrationScreen({
  calibrations,
  busy,
  onCreatePlan,
  onRunCandidate,
  onDismissError,
}: {
  calibrations: CalibrationSummary[]
  busy: boolean
  onCreatePlan: () => void
  onRunCandidate: (calibrationId: string, candidateId: string) => void
  onDismissError: (calibrationId: string) => void
}) {
  const latest = calibrations[0]
  return (
    <div className="mc-page">
      <SectionHeading
        eyebrow="Measured performance"
        title="Calibration"
        description="Compare real bounded samples from this machine. Existing recommended profiles do not require another full calibration."
        actions={<Button icon={<Sparkles aria-hidden="true" />} busy={busy} onClick={onCreatePlan}>New bounded calibration</Button>}
      />

      {!latest ? (
        <EmptyState
          icon={<BarChart3 aria-hidden="true" />}
          title="No calibration evidence found"
          description="Create a bounded plan to measure a small candidate sample. Mission Control will never launch the full timeline from this action."
          action={<Button tone="primary" busy={busy} onClick={onCreatePlan}>Create bounded plan</Button>}
        />
      ) : (
        <>
          {latest.recoverableError ? (
            <ErrorCard error={latest.recoverableError} onDismiss={() => onDismissError(latest.id)} />
          ) : null}
          <section className="mc-calibration-summary">
            <div className="mc-card mc-machine-card">
              <div className="mc-card__heading">
                <div><span className="mc-eyebrow">Latest completed calibration</span><h2>{latest.machineName}</h2></div>
                <StatusBadge tone={latest.status === 'complete' ? 'success' : latest.status === 'failed' ? 'error' : 'info'}>{latest.status}</StatusBadge>
              </div>
              <div className="mc-machine-visual"><Cpu aria-hidden="true" /></div>
              <dl className="mc-technical-list mc-technical-list--plain">
                <div><dt>Graphics</dt><dd>{latest.gpuName ?? 'Not reported'}</dd></div>
                <div><dt>Processor</dt><dd>{latest.cpuName ?? 'Not reported'}</dd></div>
                <div><dt>Memory</dt><dd>{latest.ramGiB ? `${latest.ramGiB} GB RAM` : 'Not reported'}</dd></div>
                <div><dt>Completed</dt><dd>{formatDateTime(latest.completedAt)}</dd></div>
              </dl>
            </div>
            <div className="mc-card mc-recommendation-card">
              <span className="mc-eyebrow">Recommended for this machine</span>
              <div className="mc-recommendation-card__mark"><Check aria-hidden="true" /></div>
              <h2>{latest.candidates.find((item) => item.profileId === latest.recommendedProfileId)?.displayName ?? latest.recommendedProfileId ?? 'Review pending'}</h2>
              <p>{latest.verdict ?? 'The local service has not recorded a final verdict yet.'}</p>
              <Notice tone="info"><p>Measured profiles remain valid until their saved scene or profile identity changes.</p></Notice>
            </div>
          </section>

          <section className="mc-card mc-comparison-card">
            <div className="mc-card__heading">
              <div><span className="mc-eyebrow">Finalist comparison</span><h2>Measured options</h2></div>
              <span className="mc-muted">{latest.candidates.length} candidates</span>
            </div>
            {latest.candidates.length === 0 ? (
              <div className="mc-card-empty"><p>This plan has no recorded candidates yet.</p></div>
            ) : (
              <div className="mc-table-scroll">
                <table className="mc-table">
                  <thead><tr><th>Profile</th><th>Samples</th><th>Expected</th><th>Conservative</th><th>Storage</th><th>Verdict</th><th><span className="mc-sr-only">Actions</span></th></tr></thead>
                  <tbody>
                    {latest.candidates.map((candidate) => (
                      <tr key={candidate.id}>
                        <td><strong>{candidate.displayName}</strong><small>{candidate.resolution}{candidate.recommendedRole ? ` · ${candidate.recommendedRole}` : ''}</small></td>
                        <td>{candidate.samples}</td>
                        <td>{formatDuration(candidate.expectedSeconds, true)}</td>
                        <td>{formatDuration(candidate.conservativeSeconds, true)}</td>
                        <td>{formatGiB(candidate.storageGiB)}</td>
                        <td><StatusBadge tone={candidate.qualityVerdict.toLowerCase().includes('pass') ? 'success' : 'neutral'}>{candidate.qualityVerdict}</StatusBadge>{candidate.caveat ? <small>{candidate.caveat}</small> : null}</td>
                        <td>{latest.status === 'planned' || latest.status === 'running' ? <Button tone="quiet" icon={<Play aria-hidden="true" />} disabled={busy} onClick={() => onRunCandidate(latest.id, candidate.id)}>Run sample</Button> : candidate.stillUrls[0] ? <a className="mc-button mc-button--quiet" href={candidate.stillUrls[0]} target="_blank" rel="noreferrer"><Image aria-hidden="true" />Review evidence</a> : null}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
