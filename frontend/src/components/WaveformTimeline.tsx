import { useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent } from 'react'
import { Pause, Play, RotateCcw, SkipBack, SkipForward, Volume2, VolumeX } from 'lucide-react'
import WaveSurfer from 'wavesurfer.js'
import { audioUrl } from '../api'
import type { AnalysisResult, AnalysisSection, FactUpdate } from '../types'
import { formatFeatureValue, isFeatureValue, isRecord } from '../types'
import { Button, ConfidenceBadge, InlineNotice } from './ui'

const SECTION_COLORS = ['#a8f0c6', '#f2c879', '#bdadff', '#7ed7df', '#ef9fa9', '#99bdf5']

function formatTime(value: number): string {
  if (!Number.isFinite(value)) return '0:00'
  const seconds = Math.max(0, Math.floor(value))
  return `${Math.floor(seconds / 60)}:${(seconds % 60).toString().padStart(2, '0')}`
}

function numberFromSection(value: AnalysisSection['energy']): number {
  if (typeof value === 'number') return value
  if (isFeatureValue(value) && typeof value.value === 'number') return value.value
  return 0.35
}

function chordSegments(analysis: AnalysisResult): Array<{ chord: string; start: number; end: number; confidence: string }> {
  const feature = analysis.harmony.chords
  if (!isFeatureValue(feature) || !Array.isArray(feature.value)) return []
  return feature.value.flatMap((item) => {
    if (!isRecord(item)) return []
    const start = item.startSeconds
    const end = item.endSeconds
    if (typeof start !== 'number' || typeof end !== 'number' || typeof item.chord !== 'string') return []
    return [{ chord: item.chord, start, end, confidence: typeof item.confidence === 'string' ? item.confidence : 'unknown' }]
  })
}

interface WaveformTimelineProps {
  jobId: string
  analysis: AnalysisResult
  sourceFile?: File | null
  savingPath?: string
  onUpdate: (updates: FactUpdate[]) => Promise<void>
}

interface SectionCardProps {
  section: AnalysisSection
  index: number
  color: string
  previousEnd: number
  nextStart: number
  duration: number
  disabledPaths: string[]
  savingPath?: string
  onSeek: (time: number) => void
  onUpdate: (updates: FactUpdate[]) => Promise<void>
}

