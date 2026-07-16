import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Check,
  Clipboard,
  ClipboardCheck,
  Copy,
  Dice5,
  ListFilter,
  RefreshCcw,
  Sparkles,
} from 'lucide-react'
import type {
  AnalysisResult,
  Capabilities,
  GenerationIntent,
  PromptLength,
  PromptPackage,
  PromptPreferences,
  PromptEngineMode,
  GenreInterpretationMode,
  LyricsInfluenceMode,
} from '../types'
import { Button, InlineNotice, Modal, Toggle } from './ui'

const INTENTS: Array<{ value: GenerationIntent; label: string; detail: string }> = [
  { value: 'preserve_core_character', label: 'Preserve core character', detail: 'Stay close to supported groove, energy, and palette.' },
  { value: 'inspired_variation', label: 'Inspired variation', detail: 'Keep the foundation with more compositional freedom.' },
  { value: 'more_original', label: 'More original / looser', detail: 'Use the report as broad creative context.' },
  { value: 'genre_transfer', label: 'Genre transfer', detail: 'Carry musical motion into a target genre.' },
  { value: 'instrumental_reinterpretation', label: 'Instrumental reinterpretation', detail: 'Remove vocals and reinterpret the arrangement.' },
  { value: 'change_mood_preserve_groove', label: 'Change mood, preserve groove', detail: 'Retain rhythmic identity while redirecting mood.' },
  { value: 'change_instrumentation_preserve_structure', label: 'Change instruments, preserve structure', detail: 'Keep the section plan with a new palette.' },
  { value: 'custom', label: 'Custom', detail: 'Drive the result with the controls below.' },
]

const LENGTHS: Array<{ value: PromptLength; label: string }> = [
  { value: 'compact', label: 'Compact' },
  { value: 'balanced', label: 'Balanced' },
  { value: 'detailed', label: 'Detailed' },
  { value: 'custom', label: 'Custom maximum' },
]

function seededVariation(jobId: string, index: number): number {
  let hash = 2166136261
  for (const char of `${jobId}:${index}`) {
    hash ^= char.charCodeAt(0)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 1
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // Clipboard permissions can be denied in otherwise functional local contexts.
    }
  }
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  textarea.remove()
  if (!copied) throw new Error('Clipboard access is unavailable. Select the prompt and copy it manually.')
}

interface PromptWorkspaceProps {
  jobId: string
  analysis: AnalysisResult
  promptPackage?: PromptPackage
  capabilities: Capabilities
  onGenerate: (preferences: PromptPreferences) => Promise<PromptPackage>
}

type PendingReplacement = { kind: 'generate' | 'variation' } | { kind: 'alternative'; text: string; label: string }

