import { CheckCircle2, Cpu, Film, HardDrive, RefreshCw, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { errorFromUnknown } from '../api'
import { Button, ErrorCard, Metric, Notice, ProgressBar, StatusBadge } from '../components'
import {
  localVideoClient,
  type LocalVideoProject,
  type LocalVideoProjectSummary,
  type LocalVideoQualification,
  type LocalVideoReadiness,
  type LocalVideoWorkflow,
} from '../localVideoApi'
import type { StructuredError } from '../types'

function displayToken(value: string): string {
  return value.replaceAll('_', ' ').replaceAll('-', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function duration(value: number | null): string {
  if (value === null) return '—'
  const minutes = Math.floor(value / 60)
  return `${minutes}:${(value - minutes * 60).toFixed(3).padStart(6, '0')}`
}

function gibibytes(value: number | null): string {
  return value === null ? '—' : `${(value / 1024 ** 3).toFixed(1)} GiB`
}

function tone(value: string): 'success' | 'warning' | 'error' | 'info' | 'neutral' {
  if (['complete', 'passed', 'qualified', 'analysis_archived', 'provider_ready'].includes(value)) return 'success'
  if (['failed', 'cancelled'].includes(value)) return 'error'
  if (value.startsWith('blocked') || value === 'audio_required') return 'warning'
  if (['running', 'preparing'].includes(value) || value.startsWith('generating')) return 'info'
  return 'neutral'
}

export function LocalVideoProviderPanel() {
  const [projects, setProjects] = useState<LocalVideoProjectSummary[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [project, setProject] = useState<LocalVideoProject | null>(null)
  const [readiness, setReadiness] = useState<LocalVideoReadiness | null>(null)
  const [workflows, setWorkflows] = useState<LocalVideoWorkflow[]>([])
  const [qualification, setQualification] = useState<LocalVideoQualification | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [commandCopied, setCommandCopied] = useState(false)
  const [error, setError] = useState<StructuredError | null>(null)
  const productionCommand = "& 'D:\\TrackPrompt-ComfyUI\\.venv\\Scripts\\python.exe' '.\\tools\\render-local-anime-production.py' --start --run-id production-001"

  const refresh = useCallback(async () => {
    setError(null)
    try {
      const [nextProjects, nextReadiness, nextWorkflows] = await Promise.all([
        localVideoClient.projects(),
        localVideoClient.readiness(),
        localVideoClient.workflows(),
      ])
      setProjects(nextProjects)
      setReadiness(nextReadiness)
      setWorkflows(nextWorkflows)
      const nextId = selectedId || nextProjects[0]?.projectId || ''
      setSelectedId(nextId)
      if (nextId) setProject(await localVideoClient.get(nextId))
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Local video provider could not load'))
    }
  }, [selectedId])

  useEffect(() => { void refresh() }, [refresh])

  const choose = async (projectId: string): Promise<void> => {
    setSelectedId(projectId)
    setQualification(null)
    try {
      setProject(await localVideoClient.get(projectId))
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Local video project could not load'))
    }
  }

  const prepare = async (): Promise<void> => {
    if (!selectedId) return
    setBusy('prepare')
    setError(null)
    try {
      const next = await localVideoClient.prepare(selectedId)
      setProject(next)
      setReadiness(next.provider ?? await localVideoClient.readiness())
      setProjects(await localVideoClient.projects())
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Local project preparation failed'))
    } finally {
      setBusy(null)
    }
  }

  const qualify = async (): Promise<void> => {
    const workflow = workflows.find((item) => item.capability === 'wan22-i2v')
    if (!selectedId || !workflow) return
    setBusy('qualify')
    setError(null)
    try {
      const next = await localVideoClient.qualify(selectedId, workflow.workflowId)
      setQualification(next)
      setProject(await localVideoClient.get(selectedId))
      setProjects(await localVideoClient.projects())
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Bounded local qualification failed'))
    } finally {
      setBusy(null)
    }
  }

  const copyProductionCommand = async (): Promise<void> => {
    setError(null)
    try {
      await navigator.clipboard.writeText(productionCommand)
      setCommandCopied(true)
    } catch (caught) {
      setError(errorFromUnknown(caught, 'The production command could not be copied'))
    }
  }

  const selectedSummary = projects.find((item) => item.projectId === selectedId) ?? null
  const i2vWorkflow = workflows.find((item) => item.capability === 'wan22-i2v') ?? null
  const device = readiness?.devices[0] ?? null
  const progress = project && project.totalUnits > 0 ? project.completedUnits / project.totalUnits * 100 : 0
  const candidateRows = qualification?.candidates ?? project?.qualification?.candidates ?? []
  const timelineSummary = useMemo(() => project?.timeline.map((scene) => (
    `${scene.shotId} ${scene.startSeconds.toFixed(3)}–${scene.endSeconds.toFixed(3)}s · ${displayToken(scene.boundarySource)}`
  )) ?? [], [project])

  return (
    <section className="mc-card mc-local-video" aria-labelledby="local-video-title">
      <div className="mc-card__heading">
        <div><span className="mc-eyebrow">Fully local · ComfyUI · no inference spend</span><h2 id="local-video-title">Local anime video provider</h2></div>
        <StatusBadge tone={readiness?.productionReady ? 'success' : 'warning'}>{readiness?.productionReady ? 'LOCAL VIDEO PROVIDER READY' : displayToken(readiness?.providerState ?? 'checking')}</StatusBadge>
      </div>
      <p>Analyze and archive the real track, qualify one Wan2.2 tier, then resume the same 16-scene revision through references, keyframes, video, post, edit, and final QC.</p>
      {error ? <ErrorCard error={error} onDismiss={() => setError(null)} /> : null}
      <div className="mc-local-video__setup">
        <label className="mc-field">Local project package
          <select value={selectedId} onChange={(event) => { void choose(event.target.value) }}>
            {projects.map((item) => <option key={item.projectId} value={item.projectId}>{item.title} · {displayToken(item.status)}</option>)}
          </select>
        </label>
        <div className="mc-local-video__facts">
          <Metric label="Audio clock" value={duration(project?.audioDurationSeconds ?? selectedSummary?.durationSeconds ?? null)} detail={project?.audioHashPrefix ? `SHA-256 ${project.audioHashPrefix}…` : 'Bound by content after prepare'} />
          <Metric label="Archived analysis" value={project?.analysisArchived ? 'Persistent' : 'Not prepared'} detail={project?.revisionId ? `Revision ${project.revisionId.slice(0, 8)}` : 'Append-only project revision'} />
          <Metric label="GPU / VRAM" value={device?.name ?? 'Not reported'} detail={device ? `${gibibytes(device.vramTotalBytes)} total · ${gibibytes(device.vramFreeBytes)} free` : 'Local readiness check'} />
          <Metric label="Workflow" value={i2vWorkflow ? 'Semantic map ready' : 'API workflow required'} detail={i2vWorkflow ? i2vWorkflow.workflowSha256.slice(0, 12) : 'Register the current official API-format workflow'} />
          <Metric label="Qualified tier" value={readiness?.selectedTier ?? 'Not qualified'} detail={displayToken(readiness?.qualificationState ?? 'qualification_not_run')} />
          <Metric label="Post stack" value={readiness?.postProcessingReady ? 'RIFE + Real-ESRGAN' : 'Unavailable'} detail={readiness?.productionReady ? '1080p24 delivery qualified' : readiness?.statusMessage ?? 'Checking'} />
        </div>
      </div>
      <div className="mc-button-row">
        <Button tone="primary" icon={<HardDrive aria-hidden="true" />} busy={busy === 'prepare'} disabled={!selectedId} onClick={() => { void prepare() }}>Analyze and archive project</Button>
        <Button icon={<RefreshCw aria-hidden="true" />} busy={busy === 'refresh'} onClick={() => { void refresh() }}>Refresh local readiness</Button>
        <Button icon={<Cpu aria-hidden="true" />} busy={busy === 'qualify'} disabled={!project?.analysisArchived || !readiness?.reachable || !readiness.ggufNodeAvailable || !readiness.modelsAvailable || !i2vWorkflow} onClick={() => { void qualify() }}>Run bounded Q5 → Q4 → 5B qualification</Button>
        <Button icon={<Film aria-hidden="true" />} disabled={!project?.canStart} onClick={() => { void copyProductionCommand() }}>{commandCopied ? 'Production command copied' : 'Copy explicit production command'}</Button>
      </div>
      {project?.canStart ? <Notice tone="success" title="16-scene production is launch-ready"><p>The full render remains stopped until this explicit local command is run from the repository root.</p><code>{productionCommand}</code></Notice> : null}
      {readiness?.error ? <Notice tone="warning" title={readiness.error.summary}><p>{readiness.error.action}</p><code>{readiness.error.code}</code></Notice> : null}
      {readiness?.missingNodeRoles.length ? <Notice tone="warning" title="Missing ComfyUI capabilities"><p>{readiness.missingNodeRoles.join(', ')}</p></Notice> : null}
      {project?.error ? <Notice tone="warning" title={project.error.summary}><p>{project.error.action}</p><code>{project.error.code}</code></Notice> : null}
      {project ? <>
        <div className="mc-card__heading"><div><span className="mc-eyebrow">Current exact revision</span><h3>{displayToken(project.stage)}</h3></div><StatusBadge tone={tone(selectedSummary?.status ?? project.stage)}>{project.statusMessage}</StatusBadge></div>
        <ProgressBar value={progress} label="Local video project progress" />
        <p className="mc-muted">{project.completedUnits} / {project.totalUnits} units · current shot {project.currentShotId ?? '—'} · ETA {project.etaSeconds === null ? 'calibrating' : `${Math.round(project.etaSeconds)}s`} · final QC {project.finalQcPassed ? 'passed' : 'not passed'}</p>
      </> : null}
      {candidateRows.length ? <div className="mc-video-doctor" aria-label="Local hardware qualification">{candidateRows.map((candidate) => <div key={candidate.tier}><StatusBadge tone={tone(candidate.state)}>{candidate.state}</StatusBadge><span><strong>{candidate.tier}</strong><small>{candidate.reason ?? `${candidate.elapsedSeconds?.toFixed(1) ?? '—'}s · peak VRAM ${gibibytes(candidate.peakVramBytes)}`}</small></span></div>)}</div> : null}
      {timelineSummary.length ? <details><summary><ShieldCheck aria-hidden="true" /> Snapped 16-scene audio timeline</summary><ol>{timelineSummary.map((item) => <li key={item}><code>{item}</code></li>)}</ol></details> : null}
      {project?.finalQcPassed ? <Notice tone="success" title="Final QC complete"><p><CheckCircle2 aria-hidden="true" /> The final output is exact-duration 1080p24 and its manifests/interchange files are available.</p></Notice> : null}
    </section>
  )
}