function EditableSectionCard({
  section,
  index,
  color,
  previousEnd,
  nextStart,
  duration,
  disabledPaths,
  savingPath,
  onSeek,
  onUpdate,
}: SectionCardProps) {
  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState(section.inferredLabel ?? section.neutralLabel)
  const [start, setStart] = useState(String(section.startSeconds))
  const [end, setEnd] = useState(String(section.endSeconds))
  const [error, setError] = useState<string>()
  const basePath = `structure.sections.${index}`
  const labelPath = `${basePath}.inferredLabel`
  const saving = savingPath?.startsWith(`${basePath}.`) ?? false
  const labelDisabled = disabledPaths.includes(labelPath)
  const displayLabel = section.inferredLabel ?? section.neutralLabel

  useEffect(() => {
    if (editing) return
    setLabel(displayLabel)
    setStart(String(section.startSeconds))
    setEnd(String(section.endSeconds))
  }, [displayLabel, editing, section.endSeconds, section.startSeconds])

  const closeEditor = (): void => {
    setEditing(false)
    setError(undefined)
    setLabel(displayLabel)
    setStart(String(section.startSeconds))
    setEnd(String(section.endSeconds))
  }

  const save = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    const trimmedLabel = label.trim()
    const nextStartValue = Number(start)
    const nextEndValue = Number(end)
    if (!trimmedLabel) {
      setError('Enter a neutral section label.')
      return
    }
    if (!Number.isFinite(nextStartValue) || !Number.isFinite(nextEndValue)) {
      setError('Section boundaries must be finite numbers.')
      return
    }
    if (nextStartValue < previousEnd - 0.001 || nextEndValue <= nextStartValue || nextEndValue > Math.min(duration, nextStart) + 0.001) {
      setError('Keep this section after the previous section, before the next section, and within the track.')
      return
    }
    setError(undefined)
    try {
      const updates: FactUpdate[] = []
      if (trimmedLabel !== displayLabel) updates.push({ path: labelPath, value: trimmedLabel })
      if (Math.abs(nextStartValue - section.startSeconds) > 0.001) {
        updates.push({ path: `${basePath}.startSeconds`, value: nextStartValue })
      }
      if (Math.abs(nextEndValue - section.endSeconds) > 0.001) {
        updates.push({ path: `${basePath}.endSeconds`, value: nextEndValue })
      }
      if (updates.length > 0) await onUpdate(updates)
      setEditing(false)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The section could not be updated.')
    }
  }

  const restore = async (): Promise<void> => {
    setError(undefined)
    try {
      await onUpdate([
        { path: labelPath, restoreDetected: true },
        { path: `${basePath}.startSeconds`, restoreDetected: true },
        { path: `${basePath}.endSeconds`, restoreDetected: true },
      ])
      setEditing(false)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The detected section could not be restored.')
    }
  }

  const toggleLabelInPrompt = async (): Promise<void> => {
    setError(undefined)
    try {
      await onUpdate([{ path: labelPath, disabledForPrompt: !labelDisabled }])
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The prompt inclusion setting could not be updated.')
    }
  }

  return (
    <article className={`section-card ${editing ? 'section-card--editing' : ''}`}>
      <span className="section-card__marker" style={{ background: color }} />
      <span className="section-card__top"><strong>{displayLabel}</strong><ConfidenceBadge confidence={section.confidence} /></span>
      <span className="section-card__time">{formatTime(section.startSeconds)} — {formatTime(section.endSeconds)}</span>
      <span className="section-card__details">
        {section.instruments?.length ? section.instruments.join(', ') : 'Instrumentation uncertain'}
        {section.harmonySummary ? ` · ${section.harmonySummary}` : ''}
      </span>
      {section.deepEvidence ? (
        <details className="method-details">
          <summary>Deep stem evidence · {section.deepEvidence.confidence}</summary>
          <p>Vocals {section.deepEvidence.activity.vocals ?? 'unknown'} · drums {section.deepEvidence.activity.drums ?? 'unknown'} · bass {section.deepEvidence.activity.bass ?? 'unknown'} · other {section.deepEvidence.activity.other ?? 'unknown'}</p>
          <p>{Object.entries(section.deepEvidence.relativeRms).map(([name, ratio]) => `${name} ${ratio.toFixed(3)}`).join(' · ')}</p>
          <small>{section.deepEvidence.method}</small>
        </details>
      ) : <span className="section-card__meta">Deep section evidence unavailable</span>}
      <span className="section-card__meta">Energy {formatFeatureValue(section.energy)} · Density {formatFeatureValue(section.density)}</span>
      <div className="section-card__actions">
        <Button variant="ghost" aria-label={`Play ${displayLabel} from ${formatTime(section.startSeconds)}`} onClick={() => onSeek(section.startSeconds)}>Seek</Button>
        <Button variant="ghost" aria-label={`Edit section ${displayLabel}`} onClick={() => { setEditing(true); setError(undefined) }}>Edit</Button>
      </div>
      {editing ? (
        <form className="section-editor" onSubmit={(event) => void save(event)}>
          <div className="field-stack">
            <label htmlFor={`section-label-${section.id}`}>Section label</label>
            <input id={`section-label-${section.id}`} value={label} maxLength={160} onChange={(event) => setLabel(event.target.value)} list="section-label-suggestions" />
            <small>Use a neutral label such as intro, verse, chorus, bridge, build, outro, or section A.</small>
          </div>
          <div className="two-fields">
            <div className="field-stack"><label htmlFor={`section-start-${section.id}`}>Start seconds</label><input id={`section-start-${section.id}`} type="number" min={previousEnd} max={Number(end) || nextStart} step="any" value={start} onChange={(event) => setStart(event.target.value)} /></div>
            <div className="field-stack"><label htmlFor={`section-end-${section.id}`}>End seconds</label><input id={`section-end-${section.id}`} type="number" min={Number(start) || previousEnd} max={Math.min(duration, nextStart)} step="any" value={end} onChange={(event) => setEnd(event.target.value)} /></div>
          </div>
          {error ? <InlineNotice tone="error">{error}</InlineNotice> : null}
          <div className="section-editor__actions">
            <Button type="submit" variant="primary" busy={saving}>Save section</Button>
            <Button type="button" variant="ghost" busy={saving} onClick={() => void toggleLabelInPrompt()}>{labelDisabled ? 'Use label in prompt' : 'Exclude label from prompt'}</Button>
            <Button type="button" variant="ghost" busy={saving} onClick={() => void restore()}>Restore detected</Button>
            <Button type="button" variant="ghost" disabled={saving} onClick={closeEditor}>Cancel</Button>
          </div>
        </form>
      ) : error ? <InlineNotice tone="error">{error}</InlineNotice> : null}
    </article>
  )
}

