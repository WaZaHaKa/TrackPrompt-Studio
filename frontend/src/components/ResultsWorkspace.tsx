import { useEffect, useMemo, useState, type KeyboardEvent } from 'react'
import {
  Activity,
  AudioLines,
  Download,
  FileJson,
  FileText,
  Gauge,
  Guitar,
  KeyRound,
  LayoutDashboard,
  ListMusic,
  LockKeyhole,
  Mic2,
  Music2,
  RadioTower,
  SlidersHorizontal,
  Sparkles,
  Timer,
  Trash2,
  TriangleAlert,
  Volume2,
  Waves,
} from 'lucide-react'
import { exportUrl } from '../api'
import type {
  AnalysisResult,
  Capabilities,
  FactUpdate,
  FeatureValue,
  PromptPackage,
  PromptPreferences,
} from '../types'
import { formatFeatureValue, getFeatureAtPath, isRecord } from '../types'
import { AnalysisGroupPanel } from './AnalysisGroupPanel'
import { PromptWorkspace } from './PromptWorkspace'
import { GenrePanel } from './GenrePanel'
import { LyricsPanel } from './LyricsPanel'
import { WaveformTimeline } from './WaveformTimeline'
import { Button, ConfidenceBadge, InlineNotice, Modal } from './ui'
import { RendererSelector } from '../features/renderers/RendererSelector'

type ResultsTab = 'overview' | 'timeline' | 'rhythm' | 'instruments' | 'genre' | 'lyrics' | 'production' | 'prompt'

const TABS: Array<{ id: ResultsTab; label: string; icon: typeof LayoutDashboard }> = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'timeline', label: 'Timeline', icon: Waves },
  { id: 'rhythm', label: 'Rhythm & harmony', icon: Music2 },
  { id: 'instruments', label: 'Instruments & vocals', icon: Guitar },
  { id: 'genre', label: 'Genre & style', icon: RadioTower },
  { id: 'lyrics', label: 'Lyrics', icon: Mic2 },
  { id: 'production', label: 'Production', icon: SlidersHorizontal },
  { id: 'prompt', label: 'Prompt', icon: Sparkles },
]

function MetricCard({
  label,
  feature,
  icon: Icon,
  suffix,
}: {
  label: string
  feature?: FeatureValue
  icon: typeof Gauge
  suffix?: string
}) {
  return (
    <article className="metric-card">
      <div className="metric-card__icon"><Icon aria-hidden="true" /></div>
      <span>{label}</span>
      <strong>{feature ? formatFeatureValue(feature.value) : 'Unknown'}{feature?.value !== null && feature?.value !== undefined && suffix ? ` ${suffix}` : ''}</strong>
      {feature ? (
        <details>
          <summary><ConfidenceBadge confidence={feature.confidence} /></summary>
          <p>{feature.method}{feature.warning ? ` · ${feature.warning}` : ''}</p>
        </details>
      ) : <span className="confidence confidence--unknown">not detected</span>}
    </article>
  )
}

