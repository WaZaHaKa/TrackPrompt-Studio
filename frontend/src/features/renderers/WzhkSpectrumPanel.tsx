import { type CSSProperties, useEffect, useState } from 'react'
import {
  CheckCircle2,
  CircleStop,
  Film,
  FolderCog,
  Gauge,
  Monitor,
  Palette,
  ShieldCheck,
  SlidersHorizontal,
} from 'lucide-react'

import {
  cancelWzhkSpectrumProduction,
  getRendererDescriptor,
  getWzhkSpectrumWorkspace,
  preflightWzhkSpectrumCapture,
  prepareWzhkSpectrumWorkspace,
  startWzhkSpectrumProduction,
} from '../../api'
import type {
  RendererAvailabilityState,
  RendererDescriptor,
  SpectrumBackgroundMode,
  SpectrumGenerativePreviewOverride,
  SpectrumGeometryCapability,
  SpectrumGeometryPreviewMode,
  SpectrumGeometryPreviewSection,
  SpectrumPreviewSection,
  SpectrumPreviewAudioMode,
  SpectrumProductionState,
  SpectrumVisualOverrides,
  SpectrumWorkspaceJob,
  WzhkGenerativeShapeId,
} from '../../types'
import { WZHK_GENERATIVE_SHAPE_IDS } from '../../types'
import { Button, InlineNotice } from '../../components/ui'

const STATUS_LABELS: Record<RendererAvailabilityState, string> = {
  READY: 'Ready',
  READY_FOR_PREVIEW: 'Ready for preview',
  READY_FOR_CAPTURE: 'Ready for capture',
  MISSING_RAINMETER: 'Rainmeter missing',
  MISSING_ASSETS: 'Assets missing',
  MISSING_FFMPEG: 'FFmpeg tools missing',
  MISSING_CAPTURE_PROVIDER: 'Capture provider missing',
  MISSING_MASTER: 'Master missing',
  INVALID_MASTER_DURATION: 'Master shorter than grid',
  INVALID_WORKSPACE: 'Workspace invalid',
  UNSUPPORTED_PLATFORM: 'Unsupported platform',
  INVALID_VENDOR_SNAPSHOT: 'Vendor snapshot invalid',
  INVALID_CONTRACT: 'Track contract invalid',
  INVALID_DESIGN_PRESET: 'Visual preset invalid',
  ASSET_DURATION_MISMATCH: 'Legacy duration state',
  WORKSPACE_UNAVAILABLE: 'Workspace unavailable',
}

const PRODUCTION_LABELS: Record<SpectrumProductionState, string> = {
  WORKSPACE_READY: 'Workspace ready',
  PREVIEW_READY: 'Preview ready',
  CAPTURE_PREFLIGHT: 'Capture preflight',
  CAPTURE_READY: 'Capture ready',
  CAPTURING: 'Capturing',
  CAPTURE_COMPLETE: 'Capture complete',
  MUXING: 'Muxing original master',
  VALIDATING: 'Validating final video',
  COMPLETE: 'Complete',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
}

const ACTIVE_PRODUCTION_STATES = new Set<SpectrumProductionState>([
  'CAPTURING',
  'CAPTURE_COMPLETE',
  'MUXING',
  'VALIDATING',
])

const DEFAULT_OVERRIDES: Required<SpectrumVisualOverrides> = {
  spectrumScale: 0.9,
  sensitivity: 34,
  logoScale: 0.92,
  accentColor: '#9D7CFF',
  backgroundIntensity: 0.45,
}

const SHAPE_LABELS: Record<WzhkGenerativeShapeId, string> = {
  'sparse-field': 'Sparse field',
  lissajous: 'Lissajous curve',
  'matrix-field': 'Matrix field',
  'wave-surface': 'Wave surface',
  torus: 'Torus',
  'twisted-torus': 'Twisted torus',
  'trefoil-knot': 'Trefoil knot',
  superformula: 'Superformula shell',
  'spherical-lattice': 'Spherical lattice',
  'dispersed-field': 'Dispersed field',
}

const GEOMETRY_CAPABILITY_LABELS: Record<SpectrumGeometryCapability['state'], string> = {
  READY: 'Ready',
  WEBGL2_UNAVAILABLE: 'WebGL2 unavailable',
  GPU_RENDERER_UNAVAILABLE: 'GPU renderer unavailable',
  SHADER_COMPILE_FAILED: 'Shader compile failed',
  PERFORMANCE_INSUFFICIENT: 'Performance insufficient',
  BROWSER_UNAVAILABLE: 'Supported browser unavailable',
}

