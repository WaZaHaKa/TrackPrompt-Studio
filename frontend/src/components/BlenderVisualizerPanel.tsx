import {
  type MutableRefObject,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Box, Download, Film, RotateCcw, SlidersHorizontal } from 'lucide-react'

import { exportVisualCues, resolveVisualizerConfig } from '../api'
import {
  isBlenderVisualizerPreset,
  type BlenderVisualizerPreset,
  type Capabilities,
  type SpaceJourneyPalette,
  type SpaceJourneyParameters,
  type VisualCuePreferences,
  type VisualCurveDetail,
  type VisualizerConfigRequest,
} from '../types'
import {
  cloneDefaultSpaceJourneyParameters,
  DEFAULT_VISUALIZER_SEED,
  SPACE_JOURNEY_PARAMETER_BOUNDS,
  validateSpaceJourneyParameters,
  VISUALIZER_CONFIG_SCHEMA_VERSION,
} from '../visualizer'
import { Button, InlineNotice } from './ui'

const DEFAULT_PREFERENCES: VisualCuePreferences = {
  fps: 30,
  curveDetail: 'balanced',
  includeBeats: true,
  includeOnsets: true,
  includeStemEvidence: true,
  includeCurves: true,
}

const PRESET_LABELS: Record<BlenderVisualizerPreset, string> = {
  'abstract-geometry': 'Abstract Geometry',
  'space-journey': 'Space Journey',
}

type NumericSpaceJourneyParameter = Exclude<keyof SpaceJourneyParameters, 'palette'>

interface RangeControlProps {
  id: string
  label: string
  parameter: NumericSpaceJourneyParameter
  value: number
  help: string
  error?: string
  onChange: (value: number) => void
}

function formatControlValue(value: number): string {
  return value.toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1')
}

function RangeControl({ id, label, parameter, value, help, error, onChange }: RangeControlProps) {
  const bounds = SPACE_JOURNEY_PARAMETER_BOUNDS[parameter]
  const descriptionId = `${id}-help`
  const errorId = `${id}-error`
  return (
    <div className="field-stack range-field">
      <div className="range-field__heading">
        <label htmlFor={id}>{label}</label>
        <output aria-live="polite">{formatControlValue(value)}</output>
      </div>
      <input
        id={id}
        type="range"
        min={bounds.min}
        max={bounds.max}
        step={bounds.step}
        value={value}
        aria-describedby={`${descriptionId}${error ? ` ${errorId}` : ''}`}
        aria-invalid={error ? true : undefined}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
      <small id={descriptionId}>{help}</small>
      {error ? <small className="field-error" id={errorId}>{error}</small> : null}
    </div>
  )
}

function downloadBlob(
  blob: Blob,
  filename: string,
  objectUrl: MutableRefObject<string | undefined>,
): void {
  if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
  objectUrl.current = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl.current
  anchor.download = filename
  anchor.style.display = 'none'
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
}

interface BlenderVisualizerPanelProps {
  jobId: string
  capabilities: Capabilities
}

