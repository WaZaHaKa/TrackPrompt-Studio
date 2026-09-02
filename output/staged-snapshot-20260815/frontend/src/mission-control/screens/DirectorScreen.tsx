import { CheckCircle2, Clapperboard, Eye, RefreshCw, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { errorFromUnknown } from '../api'
import { Button, ErrorCard, Notice, SectionHeading, StatusBadge } from '../components'
import { formatDateTime, sentenceCase } from '../format'
import type {
  ConnectionState,
  DirectorAssessment,
  DirectorDecision,
  DirectorReview,
  DirectorShot,
  DirectorWorkspace,
  MissionControlClient,
  StructuredError,
} from '../types'

const assessmentFields = [
  ['focalReadability', 'Focal readability'],
  ['depth', 'Depth'],
  ['silhouette', 'Silhouette'],
  ['colorHierarchy', 'Color hierarchy'],
  ['visualDensity', 'Visual density'],
  ['storyClarity', 'Story clarity'],
  ['mobileReadability', 'Mobile readability'],
] as const

type AssessmentKey = typeof assessmentFields[number][0]

const defaultAssessments: Record<AssessmentKey, DirectorAssessment> = {
  focalReadability: 'unknown',
  depth: 'unknown',
  silhouette: 'unknown',
  colorHierarchy: 'unknown',
  visualDensity: 'unknown',
  storyClarity: 'unknown',
  mobileReadability: 'unknown',
}

function representativeFrame(shot: DirectorShot): number {
  return shot.reviewFrames[0] ?? shot.frameStart
}

export function DirectorScreen({ client, connection }: { client: MissionControlClient; connection: ConnectionState }) {
  const [workspace, setWorkspace] = useState<DirectorWorkspace | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<StructuredError | null>(null)
  const [selectedShot, setSelectedShot] = useState<DirectorShot | null>(null)
  const [assessments, setAssessments] = useState(defaultAssessments)
  const [decision, setDecision] = useState<DirectorDecision>('revise')
  const [findings, setFindings] = useState('')
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setWorkspace(await client.getDirectorWorkspace())
    } catch (cause) {
      setError(errorFromUnknown(cause, 'Director workspace could not be loaded'))
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => { void load() }, [load])

  const reviewsByShot = useMemo(
    () => new Map(workspace?.reviews.map((review) => [review.shotId, review]) ?? []),
    [workspace],
  )

  const startReview = (shot: DirectorShot): void => {
    const review = reviewsByShot.get(shot.id)
    setSelectedShot(shot)
    setDecision(review?.decision ?? 'revise')
    setFindings(review?.findings.join('\n') ?? '')
    setAssessments(review ? Object.fromEntries(
      assessmentFields.map(([key]) => [key, review[key]]),
    ) as Record<AssessmentKey, DirectorAssessment> : defaultAssessments)
  }

  const saveReview = async (): Promise<void> => {
    if (!workspace || !selectedShot) return
    const current = reviewsByShot.get(selectedShot.id)
    const review: DirectorReview = {
      schemaVersion: '1.0.0',
      shotId: selectedShot.id,
      reviewFrame: representativeFrame(selectedShot),
      ...assessments,
      findings: findings.split('\n').map((line) => line.trim()).filter(Boolean).slice(0, 20),
      decision,
      revisionMetadata: {
        revision: (current?.revisionMetadata.revision ?? 0) + 1,
        reviewer: 'human',
        note: decision === 'approve' ? 'Approved in the local Director workspace.' : 'Revision requested in the local Director workspace.',
      },
    }
    setSaving(true)
    setError(null)
    try {
      setWorkspace(await client.putDirectorReview(workspace.analysisJobId, selectedShot.id, review))
      setSelectedShot(null)
    } catch (cause) {
      setError(errorFromUnknown(cause, 'Director review could not be saved'))
    } finally {
      setSaving(false)
    }
  }

  if (error && !workspace) return <div className="mc-page"><ErrorCard error={error} onRetry={() => { void load() }} /></div>

  return (
    <div className="mc-page mc-director-page">
      <SectionHeading
        eyebrow="Cinematic Visualizer V2"
        title="Director"
        description="Review explainable story intent, representative frames, and shot-level art direction. All artifacts stay on this machine."
        actions={<Button tone="quiet" icon={<RefreshCw aria-hidden="true" />} busy={loading} onClick={() => { void load() }}>Refresh</Button>}
      />

      {connection !== 'connected' ? (
        <Notice tone="info" title="Reconnecting to the local service"><p>The last loaded review remains visible. Saving waits for the local connection.</p></Notice>
      ) : null}
      {error ? <ErrorCard error={error} onDismiss={() => setError(null)} /> : null}
      {loading && !workspace ? <div className="mc-director-empty" role="status">Loading the local story and shot plans…</div> : null}
      {!loading && !workspace ? (
        <div className="mc-director-empty">
          <Clapperboard aria-hidden="true" />
          <h2>No cinematic plan yet</h2>
          <p>Compile a Space Journey Story plan from a completed local analysis, then return here to review its shots.</p>
        </div>
      ) : null}

      {workspace ? (
        <>
          <div className="mc-director-meta">
            <span>Story schema {workspace.storyPlan.schemaVersion}</span>
            <span>Shot schema {workspace.shotPlan.schemaVersion}</span>
            <span>Updated {formatDateTime(workspace.updatedAt)}</span>
          </div>
          {workspace.storyPlan.acts.map((act) => (
            <section className="mc-director-act" key={act.id} aria-labelledby={`director-act-${act.id}`}>
              <header>
                <div><span className="mc-eyebrow">Frames {act.frameStart.toLocaleString()}–{act.frameEnd.toLocaleString()}</span><h2 id={`director-act-${act.id}`}>{act.name}</h2></div>
                <StatusBadge tone="info">{sentenceCase(act.protagonistState)}</StatusBadge>
                <p>{act.narrativePurpose}</p>
              </header>
              <div className="mc-director-shot-grid">
                {workspace.shotPlan.shots.filter((shot) => shot.actId === act.id).map((shot) => {
                  const review = reviewsByShot.get(shot.id)
                  return (
                    <article className="mc-director-shot" key={shot.id}>
                      <div className="mc-director-shot__frame" aria-label={`Representative review frame ${representativeFrame(shot)}`}>
                        <Eye aria-hidden="true" /><strong>Frame {representativeFrame(shot).toLocaleString()}</strong><span>Representative review frame</span>
                      </div>
                      <div className="mc-director-shot__body">
                        <div><span className="mc-eyebrow">{shot.frameStart.toLocaleString()}–{shot.frameEnd.toLocaleString()}</span><h3>{shot.name}</h3></div>
                        <p>{shot.storyPurpose}</p>
                        {review ? (
                          <div className="mc-director-review-summary">
                            <StatusBadge tone={review.decision === 'approve' ? 'success' : 'warning'}>
                              {review.revisionMetadata.reviewer === 'human'
                                ? (review.decision === 'approve' ? 'Human approved' : 'Human requests revision')
                                : (review.decision === 'approve' ? 'Codex-assisted pass' : 'Codex recommends revision')}
                            </StatusBadge>
                            <span>Revision {review.revisionMetadata.revision}</span>
                            <span>{review.revisionMetadata.reviewer === 'human' ? 'Human review' : 'Codex-assisted review — not human approval'}</span>
                            {review.findings.length > 0 ? <ul>{review.findings.map((finding) => <li key={finding}>{finding}</li>)}</ul> : <small>No written findings.</small>}
                          </div>
                        ) : <p className="mc-muted">Awaiting review</p>}
                        <Button tone="quiet" icon={<Clapperboard aria-hidden="true" />} onClick={() => startReview(shot)}>{review ? 'Revise review' : 'Review shot'}</Button>
                      </div>
                    </article>
                  )
                })}
              </div>
            </section>
          ))}
        </>
      ) : null}

      {selectedShot ? (
        <section className="mc-card mc-director-editor" aria-labelledby="director-review-title">
          <div className="mc-card__heading"><div><span className="mc-eyebrow">Representative frame {representativeFrame(selectedShot).toLocaleString()}</span><h2 id="director-review-title">Review {selectedShot.name}</h2></div></div>
          <div className="mc-director-assessments">
            {assessmentFields.map(([key, label]) => (
              <label key={key}><span>{label}</span><select value={assessments[key]} onChange={(event) => setAssessments((current) => ({ ...current, [key]: event.target.value as DirectorAssessment }))}><option value="clear">Clear</option><option value="acceptable">Acceptable</option><option value="needs-revision">Needs revision</option><option value="unknown">Unknown</option></select></label>
            ))}
          </div>
          <label className="mc-director-findings"><span>Findings <small>One concise finding per line</small></span><textarea rows={4} value={findings} onChange={(event) => setFindings(event.target.value)} maxLength={2400} /></label>
          <fieldset className="mc-director-decision"><legend>Decision</legend><label><input type="radio" name="director-decision" checked={decision === 'approve'} onChange={() => setDecision('approve')} /> Approve</label><label><input type="radio" name="director-decision" checked={decision === 'revise'} onChange={() => setDecision('revise')} /> Request revision</label></fieldset>
          <div className="mc-director-editor__actions"><Button tone="quiet" icon={<RotateCcw aria-hidden="true" />} onClick={() => setSelectedShot(null)}>Cancel</Button><Button tone="primary" icon={<CheckCircle2 aria-hidden="true" />} busy={saving} disabled={connection !== 'connected'} onClick={() => { void saveReview() }}>Save local review</Button></div>
        </section>
      ) : null}
    </div>
  )
}