export function WaveformTimeline({ jobId, analysis, sourceFile, savingPath, onUpdate }: WaveformTimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const waveRef = useRef<WaveSurfer | null>(null)
  const fallbackAttempted = useRef(false)
  const [ready, setReady] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [showChords, setShowChords] = useState(true)
  const [currentTime, setCurrentTime] = useState(0)
  const [playbackError, setPlaybackError] = useState<string>()
  const sections = analysis.structure.sections ?? []
  const duration = analysis.file.durationSeconds ?? Math.max(0, ...sections.map((section) => section.endSeconds))
  const chords = useMemo(() => chordSegments(analysis), [analysis])

  useEffect(() => {
    if (!containerRef.current) return
    fallbackAttempted.current = false
    setReady(false)
    setPlaying(false)
    setCurrentTime(0)
    setPlaybackError(undefined)
    const wave = WaveSurfer.create({
      container: containerRef.current,
      height: 132,
      waveColor: '#35504a',
      progressColor: '#a8f0c6',
      cursorColor: '#f7faf7',
      cursorWidth: 2,
      barWidth: 2,
      barGap: 2,
      barRadius: 2,
      normalize: true,
      dragToSeek: true,
      autoScroll: false,
      backend: 'MediaElement',
    })
    waveRef.current = wave
    const unsubs = [
      wave.on('ready', () => { setReady(true); setPlaybackError(undefined) }),
      wave.on('timeupdate', (time) => setCurrentTime(time)),
      wave.on('play', () => setPlaying(true)),
      wave.on('pause', () => setPlaying(false)),
      wave.on('finish', () => setPlaying(false)),
      wave.on('error', () => {
        if (sourceFile && !fallbackAttempted.current) {
          fallbackAttempted.current = true
          void wave.loadBlob(sourceFile)
          return
        }
        setPlaybackError('Playback audio is no longer available. The saved waveform and section map remain visible.')
      }),
    ]
    void wave.load(audioUrl(jobId))
    return () => {
      unsubs.forEach((unsubscribe) => unsubscribe())
      wave.destroy()
      waveRef.current = null
    }
  }, [jobId, sourceFile])

  const seek = (seconds: number): void => {
    const wave = waveRef.current
    if (!wave) return
    wave.setTime(seconds)
    setCurrentTime(seconds)
  }

  const togglePlay = (): void => {
    if (waveRef.current) void waveRef.current.playPause()
  }

  const fallbackBars = useMemo(() => {
    const peaks = analysis.waveformPeaks
    if (peaks.length <= 180) return peaks
    return Array.from({ length: 180 }, (_, index) => (
      peaks[Math.floor((index / 179) * (peaks.length - 1))] ?? 0
    ))
  }, [analysis.waveformPeaks])

  return (
    <section className="timeline-workspace" aria-labelledby="timeline-title">
      <div className="section-heading">
        <div><span className="eyebrow">Arrangement map</span><h2 id="timeline-title">Timeline & waveform</h2><p>Click any region or section card to seek. Labels stay neutral when semantic evidence is weak.</p></div>
        <span className="count-pill">{sections.length} sections</span>
      </div>

      <div className="player-card">
        <div className="player-card__title">
          <div><strong>{analysis.file.displayName ?? 'Private local track'}</strong><small>{formatTime(duration)} · {analysis.file.codec ?? 'audio'} · {analysis.file.channels ?? '—'} channel{analysis.file.channels === 1 ? '' : 's'}</small></div>
          <div className="player-card__options">
            {chords.length > 0 ? <Button variant="ghost" aria-pressed={showChords} onClick={() => setShowChords((value) => !value)}>{showChords ? 'Hide chords' : 'Show chords'}</Button> : null}
            <span className="local-chip">Local playback</span>
          </div>
        </div>

        <div className="waveform-shell" aria-label="Interactive audio waveform with arrangement sections">
          <div ref={containerRef} className={`waveform ${playbackError ? 'waveform--hidden' : ''}`} />
          {playbackError && fallbackBars.length > 0 ? (
            <div className="waveform-fallback" aria-hidden="true">
              {fallbackBars.map((peak, index) => <span key={index} style={{ height: `${Math.max(8, Math.abs(peak) * 100)}%` }} />)}
            </div>
          ) : playbackError ? (
            <div className="waveform-unavailable">Waveform peaks unavailable</div>
          ) : null}
          <div className="section-overlays">
            {sections.map((section, index) => (
              <button
                key={section.id}
                className="section-overlay"
                style={{
                  left: `${duration > 0 ? (section.startSeconds / duration) * 100 : 0}%`,
                  width: `${duration > 0 ? ((section.endSeconds - section.startSeconds) / duration) * 100 : 0}%`,
                  '--section-color': SECTION_COLORS[index % SECTION_COLORS.length],
                } as CSSProperties}
                onClick={() => seek(section.startSeconds)}
                aria-label={`Seek to ${section.inferredLabel ?? section.neutralLabel} at ${formatTime(section.startSeconds)}`}
              >
                <span>{section.inferredLabel ?? section.neutralLabel}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="energy-strip" aria-label="Section energy curve">
          <span className="energy-strip__label">Energy</span>
          <svg viewBox="0 0 1000 64" role="img" aria-label="Energy across the arrangement" preserveAspectRatio="none">
            <polyline
              fill="none"
              stroke="#f2c879"
              strokeWidth="4"
              vectorEffect="non-scaling-stroke"
              points={sections.map((section, index) => {
                const x = sections.length <= 1 ? 0 : (index / (sections.length - 1)) * 1000
                const y = 58 - Math.min(1, Math.max(0, numberFromSection(section.energy))) * 52
                return `${x},${y}`
              }).join(' ')}
            />
          </svg>
        </div>

        {showChords && chords.length > 0 ? (
          <div className="chord-strip" aria-label="Approximate chord labels">
            <span className="chord-strip__label">Chords</span>
            <div>
              {chords.map((chord, index) => (
                <button
                  key={`${chord.start}-${chord.chord}-${index}`}
                  style={{
                    left: `${duration > 0 ? (chord.start / duration) * 100 : 0}%`,
                    width: `${duration > 0 ? ((chord.end - chord.start) / duration) * 100 : 0}%`,
                  }}
                  onClick={() => seek(chord.start)}
                  title={`${chord.confidence} confidence`}
                >{chord.chord}</button>
              ))}
            </div>
          </div>
        ) : null}

        <div className="player-controls">
          <Button variant="ghost" icon={<SkipBack aria-hidden="true" />} aria-label="Seek backward 10 seconds" onClick={() => seek(Math.max(0, currentTime - 10))}>10s</Button>
          <Button variant="primary" icon={playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />} disabled={!ready} onClick={togglePlay}>{playing ? 'Pause' : 'Play'}</Button>
          <Button variant="ghost" icon={<SkipForward aria-hidden="true" />} aria-label="Seek forward 10 seconds" onClick={() => seek(Math.min(duration, currentTime + 10))}>10s</Button>
          <span className="player-time" aria-live="off">{formatTime(currentTime)} <small>/ {formatTime(duration)}</small></span>
          <Button
            variant="ghost"
            icon={muted ? <VolumeX aria-hidden="true" /> : <Volume2 aria-hidden="true" />}
            aria-label={muted ? 'Unmute audio' : 'Mute audio'}
            onClick={() => {
              const next = !muted
              waveRef.current?.setMuted(next)
              setMuted(next)
            }}
          >{muted ? 'Unmute' : 'Mute'}</Button>
          <Button variant="ghost" icon={<RotateCcw aria-hidden="true" />} onClick={() => seek(0)}>Restart</Button>
        </div>
      </div>

      {playbackError ? <InlineNotice tone="warning">{playbackError}</InlineNotice> : null}

      <div className="section-card-grid">
        {sections.map((section, index) => (
          <EditableSectionCard
            key={section.id}
            section={section}
            index={index}
            color={SECTION_COLORS[index % SECTION_COLORS.length] ?? SECTION_COLORS[0]!}
            previousEnd={sections[index - 1]?.endSeconds ?? 0}
            nextStart={sections[index + 1]?.startSeconds ?? duration}
            duration={duration}
            disabledPaths={analysis.disabledFeaturePaths}
            savingPath={savingPath}
            onSeek={seek}
            onUpdate={onUpdate}
          />
        ))}
      </div>
      <datalist id="section-label-suggestions">
        {['intro', 'verse', 'pre-chorus', 'chorus', 'refrain', 'bridge', 'build', 'breakdown', 'drop', 'interlude', 'instrumental', 'transition', 'outro', 'section A', 'section B'].map((label) => <option value={label} key={label} />)}
      </datalist>
    </section>
  )
}