export function BlenderVisualizerPanel({ jobId, capabilities }: BlenderVisualizerPanelProps) {
  const [preferences, setPreferences] = useState(DEFAULT_PREFERENCES)
  const [preset, setPreset] = useState<BlenderVisualizerPreset>('abstract-geometry')
  const [spaceParameters, setSpaceParameters] = useState(cloneDefaultSpaceJourneyParameters)
  const [exportingCues, setExportingCues] = useState(false)
  const [resolvingConfig, setResolvingConfig] = useState(false)
  const [cueError, setCueError] = useState<string>()
  const [cueSummary, setCueSummary] = useState<string>()
  const [configError, setConfigError] = useState<string>()
  const [configSummary, setConfigSummary] = useState<string>()
  const [configWarnings, setConfigWarnings] = useState<string[]>([])
  const cueObjectUrl = useRef<string>()
  const configObjectUrl = useRef<string>()

  const supportedPresets = useMemo(() => {
    const values = capabilities.blenderVisualizerPresets.filter(isBlenderVisualizerPreset)
    return values.includes('abstract-geometry')
      ? values
      : ['abstract-geometry' as const, ...values]
  }, [capabilities.blenderVisualizerPresets])
  const validationErrors = useMemo(
    () => preset === 'space-journey' ? validateSpaceJourneyParameters(spaceParameters) : {},
    [preset, spaceParameters],
  )
  const configValid = Object.keys(validationErrors).length === 0
  const configResolverAvailable = capabilities.blenderVisualizerConfigSchemaVersion
    === VISUALIZER_CONFIG_SCHEMA_VERSION
  const busy = exportingCues || resolvingConfig

  useEffect(() => {
    if (!supportedPresets.includes(preset)) setPreset('abstract-geometry')
  }, [preset, supportedPresets])

  useEffect(() => () => {
    if (cueObjectUrl.current) URL.revokeObjectURL(cueObjectUrl.current)
    if (configObjectUrl.current) URL.revokeObjectURL(configObjectUrl.current)
  }, [])

  const clearConfigStatus = (): void => {
    setConfigError(undefined)
    setConfigSummary(undefined)
    setConfigWarnings([])
  }

  const selectPreset = (value: string): void => {
    if (!isBlenderVisualizerPreset(value) || !supportedPresets.includes(value)) return
    setPreset(value)
    clearConfigStatus()
  }

  const updateSpaceParameter = <Key extends keyof SpaceJourneyParameters,>(
    key: Key,
    value: SpaceJourneyParameters[Key],
  ): void => {
    setSpaceParameters((current) => ({ ...current, [key]: value }))
    clearConfigStatus()
  }

  const resetSpaceParameters = (): void => {
    setSpaceParameters(cloneDefaultSpaceJourneyParameters())
    clearConfigStatus()
  }

  const updateCuePreferences = (
    update: (current: VisualCuePreferences) => VisualCuePreferences,
  ): void => {
    setPreferences(update)
    setCueError(undefined)
    setCueSummary(undefined)
  }

  const exportCues = async (): Promise<void> => {
    setExportingCues(true)
    setCueError(undefined)
    setCueSummary(undefined)
    try {
      const result = await exportVisualCues(jobId, preferences)
      downloadBlob(result.blob, result.filename, cueObjectUrl)
      setCueSummary(
        `Exported ${result.cueSheet.beats.length} beats, ${result.cueSheet.onsets.length} onsets, ${result.cueSheet.sections.length} sections, ${result.cueSheet.transitions.length} transitions, and ${Object.keys(result.cueSheet.curves).length} curves.`,
      )
    } catch (caught) {
      setCueError(caught instanceof Error ? caught.message : 'The visual cue sheet could not be exported.')
    } finally {
      setExportingCues(false)
    }
  }

  const exportConfig = async (): Promise<void> => {
    if (!configValid) {
      setConfigError('Correct the highlighted visualizer settings before downloading the configuration.')
      return
    }
    setResolvingConfig(true)
    clearConfigStatus()
    try {
      const request: VisualizerConfigRequest = preset === 'space-journey'
        ? {
            schemaVersion: VISUALIZER_CONFIG_SCHEMA_VERSION,
            preset,
            parameters: { ...spaceParameters },
            seed: DEFAULT_VISUALIZER_SEED,
          }
        : {
            schemaVersion: VISUALIZER_CONFIG_SCHEMA_VERSION,
            preset,
            seed: DEFAULT_VISUALIZER_SEED,
          }
      const resolved = await resolveVisualizerConfig(request)
      if (resolved.preset === 'space-journey') setSpaceParameters(resolved.parameters)
      downloadBlob(
        new Blob([JSON.stringify(resolved, null, 2)], { type: 'application/json' }),
        'visualizer-config.resolved.json',
        configObjectUrl,
      )
      const defaults = resolved.defaultedParameters.length > 0
        ? ` The local service supplied ${resolved.defaultedParameters.length} default value${resolved.defaultedParameters.length === 1 ? '' : 's'}.`
        : ''
      setConfigSummary(`${PRESET_LABELS[resolved.preset]} configuration validated and downloaded.${defaults}`)
      setConfigWarnings(resolved.warnings)
    } catch (caught) {
      setConfigError(caught instanceof Error ? caught.message : 'The visualizer configuration could not be validated.')
    } finally {
      setResolvingConfig(false)
    }
  }

  const toggle = (key: 'includeBeats' | 'includeOnsets' | 'includeCurves') => {
    updateCuePreferences((current) => ({ ...current, [key]: !current[key] }))
  }

  return (
    <section className="visualizer-panel" aria-labelledby="blender-visualizer-heading">
      <div className="visualizer-panel__intro">
        <span className="eyebrow"><Box aria-hidden="true" /> Procedural video export</span>
        <h2 id="blender-visualizer-heading">Blender Visualizer</h2>
        <p>Download a compact audio-reactive cue sheet and a separately validated preset configuration. Blender is not launched here. The local runner creates a bounded preview by default; it never starts a full-track render automatically.</p>
        <span className="local-chip"><Film aria-hidden="true" /> {PRESET_LABELS[preset]} preset</span>
      </div>

      <div className="visualizer-panel__controls">
        <label className="field-stack visualizer-panel__preset" htmlFor="visualizer-preset">
          <span>Visualizer preset</span>
          <select
            id="visualizer-preset"
            aria-label="Visualizer preset"
            value={preset}
            disabled={busy}
            onChange={(event) => selectPreset(event.currentTarget.value)}
          >
            {supportedPresets.map((value) => (
              <option key={value} value={value}>{PRESET_LABELS[value]}</option>
            ))}
          </select>
          <small>Abstract Geometry remains the default. Space Journey adds bounded cinematic controls.</small>
        </label>

        {preset === 'space-journey' ? (
          <div className="visualizer-panel__config" aria-label="Space Journey controls">
            <div className="visualizer-panel__config-heading">
              <span><SlidersHorizontal aria-hidden="true" /><strong>Space Journey settings</strong></span>
              <Button
                variant="ghost"
                icon={<RotateCcw aria-hidden="true" />}
                disabled={busy || !configResolverAvailable}
                onClick={resetSpaceParameters}
              >
                Reset Space Journey defaults
              </Button>
            </div>

            <fieldset className="visualizer-control-group" disabled={busy || !configResolverAvailable}>
              <legend>Camera</legend>
              <div className="visualizer-control-grid">
                <RangeControl id="camera-distance" label="Camera distance" parameter="cameraDistance" value={spaceParameters.cameraDistance} help="Frames the destination from 8 to 40 scene units." error={validationErrors.cameraDistance} onChange={(value) => updateSpaceParameter('cameraDistance', value)} />
                <RangeControl id="camera-orbit-speed" label="Camera orbit speed" parameter="cameraOrbitSpeed" value={spaceParameters.cameraOrbitSpeed} help="Sets a slow macro orbit from stationary to 0.5." error={validationErrors.cameraOrbitSpeed} onChange={(value) => updateSpaceParameter('cameraOrbitSpeed', value)} />
              </div>
            </fieldset>

            <fieldset className="visualizer-control-group" disabled={busy || !configResolverAvailable}>
              <legend>Orbital structures</legend>
              <div className="visualizer-control-grid">
                <RangeControl id="ring-thickness" label="Ring thickness" parameter="ringThickness" value={spaceParameters.ringThickness} help="Keeps orbital bands readable between 0.02 and 0.20." error={validationErrors.ringThickness} onChange={(value) => updateSpaceParameter('ringThickness', value)} />
                <RangeControl id="ring-occlusion" label="Ring occlusion" parameter="ringOcclusion" value={spaceParameters.ringOcclusion} help="Controls bounded foreground crossing from 0 to 1." error={validationErrors.ringOcclusion} onChange={(value) => updateSpaceParameter('ringOcclusion', value)} />
              </div>
            </fieldset>

            <fieldset className="visualizer-control-group" disabled={busy || !configResolverAvailable}>
              <legend>Environment</legend>
              <div className="visualizer-control-grid">
                <div className="field-stack">
                  <label htmlFor="space-palette"><span>Palette</span></label>
                  <select
                    id="space-palette"
                    value={spaceParameters.palette}
                    aria-describedby={`space-palette-help${validationErrors.palette ? ' space-palette-error' : ''}`}
                    aria-invalid={validationErrors.palette ? true : undefined}
                    onChange={(event) => updateSpaceParameter('palette', event.currentTarget.value as SpaceJourneyPalette)}
                  >
                    <option value="andromeda">Andromeda</option>
                    <option value="deep-space">Deep Space</option>
                    <option value="cyan-violet">Cyan Violet</option>
                    <option value="violet-magenta">Violet Magenta</option>
                    <option value="monochrome-blue">Monochrome Blue</option>
                    <option value="dark-amber">Dark Amber</option>
                  </select>
                  <small id="space-palette-help">Selects one deterministic, restrained color family.</small>
                  {validationErrors.palette ? <small className="field-error" id="space-palette-error">{validationErrors.palette}</small> : null}
                </div>
                <RangeControl id="glow-strength" label="Glow strength" parameter="glowStrength" value={spaceParameters.glowStrength} help="Adds controlled emission from 0 to 4 without unbounded highlights." error={validationErrors.glowStrength} onChange={(value) => updateSpaceParameter('glowStrength', value)} />
                <RangeControl id="shard-density" label="Shard density" parameter="shardDensity" value={spaceParameters.shardDensity} help="Adjusts deterministic instanced debris from sparse to dense." error={validationErrors.shardDensity} onChange={(value) => updateSpaceParameter('shardDensity', value)} />
                <RangeControl id="fog-depth" label="Fog depth" parameter="fogDepth" value={spaceParameters.fogDepth} help="Adds atmospheric depth from 0 to 1 while preserving the focal path." error={validationErrors.fogDepth} onChange={(value) => updateSpaceParameter('fogDepth', value)} />
              </div>
            </fieldset>

            <fieldset className="visualizer-control-group" disabled={busy || !configResolverAvailable}>
              <legend>Audio response</legend>
              <div className="visualizer-control-grid visualizer-control-grid--three">
                <RangeControl id="bass-response" label="Bass response" parameter="bassResponse" value={spaceParameters.bassResponse} help="Scales bounded core and inner-ring movement from 0 to 2." error={validationErrors.bassResponse} onChange={(value) => updateSpaceParameter('bassResponse', value)} />
                <RangeControl id="drum-response" label="Drum response" parameter="drumResponse" value={spaceParameters.drumResponse} help="Scales localized rhythmic accents from 0 to 2." error={validationErrors.drumResponse} onChange={(value) => updateSpaceParameter('drumResponse', value)} />
                <RangeControl id="vocal-response" label="Vocal response" parameter="vocalResponse" value={spaceParameters.vocalResponse} help="Scales atmospheric vocal-energy movement from 0 to 2." error={validationErrors.vocalResponse} onChange={(value) => updateSpaceParameter('vocalResponse', value)} />
              </div>
            </fieldset>
          </div>
        ) : (
          <p className="visualizer-panel__preset-note">Uses the existing deterministic Abstract Geometry behavior. No preset parameters are sent.</p>
        )}

        {!configResolverAvailable ? (
          <InlineNotice tone="warning">The running local backend does not advertise visualizer configuration schema 1.0.0. Rebuild it from current source before downloading a configuration.</InlineNotice>
        ) : null}

        <fieldset className="visualizer-control-group visualizer-panel__cue-settings" disabled={busy || !capabilities.visualCueExportAvailable}>
          <legend>Cue sheet</legend>
          <div className="visualizer-control-grid">
            <label className="field-stack">
              <span>Frames per second</span>
              <select
                aria-label="Frames per second"
                value={preferences.fps}
                onChange={(event) => {
                  const fps = Number(event.currentTarget.value) as VisualCuePreferences['fps']
                  updateCuePreferences((current) => ({ ...current, fps }))
                }}
              >
                {[24, 25, 30, 50, 60].map((fps) => <option key={fps} value={fps}>{fps} FPS</option>)}
              </select>
            </label>
            <label className="field-stack">
              <span>Curve detail</span>
              <select
                aria-label="Curve detail"
                value={preferences.curveDetail}
                onChange={(event) => {
                  const curveDetail = event.currentTarget.value as VisualCurveDetail
                  updateCuePreferences((current) => ({ ...current, curveDetail }))
                }}
              >
                <option value="compact">Compact</option>
                <option value="balanced">Balanced</option>
                <option value="detailed">Detailed</option>
              </select>
            </label>
          </div>
          <div className="visualizer-panel__toggles" aria-label="Cue sheet contents">
            <label><input type="checkbox" checked={preferences.includeBeats} onChange={() => toggle('includeBeats')} /> Beats</label>
            <label><input type="checkbox" checked={preferences.includeOnsets} onChange={() => toggle('includeOnsets')} /> Onsets</label>
            <label><input type="checkbox" checked={preferences.includeCurves} onChange={() => toggle('includeCurves')} /> Continuous curves</label>
          </div>
        </fieldset>

        <div className="visualizer-panel__actions">
          <Button
            icon={<Download aria-hidden="true" />}
            busy={resolvingConfig}
            disabled={busy || !configResolverAvailable || !configValid}
            onClick={() => void exportConfig()}
          >
            {resolvingConfig ? 'Validating configuration...' : 'Download visualizer config'}
          </Button>
          <Button
            icon={<Download aria-hidden="true" />}
            busy={exportingCues}
            disabled={busy || !capabilities.visualCueExportAvailable}
            onClick={() => void exportCues()}
          >
            {exportingCues ? 'Preparing cue sheet...' : 'Export cue sheet'}
          </Button>
        </div>

        <div className="visualizer-panel__status">
          {configSummary ? <InlineNotice tone="success">{configSummary}</InlineNotice> : null}
          {configWarnings.length > 0 ? <InlineNotice tone="warning"><span>Configuration warnings:</span><ul>{configWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></InlineNotice> : null}
          {configError ? <InlineNotice tone="error">{configError}</InlineNotice> : null}
          {cueSummary ? <InlineNotice tone="success">{cueSummary}</InlineNotice> : null}
          {cueError ? <InlineNotice tone="error">{cueError}</InlineNotice> : null}
        </div>
      </div>
    </section>
  )
}
