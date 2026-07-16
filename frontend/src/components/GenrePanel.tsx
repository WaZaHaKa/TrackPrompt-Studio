import { useMemo, useState } from 'react'
import { LockKeyhole, RotateCcw, Tag, TriangleAlert } from 'lucide-react'
import { patchGenre } from '../api'
import type { GenreAnalysis, GenreCandidate, GenrePatch } from '../types'
import { Button, ConfidenceBadge, InlineNotice, Toggle } from './ui'

interface GenrePanelProps {
  jobId: string
  initialGenre: GenreAnalysis | null | undefined
  onChange: (genre: GenreAnalysis) => void
}

function CandidateRow({
  candidate,
  busy,
  onPatch,
}: {
  candidate: GenreCandidate
  busy: boolean
  onPatch: (patch: GenrePatch) => Promise<void>
}) {
  const [label, setLabel] = useState(candidate.label)
  return (
    <article className={`genre-candidate ${candidate.rejected ? 'genre-candidate--rejected' : ''}`}>
      <div>
        <input aria-label={`Genre label for ${candidate.canonicalLabel}`} value={label} onChange={(event) => setLabel(event.target.value)} />
        <ConfidenceBadge confidence={candidate.confidence} />
        <span className="similarity-chip">similarity {candidate.similarity.toFixed(3)}</span>
      </div>
      <small>{candidate.parent ? `Parent: ${candidate.parent} Â· ` : ''}{candidate.custom ? 'Custom label' : 'Audio-text similarity, not probability'}</small>
      <div className="candidate-actions">
        <Button disabled={busy || candidate.accepted} onClick={() => void onPatch({ updates: [{ candidateId: candidate.id, accepted: true }] })}>Accept</Button>
        <Button variant="ghost" disabled={busy || candidate.rejected} onClick={() => void onPatch({ updates: [{ candidateId: candidate.id, rejected: true }] })}>Reject</Button>
        <Button variant="ghost" disabled={busy || label.trim() === candidate.label} onClick={() => void onPatch({ updates: [{ candidateId: candidate.id, label: label.trim() }] })}>Save edit</Button>
        <Button variant="ghost" disabled={busy} onClick={() => void onPatch({ updates: [{ candidateId: candidate.id, locked: !candidate.locked }] })} icon={<LockKeyhole aria-hidden="true" />}>{candidate.locked ? 'Unlock' : 'Lock'}</Button>
        <Button variant="ghost" disabled={busy || candidate.custom} onClick={() => void onPatch({ updates: [{ candidateId: candidate.id, restoreDetected: true }] })} icon={<RotateCcw aria-hidden="true" />}>Restore</Button>
      </div>
    </article>
  )
}

export function GenrePanel({ jobId, initialGenre, onChange }: GenrePanelProps) {
  const [genre, setGenre] = useState(initialGenre)
  const [custom, setCustom] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const candidates = useMemo(() => genre ? [...genre.broadCandidates, ...genre.subgenreCandidates] : [], [genre])

  const apply = async (patch: GenrePatch): Promise<void> => {
    setBusy(true)
    setError(undefined)
    try {
      const updated = await patchGenre(jobId, patch)
      setGenre(updated)
      onChange(updated)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Genre choices could not be updated.')
    } finally {
      setBusy(false)
    }
  }

  if (!genre) {
    return <InlineNotice tone="warning"><TriangleAlert aria-hidden="true" /> No local genre result is available for this analysis.</InlineNotice>
  }
  return (
    <section className="genre-panel" aria-labelledby="genre-panel-heading">
      <div className="section-heading">
        <div><span className="eyebrow"><Tag aria-hidden="true" /> Genre and style</span><h2 id="genre-panel-heading">Review similarity-ranked styles</h2><p>Accepting a result is explicit. BPM alone never determines genre.</p></div>
        <ConfidenceBadge confidence={genre.confidence} />
      </div>
      {genre.ambiguity ? <InlineNotice tone="warning">{genre.ambiguity}</InlineNotice> : null}
      {error ? <InlineNotice tone="error">{error}</InlineNotice> : null}
      <div className="genre-candidate-list">
        {candidates.map((candidate) => <CandidateRow key={candidate.id} candidate={candidate} busy={busy} onPatch={apply} />)}
      </div>
      <div className="custom-genre-row">
        <label>Custom genre<input value={custom} onChange={(event) => setCustom(event.target.value)} placeholder="e.g. ambient electronic" /></label>
        <Button disabled={busy || !custom.trim()} onClick={() => { void apply({ customGenre: custom.trim() }); setCustom('') }}>Add and accept</Button>
      </div>
      <Toggle checked={!genre.disabledForPrompt} onChange={(checked) => void apply({ disabledForPrompt: !checked })} label="Use accepted genre in prompt" description="The separate prompt selector controls strict top, compatible blend, user-selected only, or disabled behavior." />
      <Button variant="ghost" disabled={busy} onClick={() => void apply({ restoreAll: true })} icon={<RotateCcw aria-hidden="true" />}>Restore all detected genre values</Button>
      <details className="prompt-details">
        <summary>Genre evidence and method</summary>
        <dl>
          <div><dt>Model</dt><dd>{genre.modelId}</dd></div>
          <div><dt>Taxonomy</dt><dd>{genre.taxonomyVersion}</dd></div>
          <div><dt>Device</dt><dd>{genre.selectedDevice}</dd></div>
          <div><dt>Window agreement</dt><dd>{genre.agreementAcrossWindows == null ? 'unknown' : `${Math.round(genre.agreementAcrossWindows * 100)}%`}</dd></div>
          <div><dt>Method</dt><dd>{genre.method}</dd></div>
        </dl>
        {genre.blendCandidates.length > 0 ? <p><strong>Compatible blend:</strong> {genre.blendCandidates.join(', ')}</p> : null}
        {genre.descriptiveTags.length > 0 ? <p><strong>Descriptive tags:</strong> {genre.descriptiveTags.map((item) => `${item.label} (${item.similarity.toFixed(3)})`).join(', ')}</p> : null}
        <ol>{genre.windowEvidence.map((window) => <li key={window.id}>{window.kind}: {window.startSeconds.toFixed(1)}-{window.endSeconds.toFixed(1)}s Â· {window.topLabels.join(', ')}</li>)}</ol>
      </details>
    </section>
  )
}