type GeometryPreviewSelectionMode = 'full-choreography' | SpectrumGeometryPreviewMode

interface GeometryPreviewSelection {
  mode: GeometryPreviewSelectionMode
  shapeA: WzhkGenerativeShapeId
  shapeB: WzhkGenerativeShapeId
  section: SpectrumGeometryPreviewSection
  morphProgress: number
  pointCount: number
  seed: number
  rotationDegrees: number
  scale: number
  audioMode: SpectrumPreviewAudioMode
}

const DEFAULT_GEOMETRY_PREVIEW: GeometryPreviewSelection = {
  mode: 'full-choreography',
  shapeA: 'torus',
  shapeB: 'twisted-torus',
  section: 'intro',
  morphProgress: 0.5,
  pointCount: 1600,
  seed: 84291,
  rotationDegrees: 0,
  scale: 1,
  audioMode: 'disabled',
}

type PreparingView = SpectrumPreviewSection | 'timeline' | 'geometry' | 'production'
type ProductionAction = 'preflight' | 'start' | 'cancel'

function readableError(error: unknown): string {
  return error instanceof Error
    ? error.message
    : 'The Spectrum renderer status could not be loaded.'
}

function formatDuration(seconds: number, precise = false): string {
  const minutes = Math.floor(seconds / 60)
  const remaining = seconds - minutes * 60
  return precise
    ? `${String(minutes).padStart(2, '0')}:${remaining.toFixed(3).padStart(6, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(Math.round(remaining)).padStart(2, '0')}`
}

