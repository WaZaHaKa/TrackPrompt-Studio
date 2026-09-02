import { useRef, useState, type DragEvent } from 'react'
import {
  AudioLines,
  Check,
  FileAudio,
  FolderOpen,
  HardDrive,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
  WifiOff,
  X,
} from 'lucide-react'
import type { AnalysisMode, AnalysisOptions, Capabilities } from '../types'
import { Button, InlineNotice, Toggle } from './ui'

interface UploadPanelProps {
  capabilities: Capabilities
  capabilitiesLoading: boolean
  capabilitiesError?: string
  uploadError?: string
  submitting: boolean
  onAnalyze: (file: File, mode: AnalysisMode, options: AnalysisOptions) => void
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  return minutes >= 1 ? `${minutes} minutes` : `${seconds} seconds`
}

function sanitizeDisplayName(name: string): string {
  const safe = Array.from(name)
    .filter((character) => {
      const code = character.charCodeAt(0)
      const bidiControl = (code >= 0x202a && code <= 0x202e) || (code >= 0x2066 && code <= 0x2069)
      return code >= 32 && code !== 127 && !bidiControl && !['<', '>', '/', '\\'].includes(character)
    })
    .join('')
    .slice(0, 120)
  return safe || 'audio-file'
}

export function UploadPanel({
  capabilities,
  capabilitiesLoading,
  capabilitiesError,
  uploadError,
  submitting,
  onAnalyze,
}: UploadPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [mode, setMode] = useState<AnalysisMode>('fast')
  const [permissionConfirmed, setPermissionConfirmed] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [localError, setLocalError] = useState<string>()
  const [lyricalAnalysis, setLyricalAnalysis] = useState(false)
  const [genreAnalysis, setGenreAnalysis] = useState(false)
  const [lyricsConsent, setLyricsConsent] = useState(false)
  const [deriveThemes, setDeriveThemes] = useState(false)
  const [allowFallback, setAllowFallback] = useState(false)
  const lyricalAdapter = capabilities.lyricsAdapter
  const coreToolsAvailable = Boolean(capabilitiesError) || (!capabilitiesLoading && (
    capabilities.fastMode.available && capabilities.ffmpeg.available && capabilities.ffprobe.available
  ))
  const safeDisplayName = file
    ? sanitizeDisplayName(file.name)
    : ''

  const chooseFile = (next: File | undefined): void => {
    setLocalError(undefined)
    if (!next) return
    setPermissionConfirmed(false)
    if (next.size === 0) {
      setFile(null)
      setLocalError('That file is empty. Choose an audio file containing a signal.')
      return
    }
    if (next.size > capabilities.limits.maxUploadMb * 1024 * 1024) {
      setFile(null)
      setLocalError(`That file exceeds the ${capabilities.limits.maxUploadMb} MB upload limit.`)
      return
    }
    setFile(next)
  }

  const onDrop = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault()
    setDragging(false)
    chooseFile(event.dataTransfer.files[0])
  }

  return (
    <main className="upload-layout" id="main-content">
      <section className="hero-copy" aria-labelledby="upload-heading">
        <span className="eyebrow"><AudioLines aria-hidden="true" /> Private music intelligence</span>
        <h1 id="upload-heading">Hear the details.<br /><em>Shape the prompt.</em></h1>
        <p className="hero-copy__lede">
          Turn a track into a transparent musical map and an editable, Suno-ready prompt—without sending
          your audio anywhere else.
        </p>
        <div className="trust-grid">
          <div><LockKeyhole aria-hidden="true" /><span><strong>Local by design</strong><small>No hidden uploads or telemetry</small></span></div>
          <div><ShieldCheck aria-hidden="true" /><span><strong>Traceable facts</strong><small>Method and confidence for every claim</small></span></div>
          <div><Sparkles aria-hidden="true" /><span><strong>Original output</strong><small>Musical qualities, never identity</small></span></div>
        </div>
      </section>

      <section className="upload-card" aria-label="Start a new analysis">
        <div className="upload-card__heading">
          <div>
            <span className="step-pill">01</span>
            <h2>Choose your track</h2>
            <p>We validate the actual media stream after upload.</p>
          </div>
          <HardDrive aria-hidden="true" />
        </div>

        <div
          className={`dropzone ${dragging ? 'dropzone--active' : ''} ${file ? 'dropzone--selected' : ''}`}
          onDragEnter={(event) => { event.preventDefault(); setDragging(true) }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
          }}
          onDrop={onDrop}
        >
          <input
            ref={inputRef}
            id="audio-file"
            className="visually-hidden"
            aria-label="Audio file"
            type="file"
            accept=".wav,.flac,.mp3,.m4a,.aac,.ogg,audio/*"
            onChange={(event) => chooseFile(event.target.files?.[0])}
          />
          {file ? (
            <div className="selected-file">
              <span className="selected-file__icon"><FileAudio aria-hidden="true" /></span>
              <span className="selected-file__name"><strong>{safeDisplayName}</strong><small>{(file.size / 1024 / 1024).toFixed(1)} MB · Ready for content validation</small></span>
              <button
                className="icon-button"
                type="button"
                aria-label={`Remove ${safeDisplayName}`}
                onClick={() => {
                  setFile(null)
                  setPermissionConfirmed(false)
                  if (inputRef.current) inputRef.current.value = ''
                }}
              ><X aria-hidden="true" /></button>
            </div>
          ) : (
            <>
              <span className="dropzone__icon"><FolderOpen aria-hidden="true" /></span>
              <strong>Drop an audio file here</strong>
              <span>or</span>
              <Button type="button" onClick={() => inputRef.current?.click()}>Browse files</Button>
              <small>WAV · FLAC · MP3 · M4A/AAC · OGG</small>
            </>
          )}
        </div>
        <p className="limit-copy">Up to {capabilities.limits.maxUploadMb} MB and {formatDuration(capabilities.limits.maxDurationSeconds)}. Server-side ffprobe validation is authoritative.</p>

        {(localError ?? uploadError) ? <InlineNotice tone="error">{localError ?? uploadError}</InlineNotice> : null}
        {capabilitiesError ? <InlineNotice tone="warning"><WifiOff aria-hidden="true" /> {capabilitiesError} Limits shown are safe defaults; the server will confirm them.</InlineNotice> : null}
        {!capabilitiesError && !capabilitiesLoading && !coreToolsAvailable ? (
          <InlineNotice tone="error">FFmpeg, ffprobe, or Fast analysis is unavailable on the local service. Install the missing tool and refresh before analyzing.</InlineNotice>
        ) : null}

        <fieldset className="mode-picker">
          <legend><span className="step-pill">02</span> Choose analysis depth</legend>
          <label className={`mode-option ${mode === 'fast' ? 'mode-option--active' : ''}`}>
            <input type="radio" name="analysis-mode" value="fast" checked={mode === 'fast'} onChange={() => { setMode('fast'); setLyricalAnalysis(false); setLyricsConsent(false); setDeriveThemes(false) }} />
            <span className="mode-option__check"><Check aria-hidden="true" /></span>
            <span><strong>Fast</strong><small>CPU-friendly core analysis. Always offline and model-free.</small></span>
            <span className="mode-option__meta">Recommended</span>
          </label>
          <label className={`mode-option ${mode === 'deep' ? 'mode-option--active' : ''}`}>
            <input type="radio" name="analysis-mode" value="deep" checked={mode === 'deep'} onChange={() => setMode('deep')} />
            <span className="mode-option__check"><Check aria-hidden="true" /></span>
            <span><strong>Deep</strong><small>Enhanced stems and taggers when installed; core analysis remains available.</small></span>
            <span className="mode-option__meta">{capabilities.deepMode.available ? 'Available' : 'Falls back safely'}</span>
          </label>
        </fieldset>

        {mode === 'deep' ? (
          <div className="capability-list" aria-label="Deep mode capabilities">
            <div className="capability-list__heading"><Sparkles aria-hidden="true" /><strong>Enhanced capabilities</strong></div>
            {capabilities.deepMode.adapters.length > 0 ? capabilities.deepMode.adapters.map((adapter) => (
              <div key={adapter.id} className="capability-row">
                <span className={`status-dot ${adapter.available ? 'status-dot--on' : ''}`} />
                <span><strong>{adapter.name}</strong><small>{adapter.available ? 'Installed locally' : (adapter.reason ?? 'Not installed; core fallback will be used')}{adapter.selectedDevice ? ` · device ${adapter.selectedDevice}` : ''}{adapter.gpuDeviceName ? ` (${adapter.gpuDeviceName})` : ''}{adapter.fallbackReason ? ` · ${adapter.fallbackReason}` : ''}{adapter.diskImpactMb ? ` · ${adapter.diskImpactMb} MB` : ''}</small></span>
              </div>
            )) : <p className="muted">No optional adapters are installed. Deep requests transparently use the complete Fast analysis.</p>}
            {capabilities.optionalAnalyzers.length > 0 ? (
              <details className="method-details">
                <summary>Additional local analyzer capabilities</summary>
                {capabilities.optionalAnalyzers.map((analyzer) => (
                  <p key={analyzer.id}><strong>{analyzer.name}</strong> · {analyzer.available ? 'available' : 'unavailable'} · {analyzer.reason}</p>
                ))}
              </details>
            ) : null}
            <Toggle
              checked={lyricalAnalysis}
              onChange={setLyricalAnalysis}
              label="Analyze lyrical language and prosody"
              description={lyricalAdapter?.available ? 'Explicitly enables the installed local adapter; raw lyrics are never added to prompts.' : 'Unavailable until a documented local lyrical adapter is installed.'}
              disabled={!lyricalAdapter?.available}
            />
            {lyricalAnalysis ? (
              <>
                <label className="permission-check permission-check--nested">
                  <input type="checkbox" checked={lyricsConsent} onChange={(event) => setLyricsConsent(event.target.checked)} />
                  <span><strong>I have permission to create a private approximate transcript.</strong><small>Singing transcription can be wrong. Raw words stay in a separate artifact and never enter generated prompts.</small></span>
                </label>
                <Toggle
                  checked={deriveThemes}
                  onChange={setDeriveThemes}
                  label="Derive abstract lyrical themes locally"
                  description="Off by default. Uses an isolated local-only language-model request and never quotes transcript lines."
                  disabled={!capabilities.promptWriter?.available}
                />
              </>
            ) : null}
          </div>
        ) : null}

        <div className="capability-list" aria-label="Optional local features">
          <div className="capability-list__heading"><Sparkles aria-hidden="true" /><strong>Optional local features</strong></div>
          {[capabilities.genreTagger, capabilities.lyricsAdapter, capabilities.promptWriter].filter((item) => item != null).map((adapter) => (
            <div key={adapter?.id} className="capability-row">
              <span className={`status-dot ${adapter?.available ? 'status-dot--on' : ''}`} />
              <span><strong>{adapter?.name}</strong><small>{adapter?.available ? 'Ready' : adapter?.reason}{adapter?.modelId ? ` · ${adapter.modelId}` : ''}{adapter?.effectiveDevice ? ` · ${adapter.effectiveDevice}` : ''}{adapter?.diskImpactMb ? ` · about ${adapter.diskImpactMb} MB` : ''}</small></span>
            </div>
          ))}
          <Toggle
            checked={genreAnalysis}
            onChange={setGenreAnalysis}
            label="Genre and style tagging"
            description={capabilities.genreTagger?.available ? 'Runs hierarchical CLAP similarity on bounded local windows.' : 'Unavailable until the reviewed local genre model is explicitly installed.'}
            disabled={!capabilities.genreTagger?.available}
          />
          <Toggle
            checked={allowFallback}
            onChange={setAllowFallback}
            label="Allow explicit optional-feature fallback"
            description="Continue with working core features if a selected optional adapter becomes unavailable. Every fallback is shown."
          />
          <p className="muted">GPU queue: {capabilities.gpuTaskQueue?.active ?? 0} active, {capabilities.gpuTaskQueue?.waiting ?? 0} waiting · {capabilities.gpuTaskQueue?.policy ?? 'single-heavy-task'}.</p>
        </div>

        <label className="permission-check">
          <input type="checkbox" checked={permissionConfirmed} onChange={(event) => setPermissionConfirmed(event.target.checked)} />
          <span><strong>I have permission to analyze this audio.</strong><small>The file stays in this local application and is retained persistently until you explicitly delete it.</small></span>
        </label>

        <Button
          variant="primary"
          className="analyze-button"
          disabled={!file || !permissionConfirmed || !coreToolsAvailable || (lyricalAnalysis && !lyricsConsent)}
          busy={submitting}
          onClick={() => {
            if (file) onAnalyze(file, mode, {
              enableGenreAnalysis: genreAnalysis,
              enableLyricsAnalysis: lyricalAnalysis,
              lyricsConsentConfirmed: lyricsConsent,
              deriveLyricalThemes: deriveThemes,
              allowFeatureFallback: allowFallback,
            })
          }}
        >
          {submitting ? 'Uploading securely…' : 'Analyze track'}
        </Button>

        <div className="local-footer"><LockKeyhole aria-hidden="true" /><span><strong>Audio stays local</strong><small>{capabilities.networkFeaturesEnabled ? 'Optional network features are enabled and require separate consent.' : 'Network features are off. Normal analysis makes no outbound requests.'}</small></span></div>
      </section>
    </main>
  )
}