function Overview({ analysis }: { analysis: AnalysisResult }) {
  const key = getFeatureAtPath(analysis, 'harmony.key')
  const mode = getFeatureAtPath(analysis, 'harmony.mode')
  const instruments = getFeatureAtPath(analysis, 'instrumentation.candidates')
  const instrumentSummary: FeatureValue | undefined = instruments ? {
    ...instruments,
    value: Array.isArray(instruments.value)
      ? instruments.value.flatMap((candidate) => (
          isRecord(candidate) && typeof candidate.name === 'string' ? [candidate.name] : []
        ))
      : instruments.value,
  } : undefined
  const combinedKey: FeatureValue | undefined = key ? {
    ...key,
    value: `${formatFeatureValue(key.value)}${mode?.value ? ` ${formatFeatureValue(mode.value)}` : ''}`,
    confidence: key.confidence,
    method: mode ? `${key.method}; ${mode.method}` : key.method,
  } : undefined
  const privateMetadata = isRecord(analysis.file.privateMetadata) ? analysis.file.privateMetadata : {}
  const sufficient = getFeatureAtPath(analysis, 'signalQuality.sufficientSignal')

  return (
    <div className="overview-tab">
      <section aria-labelledby="at-a-glance-heading">
        <div className="section-heading"><div><span className="eyebrow">Analysis at a glance</span><h2 id="at-a-glance-heading">The track’s musical fingerprint</h2><p>Estimated features stay qualitative unless a measurement genuinely supports a number.</p></div></div>
        <div className="metric-grid">
          <MetricCard label="Tempo" feature={getFeatureAtPath(analysis, 'rhythm.bpm')} icon={Timer} suffix="BPM" />
          <MetricCard label="Key & mode" feature={combinedKey} icon={KeyRound} />
          <MetricCard label="Meter" feature={getFeatureAtPath(analysis, 'rhythm.meter')} icon={ListMusic} />
          <MetricCard label="Style" feature={getFeatureAtPath(analysis, 'styleAndMood.broadStyle')} icon={RadioTower} />
          <MetricCard label="Mood" feature={getFeatureAtPath(analysis, 'styleAndMood.mood')} icon={Sparkles} />
          <MetricCard label="Energy" feature={getFeatureAtPath(analysis, 'styleAndMood.energy')} icon={Activity} />
          <MetricCard label="Instruments" feature={instrumentSummary} icon={Guitar} />
          <MetricCard label="Vocal presence" feature={getFeatureAtPath(analysis, 'vocals.presence')} icon={Mic2} />
          <MetricCard label="Loudness" feature={getFeatureAtPath(analysis, 'production.integratedLoudnessLufs')} icon={Volume2} suffix="LUFS" />
          <MetricCard label="Dynamic range" feature={getFeatureAtPath(analysis, 'production.macroDynamicRangeDb')} icon={Gauge} suffix="dB" />
        </div>
      </section>

      {analysis.warnings.length > 0 ? (
        <section className="warning-panel" aria-labelledby="warnings-heading">
          <div><TriangleAlert aria-hidden="true" /><h2 id="warnings-heading">Important analysis notes</h2></div>
          <ul>{analysis.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </section>
      ) : (
        <InlineNotice tone="success">No top-level analyzer warnings were returned. Individual facts can still carry their own uncertainty.</InlineNotice>
      )}

      <details className="private-metadata">
        <summary>Analysis diagnostics</summary>
        <dl>
          <div><dt>Requested mode</dt><dd>{analysis.requestedMode}</dd></div>
          <div><dt>Effective mode</dt><dd>{analysis.effectiveMode}</dd></div>
          <div><dt>Deep adapter</dt><dd>{analysis.deepDiagnostics?.adapterId ?? 'not used'}</dd></div>
          <div><dt>Effective device</dt><dd>{analysis.deepDiagnostics?.selectedDevice ?? 'cpu'}</dd></div>
          <div><dt>CUDA runtime</dt><dd>{analysis.deepDiagnostics?.cudaRuntimeAvailable ? 'available' : 'unavailable'}</dd></div>
          {analysis.deepDiagnostics?.fallbackReason ? <div><dt>Fallback reason</dt><dd>{analysis.deepDiagnostics.fallbackReason}</dd></div> : null}
        </dl>
      </details>

      <div className="overview-columns">
        <section className="file-inspection" aria-labelledby="file-inspection-heading">
          <div className="subsection-heading"><h3 id="file-inspection-heading">File & signal</h3><p>Private technical details; never used as prompt instructions.</p></div>
          <dl>
            <div><dt>Duration</dt><dd>{analysis.file.durationSeconds ? `${analysis.file.durationSeconds.toFixed(1)} s` : 'Unknown'}</dd></div>
            <div><dt>Format</dt><dd>{analysis.file.container ?? 'Unknown'} / {analysis.file.codec ?? 'Unknown'}</dd></div>
            <div><dt>Sample rate</dt><dd>{analysis.file.sampleRate ? `${analysis.file.sampleRate.toLocaleString()} Hz` : 'Unknown'}</dd></div>
            <div><dt>Channels</dt><dd>{analysis.file.channels ?? 'Unknown'}</dd></div>
            <div><dt>Usable signal</dt><dd>{sufficient ? formatFeatureValue(sufficient.value) : 'Unknown'}</dd></div>
          </dl>
        </section>
        <details className="private-metadata">
          <summary><LockKeyhole aria-hidden="true" /> Private file metadata <span>{Object.keys(privateMetadata).length}</span></summary>
          <p>Shown only for your review. Artist, album, title, filename, and tags never enter classification or prompts.</p>
          {Object.keys(privateMetadata).length > 0 ? <dl>{Object.entries(privateMetadata).map(([keyName, value]) => <div key={keyName}><dt>{keyName}</dt><dd>{String(value)}</dd></div>)}</dl> : <p className="muted">No embedded metadata was returned.</p>}
        </details>
      </div>
    </div>
  )
}

interface ResultsWorkspaceProps {
  jobId: string
  analysis: AnalysisResult
  promptPackage?: PromptPackage
  capabilities: Capabilities
  sourceFile?: File | null
  requestedMode: 'fast' | 'deep'
  effectiveMode: 'fast' | 'deep'
  savingPath?: string
  deleting: boolean
  onUpdateFact: (update: FactUpdate) => Promise<void>
  onUpdateFacts: (updates: FactUpdate[]) => Promise<void>
  onGeneratePrompt: (preferences: PromptPreferences) => Promise<PromptPackage>
  onSelectPromptCandidate: (candidateId: string) => Promise<PromptPackage>
  onRefreshAnalysis: () => Promise<boolean>
  onDelete: () => Promise<void>
}

export function ResultsWorkspace({
  jobId,
  analysis,
  promptPackage,
  capabilities,
  sourceFile,
  requestedMode,
  effectiveMode,
  savingPath,
  deleting,
  onUpdateFact,
  onUpdateFacts,
  onGeneratePrompt,
  onSelectPromptCandidate,
  onRefreshAnalysis,
  onDelete,
}: ResultsWorkspaceProps) {
  const [tab, setTab] = useState<ResultsTab>('overview')
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string>()
  const [currentGenre, setCurrentGenre] = useState(analysis.genreAnalysis)
  const usedGenreIds = useMemo(
    () => new Set(
      (promptPackage?.factsUsed ?? [])
        .map((fact) => fact.path)
        .filter((path) => path.startsWith('genreAnalysis.accepted.'))
        .map((path) => path.slice('genreAnalysis.accepted.'.length)),
    ),
    [promptPackage?.factsUsed],
  )
  useEffect(() => {
    setCurrentGenre(analysis.genreAnalysis)
  }, [analysis.genreAnalysis])
  const disabledPaths = analysis.disabledFeaturePaths
  const createdLabel = useMemo(() => {
    const value = new Date(analysis.createdAt)
    return Number.isNaN(value.getTime()) ? 'recently' : value.toLocaleString()
  }, [analysis.createdAt])

  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const currentIndex = TABS.findIndex((item) => item.id === tab)
    let nextIndex = currentIndex
    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % TABS.length
    else if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + TABS.length) % TABS.length
    else if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = TABS.length - 1
    else return
    event.preventDefault()
    const next = TABS[nextIndex]
    if (!next) return
    setTab(next.id)
    const element = event.currentTarget.querySelector<HTMLButtonElement>(`#tab-${next.id}`)
    element?.focus()
  }

  const confirmDelete = async (): Promise<void> => {
    setDeleteError(undefined)
    try {
      await onDelete()
      setDeleteOpen(false)
    } catch (caught) {
      setDeleteError(caught instanceof Error ? caught.message : 'The analysis could not be deleted.')
    }
  }

  return (
    <main className="results-page" id="main-content">
      <header className="results-heading">
        <div>
          <span className="eyebrow"><AudioLines aria-hidden="true" /> Analysis complete</span>
          <h1>{analysis.file.displayName ?? 'Private track analysis'}</h1>
          <p>Created {createdLabel} · Schema {analysis.schemaVersion} · Analyzer {analysis.analysisVersion}</p>
          <div className="result-badges">
            <span className="local-chip"><LockKeyhole aria-hidden="true" /> Local only</span>
            <span className="mode-chip">{requestedMode === 'deep' && effectiveMode === 'fast' ? 'Deep requested · Fast fallback used' : `${effectiveMode} analysis`}</span>
            <span className={`network-chip ${capabilities.networkFeaturesEnabled ? 'network-chip--on' : ''}`}>{capabilities.networkFeaturesEnabled ? 'Optional network features on' : 'Network features off'}</span>
          </div>
        </div>
        <div className="results-actions">
          <a className="button button--secondary" href={exportUrl(jobId, 'json')} download><FileJson aria-hidden="true" /><span>Export JSON</span></a>
          <a className="button button--secondary" href={exportUrl(jobId, 'md')} download><FileText aria-hidden="true" /><span>Export Markdown</span></a>
          <Button variant="danger" icon={<Trash2 aria-hidden="true" />} onClick={() => setDeleteOpen(true)}>Delete analysis</Button>
        </div>
      </header>

      <RendererSelector jobId={jobId} capabilities={capabilities} />

      <div className="results-tabs" role="tablist" aria-label="Analysis workspace" onKeyDown={onTabKeyDown}>
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            id={`tab-${id}`}
            role="tab"
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            tabIndex={tab === id ? 0 : -1}
            onClick={() => setTab(id)}
          ><Icon aria-hidden="true" /><span>{label}</span></button>
        ))}
      </div>

      {tab !== 'prompt' ? <div className="results-panel" id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0}>
        {tab === 'overview' ? <Overview analysis={analysis} /> : null}
        {tab === 'timeline' ? (
          <>
            <WaveformTimeline jobId={jobId} analysis={analysis} sourceFile={sourceFile} savingPath={savingPath} onUpdate={onUpdateFacts} />
            <AnalysisGroupPanel group={analysis.structure} prefix="structure" title="Arrangement descriptors" description="Review or disable the detected energy arc, repetition summary, and important transitions used by the composer." disabledPaths={disabledPaths} savingPath={savingPath} onUpdate={onUpdateFact} />
          </>
        ) : null}
        {tab === 'rhythm' ? (
          <>
            <AnalysisGroupPanel group={analysis.rhythm} prefix="rhythm" title="Rhythm & groove" description="Tempo alternatives and groove claims remain approximate where estimator evidence disagrees." disabledPaths={disabledPaths} savingPath={savingPath} onUpdate={onUpdateFact} />
            <AnalysisGroupPanel group={analysis.harmony} prefix="harmony" title="Harmony" description="Chord details belong in the report; the prompt uses broader harmonic character by default." disabledPaths={disabledPaths} savingPath={savingPath} onUpdate={onUpdateFact} />
          </>
        ) : null}
        {tab === 'instruments' ? (
          <>
            <AnalysisGroupPanel group={analysis.instrumentation} prefix="instrumentation" title="Instrumentation" description="Candidates are coarse unless an installed deep adapter returned stronger evidence." disabledPaths={disabledPaths} savingPath={savingPath} onUpdate={onUpdateFact} />
            <AnalysisGroupPanel group={analysis.vocals} prefix="vocals" title="Vocal presentation" description="Descriptions cover audible register and delivery only; identity and sensitive traits are never inferred." disabledPaths={disabledPaths} savingPath={savingPath} onUpdate={onUpdateFact} />
            <AnalysisGroupPanel group={analysis.melody} prefix="melody" title="Melodic character" description="Weak predominant-melody claims are omitted in dense polyphonic mixes." disabledPaths={disabledPaths} savingPath={savingPath} onUpdate={onUpdateFact} />
          </>
        ) : null}
        {tab === 'production' ? (
          <>
            <AnalysisGroupPanel group={analysis.production} prefix="production" title="Production & mix" description="Indirect cues are named as proxies, not presented as certain studio facts." disabledPaths={disabledPaths} savingPath={savingPath} onUpdate={onUpdateFact} />
            <AnalysisGroupPanel group={analysis.timbre} prefix="timbre" title="Timbre & texture" description="Low-level measurements are translated into editable listener-friendly descriptors." disabledPaths={disabledPaths} savingPath={savingPath} onUpdate={onUpdateFact} />
            <AnalysisGroupPanel group={analysis.styleAndMood} prefix="styleAndMood" title="Style, mood & energy" description="Production-era resemblance is not the recording date; genre is never inferred from one feature alone." disabledPaths={disabledPaths} savingPath={savingPath} onUpdate={onUpdateFact} />
            <AnalysisGroupPanel group={analysis.signalQuality} prefix="signalQuality" title="Signal quality" description="Technical signal facts help calibrate the reliability of the rest of the report." disabledPaths={disabledPaths} savingPath={savingPath} onUpdate={onUpdateFact} />
          </>
        ) : null}
        {tab === 'genre' ? <GenrePanel jobId={jobId} initialGenre={currentGenre} usedGenreIds={usedGenreIds} onChange={(genre) => { setCurrentGenre(genre); void onRefreshAnalysis() }} /> : null}
        {tab === 'lyrics' ? <LyricsPanel jobId={jobId} summary={analysis.lyricsSummary} sections={analysis.structure.sections ?? []} onAnalysisRefresh={onRefreshAnalysis} /> : null}
      </div> : null}
      <div
        className="results-panel"
        id="panel-prompt"
        role="tabpanel"
        aria-labelledby="tab-prompt"
        tabIndex={0}
        hidden={tab !== 'prompt'}
      >
        <PromptWorkspace jobId={jobId} analysis={{ ...analysis, genreAnalysis: currentGenre }} capabilities={capabilities} promptPackage={promptPackage} onGenerate={onGeneratePrompt} onSelectCandidate={onSelectPromptCandidate} />
      </div>

      <footer className="privacy-footer">
        <LockKeyhole aria-hidden="true" />
        <span><strong>Your completed analysis is retained persistently.</strong><small>Only an explicit delete removes its private source and canonical artifacts. Temporary worker files and stems are still cleaned automatically.</small></span>
        <Download aria-hidden="true" />
      </footer>

      <Modal
        open={deleteOpen}
        title="Delete this analysis and audio?"
        description="This immediately removes the uploaded audio, temporary stems and intermediates, saved analysis metadata, and in-memory job state. Export anything you need first."
        tone="danger"
        onClose={() => setDeleteOpen(false)}
        footer={<><Button variant="ghost" onClick={() => setDeleteOpen(false)}>Keep analysis</Button><Button variant="danger" icon={<Trash2 aria-hidden="true" />} busy={deleting} onClick={() => void confirmDelete()}>Delete everything</Button></>}
      >
        {deleteError ? <InlineNotice tone="error">{deleteError}</InlineNotice> : <InlineNotice tone="warning">Deletion is permanent and the playback URL stops working immediately.</InlineNotice>}
      </Modal>
    </main>
  )
}
