import { useEffect, useMemo, useRef, useState } from 'react'
import { Download, LockKeyhole, RotateCcw, Trash2, TriangleAlert } from 'lucide-react'
import { audioUrl, deleteLyrics, getLyrics, lyricsExportUrl, patchLyrics } from '../api'
import type { AnalysisSection, LyricsAnalysisSummary, LyricsSegment, PrivateLyricsTranscript } from '../types'
import { Button, ConfidenceBadge, InlineNotice, Modal } from './ui'

interface LyricsPanelProps {
  jobId: string
  summary: LyricsAnalysisSummary | null | undefined
  sections: AnalysisSection[]
  onAnalysisRefresh: () => Promise<boolean>
}

function SegmentRow({ segment, sectionStarts, busy, onUpdate, onSeek }: {
  segment: LyricsSegment
  sectionStarts: ReadonlyMap<string, number>
  busy: boolean
  onUpdate: (segment: LyricsSegment, text: string) => Promise<void>
  onSeek: (seconds: number) => void
}) {
  const [text, setText] = useState(segment.text)
  return (
    <article className="lyrics-segment">
      <button className="timestamp-button" onClick={() => onSeek(segment.startSeconds)} aria-label={`Seek to ${segment.startSeconds.toFixed(1)} seconds`}>{segment.startSeconds.toFixed(1)}s</button>
      <textarea aria-label={`Approximate transcript at ${segment.startSeconds.toFixed(1)} seconds`} value={text} onChange={(event) => setText(event.target.value)} />
      <ConfidenceBadge confidence={segment.confidence} />
      <span className="similarity-chip">{segment.qualityDecision.replaceAll('_', ' ')}</span>
      {segment.activeSectionIds.length > 0 ? (
        <div className="candidate-actions" aria-label="Mapped structural sections">
          {segment.activeSectionIds.map((sectionId) => (
            <button key={sectionId} className="timestamp-button" onClick={() => onSeek(sectionStarts.get(sectionId) ?? segment.startSeconds)}>
              {sectionId}
            </button>
          ))}
        </div>
      ) : null}
      {segment.qualityFlags.length > 0 ? <small>Quality notes: {segment.qualityFlags.join(', ')}</small> : null}
      <div className="candidate-actions">
        <Button disabled={busy || text === segment.text} onClick={() => void onUpdate(segment, text)}>Save edit</Button>
        <Button variant="ghost" disabled={busy} onClick={() => void onUpdate(segment, '__uncertain__')}>Mark uncertain</Button>
        <Button variant="ghost" disabled={busy || !segment.userEdited} onClick={() => void onUpdate(segment, '__restore__')} icon={<RotateCcw aria-hidden="true" />}>Restore</Button>
        <Button variant="danger" disabled={busy} onClick={() => void onUpdate(segment, '__delete__')} icon={<Trash2 aria-hidden="true" />}>Delete segment</Button>
      </div>
    </article>
  )
}