function RequirementList({ descriptor }: { descriptor: RendererDescriptor }) {
  return (
    <dl className="spectrum-requirements">
      {descriptor.requirements.map((requirement) => (
        <div key={requirement.id}>
          <dt>{requirement.label}</dt>
          <dd className={requirement.available ? 'is-ready' : 'is-blocked'}>
            {requirement.available ? 'Ready' : 'Needs attention'}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function PresetOverview({ descriptor }: { descriptor: RendererDescriptor }) {
  const preset = descriptor.designPreset
  if (!preset) return null
  return (
    <section className="spectrum-preset" aria-labelledby="spectrum-preset-heading">
      <div className="spectrum-preset__heading">
        <span><Palette aria-hidden="true" /> Visual preset</span>
        <strong id="spectrum-preset-heading">{preset.displayName}</strong>
        <small>Preview: external player · Production: owned TrackPrompt clock · progress {preset.progressVisible ? 'visible' : 'hidden'}</small>
        <small>Canonical background: {preset.backgroundMode === 'generative-geometry' ? 'Generative Geometry' : 'Milestone 3.5 static structured'}</small>
        {preset.generativeGeometry ? (
          <small>{preset.generativeGeometry.pointCount.toLocaleString()} production points · deterministic seed {preset.generativeGeometry.seed} · {preset.generativeGeometry.shapeFamilies.length} trusted shapes</small>
        ) : null}
      </div>
      <ol className="spectrum-timeline" aria-label="Canonical Scattered timeline">
        {preset.sections.map((section) => (
          <li key={section.id} style={{ '--section-color': section.spectrumColor } as CSSProperties}>
            <strong>{section.label}</strong>
            <span>{formatDuration(section.startSeconds)}–{section.endSeconds === null ? 'master EOF' : formatDuration(section.endSeconds)}</span>
          </li>
        ))}
      </ol>
      <p>The 96-bar grid ends at 03:12.000. POST_GRID_TAIL remains audio-reactive through the ffprobe-resolved master EOF; fixed preview overrides never enter production.</p>
    </section>
  )
}

function GeometryCapabilityNotice({ capability }: { capability?: SpectrumGeometryCapability | null }) {
  if (!capability) {
    return (
      <InlineNotice>
        <strong>Generative Geometry capability has not been reported yet.</strong>{' '}
        The static structured Milestone 3.5 fallback remains selectable. The capture FPS target is not evidence of renderer FPS.
      </InlineNotice>
    )
  }
  const measurement = capability.performanceMeasured && capability.rendererFps !== null
    ? `Measured renderer ${capability.rendererFps.toFixed(1)} FPS${capability.averageFrameTimeMs === null ? '' : ` · ${capability.averageFrameTimeMs.toFixed(2)} ms average frame time`}${capability.pointCount === null ? '' : ` · ${capability.pointCount.toLocaleString()} points`}.`
    : 'Renderer performance has not been measured.'
  if (capability.state === 'READY') {
    return (
      <InlineNotice tone="success">
        <strong>Generative Geometry: {GEOMETRY_CAPABILITY_LABELS[capability.state]}.</strong>{' '}
        {measurement} {capability.gpuRenderer ? `GPU: ${capability.gpuRenderer}. ` : ''}Renderer measurements remain separate from capture CFR.
      </InlineNotice>
    )
  }
  return (
    <InlineNotice tone="warning">
      <strong>Generative Geometry: {GEOMETRY_CAPABILITY_LABELS[capability.state]}.</strong>{' '}
      {capability.detail ? `${capability.detail} ` : ''}{measurement} The WZHK Spectrum renderer can still use the selectable Milestone 3.5 static fallback.
    </InlineNotice>
  )
}

interface DesignControlsProps {
  values: Required<SpectrumVisualOverrides>
  disabled: boolean
  onChange: (values: Required<SpectrumVisualOverrides>) => void
}

function DesignControls({ values, disabled, onChange }: DesignControlsProps) {
  const updateNumber = (
    key: 'spectrumScale' | 'sensitivity' | 'logoScale' | 'backgroundIntensity',
    value: string,
  ): void => onChange({ ...values, [key]: Number(value) })

  return (
    <fieldset className="spectrum-controls" disabled={disabled}>
      <legend><SlidersHorizontal aria-hidden="true" /> Safe preset controls</legend>
      <label>
        <span>Preview spectrum scale <output>{values.spectrumScale.toFixed(2)}×</output></span>
        <input aria-label="Spectrum scale" type="range" min="0.75" max="1.25" step="0.01" value={values.spectrumScale} onChange={(event) => updateNumber('spectrumScale', event.currentTarget.value)} />
      </label>
      <label>
        <span>Sensitivity <output>{values.sensitivity}</output></span>
        <input aria-label="Sensitivity" type="range" min="24" max="52" step="1" value={values.sensitivity} onChange={(event) => updateNumber('sensitivity', event.currentTarget.value)} />
      </label>
      <label>
        <span>Logo scale <output>{values.logoScale.toFixed(2)}×</output></span>
        <input aria-label="Logo scale" type="range" min="0.7" max="1.15" step="0.01" value={values.logoScale} onChange={(event) => updateNumber('logoScale', event.currentTarget.value)} />
      </label>
      <label>
        <span>Background <output>{Math.round(values.backgroundIntensity * 100)}%</output></span>
        <input aria-label="Background intensity" type="range" min="0.1" max="0.8" step="0.01" value={values.backgroundIntensity} onChange={(event) => updateNumber('backgroundIntensity', event.currentTarget.value)} />
      </label>
      <label className="spectrum-controls__color">
        <span>Accent <output>{values.accentColor.toUpperCase()}</output></span>
        <input aria-label="Accent color" type="color" value={values.accentColor} onChange={(event) => onChange({ ...values, accentColor: event.currentTarget.value.toUpperCase() })} />
      </label>
    </fieldset>
  )
}

interface BackgroundModeControlsProps {
  value: SpectrumBackgroundMode
  disabled: boolean
  onChange: (value: SpectrumBackgroundMode) => void
}

function BackgroundModeControls({ value, disabled, onChange }: BackgroundModeControlsProps) {
  return (
    <fieldset className="spectrum-controls" disabled={disabled}>
      <legend><Palette aria-hidden="true" /> Background engine</legend>
      <label>
        <span>Generative Geometry · Milestone 3.7 geometry-first</span>
        <input
          type="radio"
          name="spectrum-background-mode"
          value="generative-geometry"
          checked={value === 'generative-geometry'}
          onChange={() => onChange('generative-geometry')}
        />
      </label>
      <label>
        <span>Static structured · Milestone 3.5 fallback</span>
        <input
          type="radio"
          name="spectrum-background-mode"
          value="static-structured"
          checked={value === 'static-structured'}
          onChange={() => onChange('static-structured')}
        />
      </label>
      <small>Production contains geometry, logo and artist/title only. Spectrum bars and technical labels are preview diagnostics.</small>
    </fieldset>
  )
}

interface GeometryPreviewControlsProps {
  values: GeometryPreviewSelection
  disabled: boolean
  onChange: (values: GeometryPreviewSelection) => void
}

function GeometryPreviewControls({ values, disabled, onChange }: GeometryPreviewControlsProps) {
  const updateNumber = (
    key: 'morphProgress' | 'pointCount' | 'seed' | 'rotationDegrees' | 'scale',
    value: string,
  ): void => {
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) return
    const bounds: Record<typeof key, { minimum: number; maximum: number; integer?: boolean }> = {
      morphProgress: { minimum: 0, maximum: 1 },
      pointCount: { minimum: 16, maximum: 16_384, integer: true },
      seed: { minimum: 0, maximum: 2_147_483_647, integer: true },
      rotationDegrees: { minimum: -360, maximum: 360 },
      scale: { minimum: 0.1, maximum: 4 },
    }
    const rule = bounds[key]
    const bounded = Math.min(rule.maximum, Math.max(rule.minimum, parsed))
    onChange({ ...values, [key]: rule.integer ? Math.round(bounded) : bounded })
  }
  const showShapes = values.mode === 'shape' || values.mode === 'morph' || values.mode === 'lab'
  const showShapeB = values.mode === 'morph' || values.mode === 'lab'
  const showMorph = values.mode === 'morph' || values.mode === 'lab'

  return (
    <fieldset className="spectrum-controls" disabled={disabled}>
      <legend><Gauge aria-hidden="true" /> Generative Geometry developer preview</legend>
      <label>
        <span>Preview mode</span>
        <select
          value={values.mode}
          onChange={(event) => onChange({ ...values, mode: event.currentTarget.value as GeometryPreviewSelectionMode })}
        >
          <option value="full-choreography">Full choreography</option>
          <option value="shape">Fixed shape</option>
          <option value="morph">Morph A → B</option>
          <option value="section">Section choreography</option>
          <option value="lab">Shape laboratory</option>
        </select>
      </label>
      {showShapes ? (
        <label>
          <span>Shape A</span>
          <select value={values.shapeA} onChange={(event) => onChange({ ...values, shapeA: event.currentTarget.value as WzhkGenerativeShapeId })}>
            {WZHK_GENERATIVE_SHAPE_IDS.map((shapeId) => <option key={shapeId} value={shapeId}>{SHAPE_LABELS[shapeId]}</option>)}
          </select>
        </label>
      ) : null}
      {showShapeB ? (
        <label>
          <span>Shape B</span>
          <select value={values.shapeB} onChange={(event) => onChange({ ...values, shapeB: event.currentTarget.value as WzhkGenerativeShapeId })}>
            {WZHK_GENERATIVE_SHAPE_IDS.map((shapeId) => <option key={shapeId} value={shapeId}>{SHAPE_LABELS[shapeId]}</option>)}
          </select>
        </label>
      ) : null}
      {values.mode === 'section' ? (
        <label>
          <span>Choreography section</span>
          <select value={values.section} onChange={(event) => onChange({ ...values, section: event.currentTarget.value as SpectrumGeometryPreviewSection })}>
            <option value="intro">Intro</option>
            <option value="main">Main</option>
            <option value="outro">Outro</option>
            <option value="post-grid-tail">Post-grid tail</option>
          </select>
        </label>
      ) : null}
      {showMorph ? (
        <label>
          <span>Morph <output>{Math.round(values.morphProgress * 100)}%</output></span>
          <input aria-label="Morph progress" type="range" min="0" max="1" step="0.01" value={values.morphProgress} onChange={(event) => updateNumber('morphProgress', event.currentTarget.value)} />
        </label>
      ) : null}
      {values.mode !== 'full-choreography' ? (
        <>
          <label>
            <span>Point count</span>
            <input type="number" min="16" max="16384" step="16" value={values.pointCount} onChange={(event) => updateNumber('pointCount', event.currentTarget.value)} />
          </label>
          <label>
            <span>Deterministic seed</span>
            <input type="number" min="0" max="2147483647" step="1" value={values.seed} onChange={(event) => updateNumber('seed', event.currentTarget.value)} />
          </label>
          <label>
            <span>Audio response</span>
            <select value={values.audioMode} onChange={(event) => onChange({ ...values, audioMode: event.currentTarget.value as SpectrumPreviewAudioMode })}>
              <option value="disabled">Disabled</option>
              <option value="simulated">Simulated</option>
            </select>
          </label>
        </>
      ) : null}
      {values.mode === 'lab' ? (
        <>
          <label>
            <span>Rotation</span>
            <input type="number" min="-360" max="360" step="1" value={values.rotationDegrees} onChange={(event) => updateNumber('rotationDegrees', event.currentTarget.value)} />
          </label>
          <label>
            <span>Scale</span>
            <input type="number" min="0.1" max="4" step="0.05" value={values.scale} onChange={(event) => updateNumber('scale', event.currentTarget.value)} />
          </label>
        </>
      ) : null}
    </fieldset>
  )
}

function buildGenerativePreviewOverride(
  values: GeometryPreviewSelection,
): SpectrumGenerativePreviewOverride | undefined {
  if (values.mode === 'full-choreography') return undefined
  const common = {
    pointCount: values.pointCount,
    rotationDegrees: values.rotationDegrees,
    scale: values.scale,
    seed: values.seed,
    audioMode: values.audioMode,
  }
  const shapeA = { shapeId: values.shapeA, seed: values.seed }
  const shapeB = { shapeId: values.shapeB, seed: values.seed }
  if (values.mode === 'shape') return { ...common, mode: 'shape', shapeA }
  if (values.mode === 'morph') {
    return { ...common, mode: 'morph', shapeA, shapeB, morphProgress: values.morphProgress }
  }
  if (values.mode === 'section') return { ...common, mode: 'section', section: values.section }
  return { ...common, mode: 'lab', shapeA, shapeB, morphProgress: values.morphProgress }
}

function PreparedWorkspace({ job }: { job: SpectrumWorkspaceJob }) {
  const preparedView = job.mode === 'production'
    ? 'Production · canonical timeline'
    : job.generativePreviewOverride?.mode === 'shape'
      ? `Fixed ${SHAPE_LABELS[job.generativePreviewOverride.shapeA.shapeId]} geometry`
      : job.generativePreviewOverride?.mode === 'morph'
        ? `${SHAPE_LABELS[job.generativePreviewOverride.shapeA.shapeId]} → ${SHAPE_LABELS[job.generativePreviewOverride.shapeB.shapeId]} morph`
        : job.generativePreviewOverride?.mode === 'section'
          ? `${job.generativePreviewOverride.section} choreography preview`
          : job.generativePreviewOverride?.mode === 'lab'
            ? 'Shape laboratory preview'
            : job.previewSection
      ? `Fixed ${job.previewSection} preview`
      : 'Canonical full preview'
  const telemetry = job.geometryTelemetry
  return (
    <div className="spectrum-prepared" aria-label="Prepared Spectrum workspace">
      <span><CheckCircle2 aria-hidden="true" /> Workspace prepared</span>
      <dl>
        <div><dt>Job ID</dt><dd>{job.jobId}</dd></div>
        <div><dt>Preset</dt><dd>{job.presetName ?? 'Legacy workspace'}</dd></div>
        <div><dt>Prepared view</dt><dd>{preparedView}</dd></div>
        <div><dt>Background</dt><dd>{job.backgroundMode === 'generative-geometry' ? 'Generative Geometry' : 'Static structured · 3.5 fallback'}</dd></div>
        <div><dt>State</dt><dd>{job.state === 'PREPARED' ? 'Legacy prepared' : PRODUCTION_LABELS[job.state]}</dd></div>
        <div><dt>Contract valid</dt><dd>{job.contractValid ? 'Yes' : 'No'}</dd></div>
        <div><dt>Branding applied</dt><dd>{job.brandingApplied ? 'Yes' : 'No'}</dd></div>
        <div><dt>Vendor unchanged</dt><dd>{job.vendorUnchanged ? 'Yes' : 'No'}</dd></div>
        <div><dt>Visual QA</dt><dd>{job.visualQaRequired ? 'Operator required' : 'Complete'}</dd></div>
        {telemetry ? (
          <>
            <div><dt>Actual renderer FPS</dt><dd>{telemetry.actualFps === null ? 'Not measured' : telemetry.actualFps.toFixed(2)}</dd></div>
            <div><dt>Average renderer frame time</dt><dd>{telemetry.averageFrameTimeMs === null ? 'Not measured' : `${telemetry.averageFrameTimeMs.toFixed(2)} ms`}</dd></div>
            <div><dt>Geometry point count</dt><dd>{telemetry.pointCount === null ? 'Not reported' : telemetry.pointCount.toLocaleString()}</dd></div>
            <div><dt>Dropped renderer frames</dt><dd>{telemetry.droppedRendererFrames === null ? 'Not measured' : telemetry.droppedRendererFrames.toLocaleString()}</dd></div>
            <div><dt>GPU renderer</dt><dd>{telemetry.gpuRenderer ?? 'Not reported'}</dd></div>
          </>
        ) : null}
      </dl>
      {telemetry ? <small>Actual renderer telemetry is measured independently from the capture FPS/CFR target.</small> : null}
    </div>
  )
}

interface ProductionControlsProps {
  job: SpectrumWorkspaceJob
  action?: ProductionAction
  onPreflight: () => void
  onStart: () => void
  onCancel: () => void
}

function ProductionControls({ job, action, onPreflight, onStart, onCancel }: ProductionControlsProps) {
  const timing = job.masterTiming
  const preflight = job.capturePreflight
  const active = job.state !== 'PREPARED' && ACTIVE_PRODUCTION_STATES.has(job.state)
  const finalArtifact = job.artifacts.find((artifact) => artifact.artifactType === 'final-video')
  return (
    <section className="spectrum-production" aria-labelledby="spectrum-production-heading">
      <div>
        <span className="eyebrow"><Film aria-hidden="true" /> Milestone 3 production</span>
        <h3 id="spectrum-production-heading">Create final visualizer</h3>
        <p>Production always uses the canonical timeline and the complete approved master. Preview-only geometry overrides are excluded. Captured audio is never used in the deliverable.</p>
      </div>
      {timing ? (
        <dl className="spectrum-production__timing">
          <div><dt>Musical grid</dt><dd>{formatDuration(timing.gridDurationSeconds, true)}</dd></div>
          <div><dt>Approved master</dt><dd>{formatDuration(timing.masterDurationSeconds, true)}</dd></div>
          <div><dt>Post-grid tail</dt><dd>{formatDuration(timing.tailDurationSeconds, true)}</dd></div>
          <div><dt>Final fade starts</dt><dd>{formatDuration(timing.finalFadeStartSeconds, true)}</dd></div>
        </dl>
      ) : null}
      {preflight ? (
        <dl className="spectrum-production__dependencies">
          <div><dt>Rainmeter</dt><dd>{preflight.rainmeterPathResolved ? 'Ready' : 'Missing'}</dd></div>
          <div><dt>FFmpeg / ffprobe</dt><dd>{preflight.ffmpegPathResolved && preflight.ffprobePathResolved ? 'Ready' : 'Missing'}</dd></div>
          <div><dt>Playback clock</dt><dd>{preflight.playbackPathResolved ? 'Ready' : 'Missing'}</dd></div>
          <div><dt>Capture</dt><dd>{preflight.provider.available ? `${preflight.provider.displayName} · ${preflight.provider.encoder ?? 'encoder unavailable'}` : 'Missing'}</dd></div>
        </dl>
      ) : null}
      <div className="spectrum-production__state" role="status">
        <strong>{job.state === 'PREPARED' ? 'Legacy workspace' : PRODUCTION_LABELS[job.state]}</strong>
        {job.errorMessage ? <span>{job.errorMessage}</span> : null}
      </div>
      <div className="spectrum-production__actions">
        <Button busy={action === 'preflight'} disabled={Boolean(action) || active} onClick={onPreflight}>
          {action === 'preflight' ? 'Checking capture…' : 'Run Capture Preflight'}
        </Button>
        <Button
          icon={<Film aria-hidden="true" />}
          busy={action === 'start'}
          disabled={Boolean(action) || job.state !== 'CAPTURE_READY' || preflight?.ready !== true}
          onClick={onStart}
        >
          {action === 'start' ? 'Starting production…' : 'Create Final Visualizer'}
        </Button>
        {active ? (
          <Button icon={<CircleStop aria-hidden="true" />} busy={action === 'cancel'} disabled={Boolean(action)} onClick={onCancel}>
            {action === 'cancel' ? 'Requesting cancellation…' : 'Cancel Production'}
          </Button>
        ) : null}
      </div>
      {preflight ? <p className="spectrum-production__notice">{preflight.operatorNotice}</p> : null}
      {finalArtifact ? (
        <InlineNotice tone="success">Final video validated: {finalArtifact.relativePath} · {(finalArtifact.sizeBytes / 1024 / 1024).toFixed(1)} MiB</InlineNotice>
      ) : null}
    </section>
  )
}

export function WzhkSpectrumPanel() {
  const [descriptor, setDescriptor] = useState<RendererDescriptor>()
  const [loading, setLoading] = useState(true)
  const [preparing, setPreparing] = useState<PreparingView>()
  const [productionAction, setProductionAction] = useState<ProductionAction>()
  const [monitoring, setMonitoring] = useState(false)
  const [overrides, setOverrides] = useState<Required<SpectrumVisualOverrides>>(DEFAULT_OVERRIDES)
  const [backgroundMode, setBackgroundMode] = useState<SpectrumBackgroundMode>('generative-geometry')
  const [geometryPreview, setGeometryPreview] = useState<GeometryPreviewSelection>(DEFAULT_GEOMETRY_PREVIEW)
  const [error, setError] = useState<string>()
  const [job, setJob] = useState<SpectrumWorkspaceJob>()

  useEffect(() => {
    let active = true
    void getRendererDescriptor('wzhk-spectrum')
      .then((value) => {
        if (!active) return
        setDescriptor(value)
        setError(undefined)
      })
      .catch((caught: unknown) => {
        if (active) setError(readableError(caught))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [])

  const monitoredJobId = monitoring && job?.mode === 'production' ? job.jobId : null
  useEffect(() => {
    if (!monitoredJobId) return undefined
    let active = true
    const refresh = (): void => {
      void getWzhkSpectrumWorkspace(monitoredJobId)
        .then((value) => {
          if (!active) return
          setJob(value)
          if (value.state === 'COMPLETE' || value.state === 'FAILED' || value.state === 'CANCELLED') {
            setMonitoring(false)
          }
        })
        .catch((caught: unknown) => {
          if (active) setError(readableError(caught))
        })
    }
    refresh()
    const timer = window.setInterval(refresh, 1000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [monitoredJobId])

  const prepare = async (
    mode: 'preview' | 'production',
    previewSection: SpectrumPreviewSection | null,
    generativePreview?: SpectrumGenerativePreviewOverride,
    preparingView?: PreparingView,
  ): Promise<void> => {
    if (!descriptor?.preparationAvailable) return
    setPreparing(preparingView ?? (mode === 'production' ? 'production' : previewSection ?? 'timeline'))
    setError(undefined)
    setMonitoring(false)
    try {
      if (mode === 'production') {
        setJob(await prepareWzhkSpectrumWorkspace({
          mode: 'production',
          backgroundMode,
          previewSection: null,
          visualOverrides: overrides,
        }))
      } else {
        setJob(await prepareWzhkSpectrumWorkspace({
          mode: 'preview',
          backgroundMode,
          previewSection,
          visualOverrides: overrides,
          ...(backgroundMode === 'generative-geometry' && generativePreview
            ? { generativePreview }
            : {}),
        }))
      }
    } catch (caught) {
      setError(readableError(caught))
    } finally {
      setPreparing(undefined)
    }
  }

  const runCapturePreflight = async (): Promise<void> => {
    if (!job || job.mode !== 'production') return
    setProductionAction('preflight')
    setError(undefined)
    try {
      setJob(await preflightWzhkSpectrumCapture(job.jobId))
    } catch (caught) {
      setError(readableError(caught))
    } finally {
      setProductionAction(undefined)
    }
  }

  const startProduction = async (): Promise<void> => {
    if (!job?.capturePreflight?.ready) return
    const confirmed = window.confirm(`${job.capturePreflight.operatorNotice}\n\nThe full master will play for about ${formatDuration(job.capturePreflight.timing?.masterDurationSeconds ?? 0, true)}. Continue?`)
    if (!confirmed) return
    setProductionAction('start')
    setError(undefined)
    try {
      setJob(await startWzhkSpectrumProduction(job.jobId))
      setMonitoring(true)
    } catch (caught) {
      setError(readableError(caught))
    } finally {
      setProductionAction(undefined)
    }
  }

  const cancelProduction = async (): Promise<void> => {
    if (!job) return
    setProductionAction('cancel')
    setError(undefined)
    try {
      setJob(await cancelWzhkSpectrumProduction(job.jobId))
      setMonitoring(true)
    } catch (caught) {
      setError(readableError(caught))
    } finally {
      setProductionAction(undefined)
    }
  }

  if (loading) return <p className="spectrum-loading" role="status">Checking WZHK Spectrum locally…</p>
  if (!descriptor) return <InlineNotice tone="error">{error ?? 'WZHK Spectrum is unavailable.'}</InlineNotice>

  const summary = descriptor.contractSummary
  const isPreparing = preparing !== undefined
  return (
    <section className="spectrum-panel" aria-labelledby="wzhk-spectrum-heading">
      <div className="spectrum-panel__intro">
        <span className="eyebrow"><Gauge aria-hidden="true" /> Optional local renderer</span>
        <h2 id="wzhk-spectrum-heading">WZHK Spectrum</h2>
        <p>{descriptor.description}</p>
        <span className={`spectrum-status ${descriptor.captureAvailable ? 'is-ready' : 'is-blocked'}`}>
          {STATUS_LABELS[descriptor.availability]}
        </span>
      </div>

      {summary ? (
        <div className="spectrum-track-card">
          <div>
            <strong>{summary.artist} — {summary.title}</strong>
            <span>{summary.bpm} BPM · {summary.totalBars} bars · grid {formatDuration(summary.gridDurationSeconds, true)}</span>
            {summary.masterDurationSeconds !== null && summary.tailDurationSeconds !== null ? (
              <span>Master {formatDuration(summary.masterDurationSeconds, true)} · intentional tail {formatDuration(summary.tailDurationSeconds, true)}</span>
            ) : null}
          </div>
          <div>
            <span><Monitor aria-hidden="true" /> {summary.width}×{summary.height}</span>
            <span>{summary.fps} FPS capture target</span>
          </div>
        </div>
      ) : null}

      <PresetOverview descriptor={descriptor} />
      <GeometryCapabilityNotice capability={descriptor.geometryCapability} />
      <RequirementList descriptor={descriptor} />

      {descriptor.warnings.length > 0 ? (
        <InlineNotice tone="warning"><span>Spectrum readiness notes:</span><ul>{descriptor.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></InlineNotice>
      ) : (
        <InlineNotice tone="success">Rainmeter, playback, FFmpeg validation, capture, and private workspace requirements are ready.</InlineNotice>
      )}

      <DesignControls values={overrides} disabled={isPreparing || monitoring} onChange={setOverrides} />
      <BackgroundModeControls value={backgroundMode} disabled={isPreparing || monitoring} onChange={setBackgroundMode} />
      <GeometryPreviewControls
        values={geometryPreview}
        disabled={isPreparing || monitoring || backgroundMode !== 'generative-geometry'}
        onChange={setGeometryPreview}
      />

      <div className="spectrum-panel__actions" aria-label="Spectrum workspace actions">
        <Button icon={<FolderCog aria-hidden="true" />} busy={preparing === 'timeline'} disabled={isPreparing || monitoring || !descriptor.preparationAvailable} onClick={() => void prepare('preview', null)}>
          {preparing === 'timeline' ? 'Preparing full preview…' : 'Prepare Full Preview'}
        </Button>
        {(['intro', 'main', 'outro'] as const).map((section) => (
          <Button key={section} busy={preparing === section} disabled={isPreparing || monitoring || !descriptor.preparationAvailable} onClick={() => void prepare('preview', section)}>
            {preparing === section ? `Preparing ${section}…` : `Preview ${section.charAt(0).toUpperCase()}${section.slice(1)}`}
          </Button>
        ))}
        <Button
          busy={preparing === 'geometry'}
          disabled={isPreparing || monitoring || !descriptor.preparationAvailable || backgroundMode !== 'generative-geometry'}
          onClick={() => {
            const generativePreview = buildGenerativePreviewOverride(geometryPreview)
            const previewSection = generativePreview?.mode === 'section' && generativePreview.section !== 'post-grid-tail'
              ? generativePreview.section
              : null
            void prepare('preview', previewSection, generativePreview, 'geometry')
          }}
        >
          {preparing === 'geometry' ? 'Preparing geometry preview…' : 'Prepare Geometry Preview'}
        </Button>
        <Button icon={<Film aria-hidden="true" />} busy={preparing === 'production'} disabled={isPreparing || monitoring || !descriptor.preparationAvailable} onClick={() => void prepare('production', null)}>
          {preparing === 'production' ? 'Preparing production…' : 'Prepare Production Workspace'}
        </Button>
        <span><ShieldCheck aria-hidden="true" /> Job-specific Rainmeter staging; vendor and unrelated OBS/Rainmeter state stay untouched.</span>
      </div>

      {!descriptor.preparationAvailable ? <InlineNotice tone="error">Resolve the required Spectrum preparation gates before creating a workspace.</InlineNotice> : null}
      {error ? <InlineNotice tone="error">{error}</InlineNotice> : null}
      {job ? <PreparedWorkspace job={job} /> : null}
      {job?.mode === 'production' ? (
        <ProductionControls
          job={job}
          action={productionAction}
          onPreflight={() => void runCapturePreflight()}
          onStart={() => void startProduction()}
          onCancel={() => void cancelProduction()}
        />
      ) : null}
      <p className="spectrum-manual-note">Preview preparation never records or changes canonical choreography. Production never receives developer preview overrides and requires a separate capture preflight plus explicit desktop/audio confirmation. No YouTube upload is implemented.</p>
    </section>
  )
}