export function PromptWorkspace({ jobId, analysis, promptPackage, capabilities, onGenerate }: PromptWorkspaceProps) {
  const [preferences, setPreferences] = useState<PromptPreferences>({
    outputLanguage: 'English',
    generationIntent: 'preserve_core_character',
    promptLength: 'balanced',
    includeBpm: true,
    includeKey: true,
    instrumental: false,
    creativity: 0.35,
    preserveEnergyArc: true,
    preserveInstrumentation: true,
    preserveStructure: true,
    preserveGroove: true,
    exclusions: promptPackage?.exclusions ?? [],
    disabledFeaturePaths: analysis.disabledFeaturePaths,
    userOverrides: {},
    promptEngineMode: promptPackage?.engineMode ?? 'reliable',
    genreInterpretationMode: 'strict_top',
    lyricsInfluenceMode: 'none',
    candidateCount: 3,
    lockSeed: false,
    lockedFeaturePaths: [],
    includeDetectedGenre: true,
    acceptedGenreIds: analysis.genreAnalysis
      ? [...analysis.genreAnalysis.broadCandidates, ...analysis.genreAnalysis.subgenreCandidates].filter((item) => item.accepted).map((item) => item.id)
      : [],
    includeLyricalThemes: false,
    desiredTransformations: [],
    variationSeed: promptPackage?.seed ?? undefined,
  })
  const [currentPackage, setCurrentPackage] = useState(promptPackage)
  const [editor, setEditor] = useState(promptPackage?.primaryPrompt ?? '')
  const [manualDirty, setManualDirty] = useState(false)
  const [packageInvalidated, setPackageInvalidated] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [generationError, setGenerationError] = useState<string>()
  const [variationIndex, setVariationIndex] = useState(1)
  const [pending, setPending] = useState<PendingReplacement>()
  const [copyStatus, setCopyStatus] = useState<string>()
  const [copyError, setCopyError] = useState<string>()
  const [exclusionsDraft, setExclusionsDraft] = useState((promptPackage?.exclusions ?? []).join('\n'))
  const lastPackage = useRef(promptPackage)

  useEffect(() => {
    setPreferences((current) => ({ ...current, disabledFeaturePaths: analysis.disabledFeaturePaths }))
  }, [analysis.disabledFeaturePaths])

  useEffect(() => {
    if (promptPackage === lastPackage.current) return
    const previousPackage = lastPackage.current
    lastPackage.current = promptPackage
    if (!promptPackage) {
      setCurrentPackage(undefined)
      if (previousPackage) {
        setPackageInvalidated(true)
        if (!manualDirty) {
          setEditor('')
          setExclusionsDraft('')
        }
      }
      return
    }
    setCurrentPackage(promptPackage)
    setExclusionsDraft(promptPackage.exclusions.join('\n'))
    setPackageInvalidated(false)
    if (!manualDirty) setEditor(promptPackage.primaryPrompt)
  }, [manualDirty, promptPackage])

  const maxCharacters = preferences.promptLength === 'custom' ? preferences.customMaxCharacters : undefined
  const exclusionLines = useMemo(
    () => exclusionsDraft.split(/\r?\n/).map((line) => line.trim()).filter(Boolean),
    [exclusionsDraft],
  )
  const exclusions = useMemo(
    () => exclusionLines.slice(0, 20).map((line) => line.slice(0, 200)),
    [exclusionLines],
  )
  const exclusionsAdjusted = exclusionLines.length > 20 || exclusionLines.some((line) => line.length > 200)

  const updatePreference = <Key extends keyof PromptPreferences>(key: Key, value: PromptPreferences[Key]): void => {
    setPreferences((current) => ({ ...current, [key]: value }))
  }

  const updateIntent = (intent: GenerationIntent): void => {
    setPreferences((current) => ({
      ...current,
      generationIntent: intent,
      instrumental: intent === 'instrumental_reinterpretation'
        ? true
        : current.generationIntent === 'instrumental_reinterpretation'
          ? false
          : current.instrumental,
      lyricsInfluenceMode: intent === 'instrumental_reinterpretation' ? 'none' : current.lyricsInfluenceMode,
      includeLyricalThemes: intent === 'instrumental_reinterpretation' ? false : current.includeLyricalThemes,
      preserveInstrumentation: intent === 'change_instrumentation_preserve_structure'
        ? false
        : current.generationIntent === 'change_instrumentation_preserve_structure'
          ? true
          : current.preserveInstrumentation,
      preserveStructure: intent === 'change_instrumentation_preserve_structure'
        ? true
        : current.preserveStructure,
      preserveGroove: intent === 'change_mood_preserve_groove'
        ? true
        : current.preserveGroove,
    }))
  }

  const runGenerate = async (variation: boolean): Promise<void> => {
    if (preferences.generationIntent === 'genre_transfer' && !preferences.targetGenre?.trim()) {
      setGenerationError('Enter a target genre before generating a genre transfer.')
      setPending(undefined)
      return
    }
    if (preferences.promptLength === 'custom' && (
      preferences.customMaxCharacters === undefined ||
      preferences.customMaxCharacters < 200 ||
      preferences.customMaxCharacters > 4000
    )) {
      setGenerationError('Choose a custom maximum between 200 and 4,000 characters.')
      setPending(undefined)
      return
    }
    if (preferences.targetDuration !== undefined && (
      !Number.isFinite(preferences.targetDuration) ||
      preferences.targetDuration <= 0 ||
      preferences.targetDuration > 7200
    )) {
      setGenerationError('Choose a target duration between 1 and 7,200 seconds.')
      setPending(undefined)
      return
    }
    setGenerating(true)
    setGenerationError(undefined)
    setPending(undefined)
    try {
      const seed = variation
        ? seededVariation(jobId, variationIndex)
        : preferences.variationSeed
      const next = await onGenerate({
        ...preferences,
        exclusions,
        disabledFeaturePaths: analysis.disabledFeaturePaths,
        variationSeed: seed,
      })
      setCurrentPackage(next)
      setEditor(next.primaryPrompt)
      setExclusionsDraft(next.exclusions.join('\n'))
      setManualDirty(false)
      setPackageInvalidated(false)
      if (variation) setVariationIndex((index) => index + 1)
      updatePreference('variationSeed', next.seed ?? seed)
    } catch (caught) {
      setGenerationError(caught instanceof Error ? caught.message : 'The prompt could not be generated.')
    } finally {
      setGenerating(false)
    }
  }

  const requestGenerate = (variation: boolean): void => {
    if (manualDirty) setPending({ kind: variation ? 'variation' : 'generate' })
    else void runGenerate(variation)
  }

  const selectAlternative = (text: string, label: string): void => {
    if (manualDirty) {
      setPending({ kind: 'alternative', text, label })
      return
    }
    setEditor(text)
    setManualDirty(false)
  }

  const confirmReplacement = (): void => {
    if (!pending) return
    if (pending.kind === 'alternative') {
      setEditor(pending.text)
      setManualDirty(false)
      setPending(undefined)
    } else {
      void runGenerate(pending.kind === 'variation')
    }
  }

  const copy = async (value: string, label: string): Promise<void> => {
    setCopyError(undefined)
    try {
      await copyText(value)
      setCopyStatus(label)
      window.setTimeout(() => setCopyStatus(undefined), 2200)
    } catch (caught) {
      setCopyError(caught instanceof Error ? caught.message : 'Copy failed. Select the text and copy it manually.')
    }
  }

  const withExclusions = exclusions.length > 0 ? `${editor}\n\nAvoid: ${exclusions.join('; ')}.` : editor
  const selectedIntent = INTENTS.find((intent) => intent.value === preferences.generationIntent)
  const writerReady = Boolean(capabilities.promptWriter?.available)
  const promptModes: Array<{ value: PromptEngineMode; label: string; detail: string; requiresLlm: boolean }> = [
    { value: 'reliable', label: 'Reliable', detail: 'Deterministic typed composition; stable for the same analysis, preferences, and seed.', requiresLlm: false },
    { value: 'creative', label: 'Creative', detail: 'Sampled local GPU wording and arrangement variation kept close to accepted evidence.', requiresLlm: true },
    { value: 'experimental', label: 'Experimental', detail: 'Broader sampled reinterpretation of unlocked traits while preserving every lock.', requiresLlm: true },
  ]
  const newSeed = (): void => {
    const values = new Uint32Array(1)
    window.crypto.getRandomValues(values)
    updatePreference('variationSeed', (values[0] ?? seededVariation(jobId, variationIndex)) & 0x7fffffff)
  }

  return (
    <section className="prompt-workspace" aria-labelledby="prompt-title">
      <div className="section-heading">
        <div><span className="eyebrow"><Sparkles aria-hidden="true" /> Local prompt engines</span><h2 id="prompt-title">Shape your generation prompt</h2><p>Analysis remains stable; interpretation may be creative. Manual edits are always protected.</p></div>
        <span className="local-chip">{preferences.promptEngineMode === 'reliable' ? 'No LLM required' : writerReady ? `Local GPU Â· ${capabilities.promptWriter?.modelId ?? 'ready'}` : 'Reliable fallback'}</span>
      </div>

      <div className="prompt-layout">
        <aside className="prompt-controls" aria-label="Prompt preferences">
          <div className="control-section prompt-engine-section">
            <span className="control-section__number">00</span>
            <h3>Prompt engine</h3>
            <div className="prompt-engine-options">
              {promptModes.map((engine) => {
                const unavailable = engine.requiresLlm && !writerReady
                return (
                  <label key={engine.value} className={`mode-option ${preferences.promptEngineMode === engine.value ? 'mode-option--active' : ''} ${unavailable ? 'mode-option--disabled' : ''}`}>
                    <input type="radio" name="prompt-engine" value={engine.value} checked={preferences.promptEngineMode === engine.value} disabled={unavailable} onChange={() => updatePreference('promptEngineMode', engine.value)} />
                    <span className="mode-option__check"><Check aria-hidden="true" /></span>
                    <span><strong>{engine.label}</strong><small>{engine.detail} {engine.requiresLlm ? 'Requires the local LLM; failures use Reliable.' : 'Works offline without a GPU and does not vary unless a seed changes.'}</small></span>
                    <span className="mode-option__meta">{unavailable ? 'Unavailable' : engine.requiresLlm ? 'GPU sampled' : 'Always ready'}</span>
                  </label>
                )
              })}
            </div>
            <div className="two-fields">
              <div className="field-stack"><label htmlFor="candidate-count">Candidate count</label><select id="candidate-count" value={preferences.candidateCount} onChange={(event) => updatePreference('candidateCount', Number(event.target.value) as 1 | 3)}><option value={1}>1</option><option value={3}>3</option></select></div>
              <div className="field-stack"><label htmlFor="variation-seed">Optional seed</label><input id="variation-seed" type="number" min={0} max={2147483647} value={preferences.variationSeed ?? ''} onChange={(event) => updatePreference('variationSeed', event.target.value ? Number(event.target.value) : undefined)} /></div>
            </div>
            <div className="candidate-actions"><Button variant="ghost" icon={<Dice5 aria-hidden="true" />} onClick={newSeed}>New seed</Button><Button variant="ghost" disabled={currentPackage?.seed == null} onClick={() => updatePreference('variationSeed', currentPackage?.seed ?? undefined)}>Reuse this seed</Button></div>
          </div>
          <div className="control-section">
            <span className="control-section__number">01</span>
            <div className="field-stack">
              <label htmlFor="intent">Generation intent</label>
              <select id="intent" value={preferences.generationIntent} onChange={(event) => updateIntent(event.target.value as GenerationIntent)}>
                {INTENTS.map((intent) => <option value={intent.value} key={intent.value}>{intent.label}</option>)}
              </select>
              <small>{selectedIntent?.detail}</small>
            </div>
            <div className="two-fields">
              <div className="field-stack">
                <label htmlFor="prompt-length">Prompt length</label>
                <select id="prompt-length" value={preferences.promptLength} onChange={(event) => {
                  const length = event.target.value as PromptLength
                  updatePreference('promptLength', length)
                  if (length === 'custom' && preferences.customMaxCharacters === undefined) updatePreference('customMaxCharacters', 1000)
                }}>
                  {LENGTHS.map((length) => <option key={length.value} value={length.value}>{length.label}</option>)}
                </select>
              </div>
              <div className="field-stack">
                <label htmlFor="output-language">Language</label>
                <input id="output-language" value={preferences.outputLanguage} maxLength={60} onChange={(event) => updatePreference('outputLanguage', event.target.value)} />
              </div>
            </div>
            {preferences.promptLength === 'custom' ? (
              <div className="field-stack">
                <label htmlFor="max-characters">Maximum characters</label>
                <input id="max-characters" type="number" min={200} max={4000} value={preferences.customMaxCharacters ?? 1000} onChange={(event) => updatePreference('customMaxCharacters', Number(event.target.value))} />
                <small>The composer budgets complete phrases and never truncates mid-sentence.</small>
              </div>
            ) : null}
          </div>

          <div className="control-section">
            <span className="control-section__number">02</span>
            <h3>Musical facts</h3>
            <Toggle checked={preferences.includeBpm} onChange={(value) => updatePreference('includeBpm', value)} label="Include BPM" description="Only when confidence supports it" />
            <Toggle checked={preferences.includeKey} onChange={(value) => updatePreference('includeKey', value)} label="Include key / mode" description="Weak tonal estimates stay omitted" />
            <Toggle
              checked={preferences.instrumental}
              onChange={(value) => setPreferences((current) => ({
                ...current,
                instrumental: value,
                lyricsInfluenceMode: value ? 'none' : current.lyricsInfluenceMode,
                includeLyricalThemes: value ? false : current.includeLyricalThemes,
              }))}
              label="Instrumental output"
              description={preferences.generationIntent === 'instrumental_reinterpretation' ? 'Required by the selected intent' : 'Suppress vocal-delivery instructions'}
              disabled={preferences.generationIntent === 'instrumental_reinterpretation'}
            />
            <div className="field-stack">
              <label htmlFor="genre-use">Genre use in prompt</label>
              <select id="genre-use" value={preferences.genreInterpretationMode} onChange={(event) => updatePreference('genreInterpretationMode', event.target.value as GenreInterpretationMode)}>
                <option value="strict_top">Strict top accepted genre</option>
                <option value="blend">Compatible accepted blend</option>
                <option value="user_selected_only">User-selected only</option>
                <option value="disabled">Disabled</option>
              </select>
              <small>{preferences.acceptedGenreIds.length} accepted genre candidate{preferences.acceptedGenreIds.length === 1 ? '' : 's'} available.</small>
            </div>
            <div className="field-stack">
              <label htmlFor="lyrics-influence">Lyrics influence</label>
              <select id="lyrics-influence" value={preferences.lyricsInfluenceMode} disabled={preferences.instrumental} onChange={(event) => {
                const value = event.target.value as LyricsInfluenceMode
                setPreferences((current) => ({ ...current, lyricsInfluenceMode: value, includeLyricalThemes: value === 'abstract_themes' }))
              }}>
                <option value="none">None</option>
                <option value="prosody_only">Prosody only</option>
                <option value="abstract_themes">Approved abstract themes</option>
                <option value="user_written_direction">My written direction</option>
              </select>
              <small>Raw transcript lines are never inserted or sent as prompt evidence.</small>
            </div>
            {preferences.lyricsInfluenceMode === 'user_written_direction' && !preferences.instrumental ? <div className="field-stack"><label htmlFor="lyrical-direction">Your lyrical direction</label><input id="lyrical-direction" maxLength={240} value={preferences.userWrittenLyricalDirection ?? ''} onChange={(event) => updatePreference('userWrittenLyricalDirection', event.target.value || undefined)} /></div> : null}
          </div>

          <div className="control-section">
            <span className="control-section__number">03</span>
            <h3>Creative direction</h3>
            <div className="two-fields">
              <div className="field-stack"><label htmlFor="target-genre">Target genre</label><input id="target-genre" placeholder={preferences.generationIntent === 'genre_transfer' ? 'Required for genre transfer' : 'Optional'} required={preferences.generationIntent === 'genre_transfer'} maxLength={120} value={preferences.targetGenre ?? ''} onChange={(event) => updatePreference('targetGenre', event.target.value || undefined)} /></div>
              <div className="field-stack"><label htmlFor="target-mood">Target mood</label><input id="target-mood" placeholder="Optional" maxLength={120} value={preferences.targetMood ?? ''} onChange={(event) => updatePreference('targetMood', event.target.value || undefined)} /></div>
            </div>
            <div className="field-stack"><label htmlFor="target-duration">Target duration in seconds</label><input id="target-duration" type="number" min={1} max={7200} placeholder="Optional" value={preferences.targetDuration ?? ''} onChange={(event) => updatePreference('targetDuration', event.target.value ? Number(event.target.value) : undefined)} /><small>Used as arrangement guidance, not a predicted completion time.</small></div>
            <div className="field-stack"><label htmlFor="vocal-presentation">Desired vocal presentation</label><input id="vocal-presentation" placeholder="e.g. airy low register, layered chorus" maxLength={200} value={preferences.desiredVocalPresentation ?? ''} onChange={(event) => updatePreference('desiredVocalPresentation', event.target.value || undefined)} disabled={preferences.instrumental} /></div>
            <div className="field-stack"><label htmlFor="desired-transformations">Desired transformations</label><textarea id="desired-transformations" rows={3} placeholder="One bounded direction per line" value={preferences.desiredTransformations.join('\n')} onChange={(event) => updatePreference('desiredTransformations', event.target.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean).slice(0, 12))} /></div>
            <div className="field-stack range-field">
              <label htmlFor="creativity"><span>Creativity</span><output>{Math.round(preferences.creativity * 100)}%</output></label>
              <input id="creativity" type="range" min={0} max={1} step={0.05} value={preferences.creativity} onChange={(event) => updatePreference('creativity', Number(event.target.value))} />
              <div><small>Literal</small><small>Adventurous</small></div>
            </div>
          </div>

          <div className="control-section">
            <span className="control-section__number">04</span>
            <h3>Preserve</h3>
            <Toggle checked={preferences.preserveStructure} onChange={(value) => updatePreference('preserveStructure', value)} label="Section structure" disabled={preferences.generationIntent === 'change_instrumentation_preserve_structure'} />
            <Toggle checked={preferences.preserveGroove} onChange={(value) => updatePreference('preserveGroove', value)} label="Groove" disabled={preferences.generationIntent === 'change_mood_preserve_groove'} />
            <Toggle checked={preferences.preserveInstrumentation} onChange={(value) => updatePreference('preserveInstrumentation', value)} label="Instrumentation" disabled={preferences.generationIntent === 'change_instrumentation_preserve_structure'} />
            <Toggle checked={preferences.preserveEnergyArc} onChange={(value) => updatePreference('preserveEnergyArc', value)} label="Energy arc" />
          </div>

          <div className="control-section">
            <span className="control-section__number">05</span>
            <div className="field-stack">
              <label htmlFor="exclusions">Exclusions</label>
              <textarea id="exclusions" rows={5} value={exclusionsDraft} onChange={(event) => setExclusionsDraft(event.target.value)} placeholder={'One per line, e.g.\nNo long intro\nAvoid overly bright mastering'} />
              <small>Kept separate by default because generation tools may not have a dedicated negative-prompt field.</small>
              {exclusionsAdjusted ? <small className="field-error">Up to 20 exclusions are used; each is limited to 200 characters.</small> : null}
            </div>
          </div>
        </aside>

        <div className="prompt-editor-panel">
          <div className="prompt-actions">
            <Button variant="primary" icon={<RefreshCcw aria-hidden="true" />} busy={generating} onClick={() => requestGenerate(false)}>Generate candidates</Button>
            <Button icon={<Dice5 aria-hidden="true" />} busy={generating} onClick={() => requestGenerate(true)}>Generate another set</Button>
            <span className="seed-note">{preferences.variationSeed == null ? 'A seed will be returned' : `Seed ${preferences.variationSeed} Â· best-effort reproduction`}</span>
          </div>

          {generationError ? <InlineNotice tone="error">{generationError}</InlineNotice> : null}
          {packageInvalidated ? <InlineNotice tone="warning">Analysis facts changed. {manualDirty ? 'Your manual text is preserved, but regenerate before treating it as synchronized.' : 'Generate a new prompt before copying.'}</InlineNotice> : null}
          {analysis.disabledFeaturePaths.length > 0 ? <InlineNotice tone="info"><ListFilter aria-hidden="true" /> {analysis.disabledFeaturePaths.length} disabled fact{analysis.disabledFeaturePaths.length === 1 ? '' : 's'} will be omitted.</InlineNotice> : null}

          <div className="editor-heading">
            <div><span className="eyebrow">Primary prompt</span><h3>Suno-ready paragraph</h3></div>
            {manualDirty ? <span className="edited-chip">Manual edits protected</span> : currentPackage ? <span className="accepted-chip"><Check aria-hidden="true" /> Synced</span> : <span className="edited-chip">Needs generation</span>}
          </div>
          <label className="visually-hidden" htmlFor="primary-prompt">Editable primary prompt</label>
          <textarea
            id="primary-prompt"
            className="prompt-textarea"
            value={editor}
            onChange={(event) => { setEditor(event.target.value); setManualDirty(true) }}
            rows={14}
            spellCheck="true"
          />
          <div className="editor-footer">
            <span className={maxCharacters && editor.length > maxCharacters ? 'char-count char-count--over' : 'char-count'}>{editor.length}{maxCharacters ? ` / ${maxCharacters}` : ''} characters</span>
            <div>
              <Button icon={copyStatus === 'Prompt copied' ? <ClipboardCheck aria-hidden="true" /> : <Copy aria-hidden="true" />} disabled={!editor.trim()} onClick={() => void copy(editor, 'Prompt copied')}>Copy prompt</Button>
              <Button icon={<Clipboard aria-hidden="true" />} disabled={!editor.trim()} onClick={() => void copy(withExclusions, 'Prompt with exclusions copied')}>Copy with exclusions</Button>
            </div>
          </div>
          {maxCharacters && editor.length > maxCharacters ? <InlineNotice tone="warning">Your manual edit exceeds the selected maximum. Regeneration will return a phrase-budgeted version.</InlineNotice> : null}
          <div className="copy-feedback" aria-live="polite">{copyStatus ? <span><ClipboardCheck aria-hidden="true" /> {copyStatus}</span> : null}{copyError ? <span className="field-error">{copyError}</span> : null}</div>

          <section className="exclusions-preview" aria-labelledby="exclusions-heading">
            <div><h3 id="exclusions-heading">Exclusions</h3><span>{exclusions.length} instruction{exclusions.length === 1 ? '' : 's'}</span></div>
            {exclusions.length ? <ul>{exclusions.map((exclusion) => <li key={exclusion}>{exclusion}</li>)}</ul> : <p className="muted">No exclusions added.</p>}
            <Button icon={<Copy aria-hidden="true" />} disabled={exclusions.length === 0} onClick={() => void copy(exclusions.join('; '), 'Exclusions copied')}>Copy exclusions separately</Button>
          </section>

          {currentPackage ? (
            <>
              {currentPackage.warnings.map((warning) => <InlineNotice key={warning} tone="warning">{warning}</InlineNotice>)}
              {currentPackage.validationWarnings.map((warning) => <InlineNotice key={warning} tone="warning">{warning}</InlineNotice>)}
              {currentPackage.deterministicFallbackUsed ? <InlineNotice tone="warning">One or more sampled candidates failed validation or the local writer was unavailable. Reliable deterministic fallback behavior was used.</InlineNotice> : null}
              {currentPackage.candidates.length > 0 ? (
                <section className="candidate-comparison" aria-labelledby="candidate-comparison-heading">
                  <div className="subsection-heading"><h3 id="candidate-comparison-heading">Candidate comparison</h3><p>Candidates differ in wording, arrangement emphasis, and production emphasis while sharing the same accepted evidence.</p></div>
                  <div className="candidate-card-grid">
                    {currentPackage.candidates.map((candidate) => (
                      <article key={candidate.id} className={currentPackage.selectedCandidateId === candidate.id ? 'candidate-card candidate-card--selected' : 'candidate-card'}>
                        <span className="eyebrow">{candidate.shortTitle}</span>
                        <p>{candidate.prompt}</p>
                        <div className="candidate-actions"><Button variant="primary" onClick={() => selectAlternative(candidate.prompt, candidate.shortTitle)}>Use this prompt</Button><Button variant="ghost" icon={<Copy aria-hidden="true" />} onClick={() => void copy(candidate.prompt, `${candidate.shortTitle} copied`)}>Copy</Button></div>
                        <details><summary>Facts used ({candidate.factsUsed.length})</summary><div>{candidate.factsUsed.map((fact) => <code key={fact}>{fact}</code>)}</div></details>
                        {candidate.warnings.map((warning) => <small key={warning}>{warning}</small>)}
                      </article>
                    ))}
                  </div>
                </section>
              ) : null}
              <section className="prompt-alternatives" aria-labelledby="alternatives-heading">
                <div className="subsection-heading"><h3 id="alternatives-heading">Prompt alternatives</h3><p>Use one as your editable prompt; changes are never automatic.</p></div>
                <article><span className="eyebrow">Compact</span><p>{currentPackage.compactPrompt}</p><Button variant="ghost" onClick={() => selectAlternative(currentPackage.compactPrompt, 'compact prompt')}>Use compact</Button></article>
                <article><span className="eyebrow">Detailed</span><p>{currentPackage.detailedPrompt}</p><Button variant="ghost" onClick={() => selectAlternative(currentPackage.detailedPrompt, 'detailed prompt')}>Use detailed</Button></article>
              </section>

              {currentPackage.arrangementBlueprint.length > 0 ? (
                <details className="prompt-details" open>
                  <summary>Arrangement blueprint</summary>
                  <ol>{currentPackage.arrangementBlueprint.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ol>
                </details>
              ) : null}

              <details className="prompt-details">
                <summary>Why this phrase? <span>{currentPackage.rationale.length} explanations</span></summary>
                <div className="rationale-list">
                  {currentPackage.rationale.length > 0 ? currentPackage.rationale.map((item, index) => (
                    <article key={`${index}-${item.phrase}`}><q>{item.phrase}</q><div>{item.factPaths.map((path) => <code key={path}>{path}</code>)}</div></article>
                  )) : <p className="muted">The composer did not return phrase-level rationale.</p>}
                </div>
              </details>

              <details className="prompt-details">
                <summary>Facts omitted <span>{currentPackage.factsOmitted.length}</span></summary>
                <ul>{currentPackage.factsOmitted.map((item) => <li key={item.path}><code>{item.path}</code> — {item.reason}</li>)}</ul>
              </details>
              <details className="prompt-details">
                <summary>Facts used <span>{currentPackage.factsUsed.length}</span></summary>
                <div className="rationale-list"><article><div>{currentPackage.factsUsed.map((path) => <code key={path}>{path}</code>)}</div></article></div>
              </details>
              <details className="prompt-details">
                <summary>Prompt-engine diagnostics</summary>
                <dl>
                  <div><dt>Engine</dt><dd>{currentPackage.engineMode}</dd></div>
                  <div><dt>Model</dt><dd>{currentPackage.modelId}</dd></div>
                  <div><dt>Seed</dt><dd>{currentPackage.seed ?? 'not used'}</dd></div>
                  <div><dt>Sampling</dt><dd>{currentPackage.generationParameters.sampling ? 'enabled' : 'disabled'}</dd></div>
                  <div><dt>Temperature / top-p</dt><dd>{currentPackage.generationParameters.temperature} / {currentPackage.generationParameters.topP}</dd></div>
                  <div><dt>GPU queue</dt><dd>{capabilities.gpuTaskQueue?.active ?? 0} active Â· {capabilities.gpuTaskQueue?.waiting ?? 0} waiting</dd></div>
                </dl>
              </details>
            </>
          ) : (
            <div className="empty-prompt"><Sparkles aria-hidden="true" /><h3>Ready when you are</h3><p>Adjust the controls, then generate a deterministic prompt from the reviewed facts.</p></div>
          )}

          <InlineNotice tone="warning"><AlertTriangle aria-hidden="true" /><span>The composer requests an original melody, arrangement, and lyrics. It excludes the filename, private metadata, raw lyrics, exact melody, and complete chord sequence.</span></InlineNotice>
        </div>
      </div>

      <Modal
        open={Boolean(pending)}
        title="Replace your manual edits?"
        description={`Your edited prompt will be replaced${pending?.kind === 'alternative' ? ` by the ${pending.label}` : ' by newly generated text'}. This cannot be undone.`}
        onClose={() => setPending(undefined)}
        footer={<><Button variant="ghost" onClick={() => setPending(undefined)}>Keep my edits</Button><Button variant="primary" onClick={confirmReplacement}>Replace prompt</Button></>}
      >
        <InlineNotice tone="warning">Copy your current text first if you want to keep it outside TrackPrompt Studio.</InlineNotice>
      </Modal>
    </section>
  )
}