export function LyricsPanel({ jobId, summary, sections, onAnalysisRefresh }: LyricsPanelProps) {
  const [transcript, setTranscript] = useState<PrivateLyricsTranscript>()
  const shouldLoadTranscript = Boolean(
    summary?.transcriptAvailable || summary?.status === 'completed' || summary?.status === 'no_reliable_words',
  )
  const [loading, setLoading] = useState(shouldLoadTranscript)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [themes, setThemes] = useState((summary?.abstractThemes ?? []).join(', '))
  const [themesApproved, setThemesApproved] = useState(Boolean(summary?.themesUserApproved))
  const [showRejected, setShowRejected] = useState(false)
  const audioRef = useRef<HTMLAudioElement>(null)
  const sectionStarts = useMemo(
    () => new Map(sections.map((section) => [section.id, section.startSeconds])),
    [sections],
  )

  const summaryThemes = (summary?.abstractThemes ?? []).join(', ')
  useEffect(() => {
    setThemes(summaryThemes)
    setThemesApproved(Boolean(summary?.themesUserApproved))
  }, [jobId, summaryThemes, summary?.themesUserApproved])

  useEffect(() => {
    if (!shouldLoadTranscript) return
    let active = true
    setLoading(true)
    void getLyrics(jobId).then((value) => { if (active) setTranscript(value) }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : 'The private transcript could not be loaded.')
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [jobId, shouldLoadTranscript])

  const updateSegment = async (segment: LyricsSegment, text: string): Promise<void> => {
    setBusy(true)
    setError(undefined)
    try {
      const update = text === '__delete__'
        ? { segmentId: segment.id, delete: true }
        : text === '__restore__'
          ? { segmentId: segment.id, restoreDetected: true }
          : text === '__uncertain__'
            ? { segmentId: segment.id, markUncertain: true }
            : { segmentId: segment.id, text }
      setTranscript(await patchLyrics(jobId, { updates: [update] }))
      if (!await onAnalysisRefresh()) {
        setError('The transcript was saved, but the current analysis summary could not be refreshed.')
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The transcript could not be updated.')
    } finally {
      setBusy(false)
    }
  }

  const seek = (seconds: number): void => {
    if (!audioRef.current) return
    audioRef.current.currentTime = seconds
    void audioRef.current.play().catch(() => undefined)
  }

  const saveThemes = async (): Promise<void> => {
    setBusy(true)
    setError(undefined)
    try {
      const next = themes.split(',').map((item) => item.trim()).filter(Boolean).slice(0, 8)
      setTranscript(await patchLyrics(jobId, { abstractThemes: next }))
      if (!await onAnalysisRefresh()) {
        setError('The themes were saved, but their prompt-approval state could not be refreshed.')
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Abstract themes could not be saved.')
    } finally {
      setBusy(false)
    }
  }

  const removeTranscript = async (): Promise<void> => {
    setBusy(true)
    try {
      await deleteLyrics(jobId)
      setTranscript(undefined)
      setDeleteOpen(false)
      if (!await onAnalysisRefresh()) {
        setError('The transcript was deleted, but the current analysis summary could not be refreshed.')
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The private transcript could not be deleted.')
    } finally {
      setBusy(false)
    }
  }

  const visibleSegments = transcript?.segments.filter(
    (segment) => segment.qualityDecision === 'accepted' || segment.qualityDecision === 'uncertain',
  ) ?? []
  const rejectedSegments = transcript?.segments.filter(
    (segment) => segment.qualityDecision === 'rejected_as_likely_hallucination'
      || segment.qualityDecision === 'non_lexical',
  ) ?? []

  return (
    <section className="lyrics-panel" aria-labelledby="lyrics-panel-heading">
      <div className="section-heading"><div><span className="eyebrow"><LockKeyhole aria-hidden="true" /> Private lyrics artifact</span><h2 id="lyrics-panel-heading">Approximate sung-word transcript</h2><p>Whisper is designed for speech. Singing, reverb, layers, vocal chops, distortion, and dense mixes can cause major errors.</p></div></div>
      <InlineNotice tone="warning"><TriangleAlert aria-hidden="true" /> Raw transcript text is private, excluded from standard exports, and never supplied as Suno prompt evidence.</InlineNotice>
      {summary ? (
        <dl className="diagnostic-grid">
          <div><dt>Status</dt><dd>{summary.status}</dd></div>
          <div><dt>Language</dt><dd>{summary.language ?? 'unknown'} ({summary.languageConfidence})</dd></div>
          <div><dt>Model/device</dt><dd>{summary.modelId ?? 'unavailable'} · {summary.selectedDevice}</dd></div>
          <div><dt>Usable segments</dt><dd>{summary.segmentCount}</dd></div>
          <div><dt>Vocal density</dt><dd>{summary.vocalWordDensity ?? 'unknown'}</dd></div>
          <div><dt>Active sections</dt><dd>{summary.activeSectionIds.length > 0 ? summary.activeSectionIds.join(', ') : 'none'}</dd></div>
        </dl>
      ) : null}
      {error ? <InlineNotice tone="error">{error}</InlineNotice> : null}
      {loading ? <p>Loading the private transcript…</p> : null}
      {transcript ? (
        <>
          <audio ref={audioRef} controls preload="metadata" src={audioUrl(jobId)} aria-label="Private source track playback" />
          <div className="lyrics-segment-list">{visibleSegments.map((segment) => <SegmentRow key={segment.id} segment={segment} sectionStarts={sectionStarts} busy={busy} onUpdate={updateSegment} onSeek={seek} />)}</div>
          {rejectedSegments.length > 0 ? (
            <>
              <InlineNotice tone="warning">
                {rejectedSegments.length} detected segment{rejectedSegments.length === 1 ? '' : 's'} hidden because the quality gate marked them as likely hallucinations or non-lexical vocals.
              </InlineNotice>
              <Button variant="ghost" onClick={() => setShowRejected((current) => !current)}>
                {showRejected ? 'Hide rejected detections' : 'Review rejected detections'}
              </Button>
              {showRejected ? (
                <div className="lyrics-segment-list" aria-label="Rejected transcript detections">
                  {rejectedSegments.map((segment) => <SegmentRow key={segment.id} segment={segment} sectionStarts={sectionStarts} busy={busy} onUpdate={updateSegment} onSeek={seek} />)}
                </div>
              ) : null}
            </>
          ) : null}
          <div className="results-actions">
            <a className="button button--secondary" href={lyricsExportUrl(jobId)} download><Download aria-hidden="true" /> Explicit transcript export</a>
            <Button variant="danger" onClick={() => setDeleteOpen(true)} icon={<Trash2 aria-hidden="true" />}>Delete complete transcript</Button>
          </div>
        </>
      ) : <p className="muted">No private transcript is available. Status: {summary?.status ?? 'not requested'}.</p>}
      {summary && summary.abstractThemes.length === 0 ? (
        <InlineNotice tone="warning">Abstract themes are unavailable until the lyric evidence passes the quality gate or you explicitly provide and approve themes.</InlineNotice>
      ) : null}
      <label className="theme-editor">Approved abstract themes<textarea value={themes} onChange={(event) => { setThemes(event.target.value); setThemesApproved(false) }} placeholder="persistence and late-night movement, introspection and uncertainty" /></label>
      <div className="results-actions">
        <Button disabled={busy} onClick={() => void saveThemes()}>Save and approve abstract themes</Button>
        <span className="muted">{themesApproved ? 'Themes are user approved.' : 'Themes are not approved for prompt evidence.'}</span>
      </div>
      {summary?.warnings.map((warning) => <InlineNotice key={warning} tone="warning">{warning}</InlineNotice>)}
      <Modal open={deleteOpen} title="Delete the private transcript?" description="This removes raw and detected transcript artifacts. The audio analysis remains." tone="danger" onClose={() => setDeleteOpen(false)} footer={<><Button variant="ghost" onClick={() => setDeleteOpen(false)}>Keep transcript</Button><Button variant="danger" busy={busy} onClick={() => void removeTranscript()}>Delete transcript</Button></>}>
        <InlineNotice tone="warning">This does not delete browser downloads or clipboard copies.</InlineNotice>
      </Modal>
    </section>
  )
}
