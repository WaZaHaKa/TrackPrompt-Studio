import {
  Check,
  Download,
  ExternalLink,
  Film,
  FolderOpen,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { errorFromUnknown } from '../api'
import {
  AdvancedDetails,
  Button,
  ErrorCard,
  Metric,
  Notice,
  ProgressBar,
  SectionHeading,
  StatusBadge,
} from '../components'
import type { StructuredError } from '../types'
import {
  type VideoCatalog,
  type VideoDoctorResult,
  type VideoJob,
  type VideoProfile,
  type VideoRequestPreview,
  videoGenerationClient,
} from '../videoGenerationApi'

const LIVE_STATES = new Set(['smoke_submitted', 'generating', 'assembling'])

function money(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value)
}

function label(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function randomSeed(): number {
  return crypto.getRandomValues(new Uint32Array(1))[0] ?? 0
}

function badgeTone(value: string): 'success' | 'warning' | 'error' | 'neutral' | 'info' {
  if (['complete', 'verified', 'accepted', 'timeline_ready', 'exported', 'review_ready'].includes(value)) return 'success'
  if (['failed', 'filtered', 'rejected', 'blocked_budget', 'blocked_provider_access', 'blocked_provider_quota'].includes(value)) return 'error'
  if (['reserved', 'submitted', 'running', 'downloaded', 'smoke_submitted', 'generating', 'assembling'].includes(value)) return 'info'
  if (value === 'partial') return 'warning'
  return 'neutral'
}

export function VideoGenerationScreen() {
  const [catalog, setCatalog] = useState<VideoCatalog | null>(null)
  const [jobs, setJobs] = useState<VideoJob[]>([])
  const [job, setJob] = useState<VideoJob | null>(null)
  const [requests, setRequests] = useState<VideoRequestPreview | null>(null)
  const [doctor, setDoctor] = useState<VideoDoctorResult | null>(null)
  const [analysisId, setAnalysisId] = useState('')
  const [projectId, setProjectId] = useState('')
  const [profileId, setProfileId] = useState<VideoProfile['id']>('fast-1080p')
  const [gcpProjectId, setGcpProjectId] = useState(() => localStorage.getItem('wzhk.video.gcp-project') ?? '')
  const [gcsBucket, setGcsBucket] = useState(() => localStorage.getItem('wzhk.video.gcs-bucket') ?? '')
  const [masterSeed, setMasterSeed] = useState<number | null>(null)
  const [seedLocked, setSeedLocked] = useState(true)
  const [referenceImagePath, setReferenceImagePath] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<StructuredError | null>(null)
  const lastSequence = useRef(0)

  const refresh = useCallback(async () => {
    try {
      const [nextCatalog, nextJobs] = await Promise.all([
        videoGenerationClient.catalog(),
        videoGenerationClient.jobs(),
      ])
      setCatalog(nextCatalog)
      setJobs(nextJobs)
      setAnalysisId((current) => current || nextCatalog.analyses[0]?.analysisJobId || '')
      const defaultProjectId = nextCatalog.packages[0]?.projectId || ''
      setProjectId((current) => current || defaultProjectId)
      setMasterSeed((current) => current ?? nextCatalog.packages[0]?.defaultMasterSeed ?? 0)
      setJob((current) => current
        ? nextJobs.find((candidate) => candidate.jobId === current.jobId) ?? current
        : nextJobs.find((candidate) => candidate.projectId === defaultProjectId) ?? nextJobs[0] ?? null)
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Video workspace could not load'))
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const refreshJob = useCallback(async (jobId: string) => {
    try {
      const next = await videoGenerationClient.get(jobId)
      setJob(next)
      setJobs((current) => [next, ...current.filter((candidate) => candidate.jobId !== next.jobId)])
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Video job could not refresh'))
    }
  }, [])

  const activeJobId = job?.jobId
  const activeJobState = job?.state

  useEffect(() => {
    if (!activeJobId || !activeJobState) return
    let closed = false
    let source: EventSource | null = null
    let reconnectTimer: number | undefined
    let retries = 0
    const connect = (): void => {
      if (closed) return
      const query = new URLSearchParams({ jobId: activeJobId, afterSequence: String(lastSequence.current) })
      source = new EventSource(`/api/mission-control/events?${query.toString()}`, { withCredentials: true })
      source.onopen = () => { retries = 0 }
      source.addEventListener('video_generation', (event) => {
        if (!(event instanceof MessageEvent)) return
        try {
          const payload: unknown = JSON.parse(String(event.data))
          if (typeof payload === 'object' && payload !== null && 'sequence' in payload && typeof payload.sequence === 'number') {
            lastSequence.current = Math.max(lastSequence.current, payload.sequence)
          }
          void refreshJob(activeJobId)
        } catch {
          void refreshJob(activeJobId)
        }
      })
      source.onerror = () => {
        source?.close()
        source = null
        if (closed || reconnectTimer !== undefined) return
        retries += 1
        reconnectTimer = window.setTimeout(() => {
          reconnectTimer = undefined
          connect()
        }, Math.min(10_000, 750 * (2 ** Math.min(retries, 4))))
      }
    }
    connect()
    const polling = LIVE_STATES.has(activeJobState)
      ? window.setInterval(() => { void refreshJob(activeJobId) }, 5_000)
      : undefined
    return () => {
      closed = true
      source?.close()
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      if (polling !== undefined) window.clearInterval(polling)
    }
  }, [activeJobId, activeJobState, refreshJob])

  useEffect(() => {
    if (!activeJobId) {
      setRequests(null)
      return
    }
    void videoGenerationClient.requests(activeJobId).then(setRequests).catch(() => setRequests(null))
  }, [activeJobId])

  const selectedPackage = catalog?.packages.find((item) => item.projectId === projectId) ?? catalog?.packages[0]
  const selectedProfile = selectedPackage?.profiles.find((item) => item.id === profileId)
  const canCompile = Boolean(
    analysisId && projectId && gcpProjectId.trim() && gcsBucket.trim() && selectedProfile?.available,
  )
  const exactConfirmation = job?.authorizationPhrase ?? ''

  const chooseProject = (nextProjectId: string): void => {
    setProjectId(nextProjectId)
    const nextPackage = catalog?.packages.find((item) => item.projectId === nextProjectId)
    setMasterSeed(nextPackage?.defaultMasterSeed ?? 0)
    const matchingJob = jobs.find((candidate) => candidate.projectId === nextProjectId)
    if (matchingJob) setJob(matchingJob)
  }

  const run = async (name: string, action: () => Promise<VideoJob>, success?: (value: VideoJob) => void) => {
    setBusy(name)
    setError(null)
    try {
      const value = await action()
      setJob(value)
      setJobs((current) => [value, ...current.filter((candidate) => candidate.jobId !== value.jobId)])
      success?.(value)
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Video action could not complete'))
    } finally {
      setBusy(null)
    }
  }

  const compile = (): void => {
    localStorage.setItem('wzhk.video.gcp-project', gcpProjectId.trim())
    localStorage.setItem('wzhk.video.gcs-bucket', gcsBucket.trim())
    const effectiveSeed = seedLocked
      ? masterSeed ?? selectedPackage?.defaultMasterSeed ?? 0
      : randomSeed()
    if (!seedLocked) setMasterSeed(effectiveSeed)
    void run('compile', () => videoGenerationClient.createPlan({
      analysisJobId: analysisId,
      projectId,
      profileId,
      gcpProjectId: gcpProjectId.trim(),
      gcsBucket: gcsBucket.trim(),
      audioPath: null,
      masterSeed: effectiveSeed,
      seedLocked,
      referenceImagePath: referenceImagePath.trim() || null,
    }), () => setConfirmation(''))
  }

  const checkProvider = async (): Promise<void> => {
    setBusy('doctor')
    setError(null)
    try {
      setDoctor(await videoGenerationClient.doctor(gcpProjectId.trim(), gcsBucket.trim()))
    } catch (caught) {
      setError(errorFromUnknown(caught, 'GCP readiness check failed'))
    } finally {
      setBusy(null)
    }
  }

  const pickAudio = async (): Promise<void> => {
    if (!job) return
    setBusy('audio-selecting')
    setError(null)
    try {
      const selected = await videoGenerationClient.selectAudio(job.jobId)
      if (selected.selected) await refreshJob(job.jobId)
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Audio selection failed'))
    } finally {
      setBusy(null)
    }
  }

  const bindRetainedAudio = async (): Promise<void> => {
    if (!job) return
    setBusy('audio-retained')
    setError(null)
    try {
      const selected = await videoGenerationClient.useRetainedAudio(job.jobId)
      if (selected.selected) await refreshJob(job.jobId)
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Retained analysis audio could not be bound'))
    } finally {
      setBusy(null)
    }
  }

  const clearAudio = async (): Promise<void> => {
    if (!job) return
    setBusy('audio-clearing')
    setError(null)
    try {
      await videoGenerationClient.clearAudio(job.jobId)
      await refreshJob(job.jobId)
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Audio binding could not be cleared'))
    } finally {
      setBusy(null)
    }
  }

  const pickReferenceImage = async (): Promise<void> => {
    setBusy('reference')
    try {
      const selected = await videoGenerationClient.selectReferenceImage()
      if (selected) setReferenceImagePath(selected)
    } catch (caught) {
      setError(errorFromUnknown(caught, 'Continuity reference selection failed'))
    } finally {
      setBusy(null)
    }
  }

  const generateMasterSeed = (): void => {
    setMasterSeed(randomSeed())
  }

  const artifactLinks = useMemo(() => job ? [
    ['FCPXML', job.artifacts.fcpxmlUrl],
    ['FCP 7 XML', job.artifacts.fcp7XmlUrl],
    ['EDL', job.artifacts.edlUrl],
    ['Edit sheet CSV', job.artifacts.editSheetUrl],
    ['Markers CSV', job.artifacts.markersUrl],
    ['Relink map', job.artifacts.relinkMapUrl],
    ['Coverage report', job.artifacts.coverageReportUrl],
    ['Render manifest', job.artifacts.renderManifestUrl],
    ['Verification report', job.artifacts.verificationReportUrl],
  ].filter((item): item is [string, string] => item[1] !== null) : [], [job])

  return (
    <div className="mc-page mc-video-page">
      <SectionHeading
        eyebrow="GCP Veo 3.1 · exact-batch workflow"
        title="Generate the complete music video"
        description="Compile a deterministic plan from TrackPrompt analysis, approve one hard cost ceiling, then let Mission Control smoke-test, resume, verify, assemble, and export the same batch."
        actions={<Button icon={<RefreshCw aria-hidden="true" />} onClick={() => { void refresh() }}>Refresh</Button>}
      />

      <Notice tone="info" title="No paid request occurs during planning or readiness checks">
        <p>The first billable request is the smoke shot, and Start remains locked until the exact plan, pricing snapshot, maximum spend, and confirmation phrase are reviewed.</p>
      </Notice>
      {error ? <ErrorCard error={error} onDismiss={() => setError(null)} /> : null}

      <section className="mc-card mc-video-setup" aria-labelledby="video-setup-title">
        <div className="mc-card__heading"><div><span className="mc-eyebrow">Step 1 · source and delivery</span><h2 id="video-setup-title">Compile the exact plan</h2></div><Sparkles aria-hidden="true" /></div>
        <div className="mc-video-form-grid">
          <label className="mc-field">Track analysis
            <select value={analysisId} onChange={(event) => setAnalysisId(event.target.value)}>
              {catalog?.analyses.map((analysis) => <option key={analysis.analysisJobId} value={analysis.analysisJobId}>{analysis.displayName}{analysis.retainedAudioAvailable ? ' · audio retained' : ''}</option>)}
            </select>
          </label>
          <label className="mc-field">Content package
            <select value={projectId} onChange={(event) => chooseProject(event.target.value)}>
              {catalog?.packages.map((pack) => <option key={pack.projectId} value={pack.projectId}>{pack.title} · {pack.shotCount} shots</option>)}
            </select>
          </label>
          <label className="mc-field">Delivery profile
            <select value={profileId} onChange={(event) => setProfileId(event.target.value as VideoProfile['id'])}>
              {selectedPackage?.profiles.map((profile) => <option key={profile.id} value={profile.id} disabled={!profile.available}>{profile.displayName}{profile.default ? ' · default' : ' · optional'}{profile.available ? '' : ' · unavailable'}</option>)}
            </select>
            {selectedProfile?.availabilityNote ? <small>{selectedProfile.availabilityNote}</small> : null}
          </label>
          <label className="mc-field">GCP project ID<input value={gcpProjectId} onChange={(event) => setGcpProjectId(event.target.value)} autoComplete="off" /></label>
          <label className="mc-field">GCS bucket<input value={gcsBucket} onChange={(event) => setGcsBucket(event.target.value)} placeholder="my-private-video-bucket" autoComplete="off" /></label>
          <label className="mc-field mc-video-seed">Master continuity seed
            <span><input type="number" min="0" max="4294967295" value={masterSeed ?? ''} onChange={(event) => setMasterSeed(Number(event.target.value))} /><Button type="button" disabled={seedLocked} onClick={generateMasterSeed}>Generate new seed</Button></span>
            <span><input type="checkbox" checked={seedLocked} onChange={(event) => setSeedLocked(event.target.checked)} /> Lock seed for this plan</span>
          </label>
          <label className="mc-field mc-video-audio">Private character / first-frame reference
            <span><input value={referenceImagePath} onChange={(event) => setReferenceImagePath(event.target.value)} placeholder="Optional JPEG or PNG" /><Button type="button" busy={busy === 'reference'} icon={<FolderOpen aria-hidden="true" />} onClick={() => { void pickReferenceImage() }}>Attach reference</Button></span>
            <small>Hash-bound into the plan and uploaded only after exact-batch authorization. Veo receives it as a supported first-frame image, not as an unsupported referenceImages field.</small>
          </label>
        </div>
        {selectedProfile ? <div className="mc-video-metrics"><Metric label="Base estimate" value={money(selectedProfile.baseEstimatedUsd)} /><Metric label="Conservative estimate" value={money(selectedProfile.conservativeEstimatedUsd)} /><Metric label="Hard ceiling" value={money(selectedProfile.maxSpendUsd)} /><Metric label="Pricing snapshot" value={catalog?.pricingSnapshotDate ?? '—'} /></div> : null}
        <div className="mc-button-row">
          <Button tone="primary" busy={busy === 'compile'} disabled={!canCompile} icon={<Film aria-hidden="true" />} onClick={compile}>Compile exact video plan</Button>
          <Button busy={busy === 'doctor'} disabled={!gcpProjectId.trim() || !gcsBucket.trim()} icon={<ShieldCheck aria-hidden="true" />} onClick={() => { void checkProvider() }}>Run free GCP readiness check</Button>
        </div>
        {doctor ? <div className="mc-video-doctor" aria-label="GCP readiness results">{doctor.checks.map((check) => <div key={check.id}><StatusBadge tone={badgeTone(check.status)}>{check.status}</StatusBadge><span><strong>{label(check.id)}</strong><small>{check.detail}</small></span></div>)}</div> : null}
      </section>

      {jobs.length > 1 ? <label className="mc-field mc-video-job-picker">Saved video job
        <select value={job?.jobId ?? ''} onChange={(event) => { const selected = jobs.find((item) => item.jobId === event.target.value); if (selected) setJob(selected) }}>
          {jobs.map((item) => <option key={item.jobId} value={item.jobId}>{item.title} · {label(item.state)} · {item.jobId.slice(0, 8)}</option>)}
        </select>
      </label> : null}

      {job ? <>
        <section className="mc-card mc-video-plan" aria-labelledby="video-plan-title">
          <div className="mc-card__heading"><div><span className="mc-eyebrow">Step 2 · immutable batch</span><h2 id="video-plan-title">Review prompts and maximum spend</h2></div><StatusBadge tone={badgeTone(job.state)}>{label(job.state)}</StatusBadge></div>
          <div className="mc-video-metrics"><Metric label="Shots" value={job.totalShotCount} detail={`${job.verifiedShotCount} verified`} /><Metric label="Base estimate" value={money(job.cost.baseEstimatedUsd)} /><Metric label="Conservative" value={money(job.cost.conservativeEstimatedUsd)} /><Metric label="Maximum spend" value={money(job.cost.maxSpendUsd)} /></div>
          <ProgressBar value={job.progressPercent} label="Video generation progress" />
          <p className="mc-muted">Pricing snapshot {job.cost.pricingSnapshotDate} · {money(job.cost.rateUsdPerOutputSecond)} per output second · reserved {money(job.reservedCostUsd)} · remaining {money(job.remainingAuthorizedUsd)}</p>
          <Notice tone="warning" title="Continuity profile is deterministic; model identity remains best effort"><p>{job.consistencyNotice}</p></Notice>
          <AdvancedDetails summary="Continuity profile, master seed, and groups">
            <p><strong>Master seed</strong> <code>{String(job.continuity.masterSeed ?? 'unknown')}</code> · {job.continuity.seedLocked ? 'locked' : 'unlocked at compile'} · {String(job.continuity.seedDerivation ?? 'unknown')}</p>
            <pre>{JSON.stringify({ characterProfiles: job.continuity.characterProfiles, visualAnchors: job.continuity.visualAnchors, groups: job.continuity.groups }, null, 2)}</pre>
          </AdvancedDetails>
          <AdvancedDetails summary="Exact provider requests and plan identity">
            <p><strong>Plan digest</strong><br /><code>{job.planDigest}</code></p>
            <pre>{JSON.stringify(requests?.requests ?? [], null, 2)}</pre>
          </AdvancedDetails>
          {job.state === 'planned' ? <div className="mc-video-authorization">
            <label className="mc-field">One-time confirmation phrase
              <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" spellCheck={false} />
            </label>
            <code>{exactConfirmation}</code>
            <Button tone="primary" busy={busy === 'authorize'} disabled={confirmation !== exactConfirmation} icon={<ShieldCheck aria-hidden="true" />} onClick={() => { void run('authorize', () => videoGenerationClient.authorize(job.jobId, confirmation), () => setConfirmation('')) }}>Authorize this complete exact batch once</Button>
          </div> : null}
          {job.state === 'authorized' ? <Notice tone="success" title="Exact batch authorized"><p>Start submits the smoke shot first. Mission Control automatically continues the unchanged remaining batch only after that clip passes technical verification.</p><Button tone="primary" busy={busy === 'start'} icon={<Play aria-hidden="true" />} onClick={() => { void run('start', () => videoGenerationClient.action(job.jobId, 'start')) }}>Start smoke shot and complete batch</Button></Notice> : null}
          {LIVE_STATES.has(job.state) ? <div className="mc-button-row"><Button tone="danger" busy={busy === 'cancel'} onClick={() => { void run('cancel', () => videoGenerationClient.action(job.jobId, 'cancel')) }}>Cancel batch safely</Button></div> : null}
          {job.error ? <Notice tone="error" title={label(job.error.code)}><p>{job.error.summary}</p>{job.error.httpStatus ? <p>HTTP {job.error.httpStatus}{job.error.providerStatus ? ` · ${job.error.providerStatus}` : ''}{job.error.diagnosticId ? ` · diagnostic ${job.error.diagnosticId}` : ''}</p> : null}{job.error.retryable || job.state.startsWith('blocked_') ? <Button busy={busy === 'resume'} onClick={() => { void run('resume', () => videoGenerationClient.action(job.jobId, 'resume')) }}>Resume exact plan</Button> : null}</Notice> : null}
        </section>

        <section className="mc-video-shot-grid" aria-label="Planned video shots">
          {job.shots.map((shot) => <article className="mc-card mc-video-shot" key={shot.shotId}>
            <header><span className="mc-eyebrow">{String(shot.order).padStart(2, '0')} · {shot.chapterId}</span><StatusBadge tone={badgeTone(shot.state)}>{label(shot.state)}</StatusBadge></header>
            <h2>{shot.title}</h2>
            {shot.clipUrl ? <video controls preload="metadata" src={shot.clipUrl} aria-label={`${shot.title} generated clip`} /> : <div className="mc-video-shot__placeholder"><Film aria-hidden="true" /><span>Clip not verified yet</span></div>}
            <p>{shot.prompt}</p>
            <AdvancedDetails summary="Negative prompt, seed, and continuity"><p>{shot.negativePrompt}</p><p><code>Seed {shot.seed}</code> · variation {shot.variationIndex} · {shot.continuationMode}</p><p>Groups: {shot.continuityGroupIds.join(', ') || 'none'}{shot.referenceAssetId ? ` · reference ${shot.referenceAssetId}` : ''}</p></AdvancedDetails>
            <footer><span>Attempt {shot.attemptCount || '—'} · {money(shot.reservedCostUsd)}</span><div className="mc-button-row">
              {shot.state === 'verified' ? <><Button tone="quiet" onClick={() => { void run(`accept-${shot.shotId}`, () => videoGenerationClient.review(job.jobId, shot.shotId, 'accepted')) }}><Check aria-hidden="true" />Accept</Button><Button tone="quiet" onClick={() => { void run(`reject-${shot.shotId}`, () => videoGenerationClient.review(job.jobId, shot.shotId, 'rejected')) }}>Reject</Button></> : null}
              {['failed', 'filtered', 'verified'].includes(shot.state) ? <><Button busy={busy === `retry-${shot.shotId}`} onClick={() => { void run(`retry-${shot.shotId}`, () => videoGenerationClient.retry(job.jobId, shot.shotId, 'same_setup')) }}>Retry same setup</Button><Button tone="quiet" busy={busy === `variation-${shot.shotId}`} onClick={() => { void run(`variation-${shot.shotId}`, () => videoGenerationClient.retry(job.jobId, shot.shotId, 'new_variation'), () => setConfirmation('')) }}>Generate new variation</Button></> : null}
              {shot.previousShotId && job.shots.some((candidate) => candidate.shotId === shot.previousShotId && candidate.reviewState === 'accepted') ? <Button tone="quiet" busy={busy === `chain-${shot.shotId}`} onClick={() => { void run(`chain-${shot.shotId}`, () => videoGenerationClient.chainReference(job.jobId, shot.shotId, shot.previousShotId as string), () => setConfirmation('')) }}>Use previous accepted end frame</Button> : null}
            </div></footer>
            {shot.error ? <small className="mc-video-shot__error">{shot.error.summary}</small> : null}
          </article>)}
        </section>

        <section className="mc-card mc-video-delivery">
          <div className="mc-card__heading"><div><span className="mc-eyebrow">Step 3 · local finishing package</span><h2>Automatic assembly and Resolve handoff</h2></div><Download aria-hidden="true" /></div>
          <div className="mc-video-audio-binding" aria-live="polite">
            <div>
              <strong>Original master</strong>
              <span className="mc-eyebrow">{busy?.startsWith('audio-') ? 'Selecting · binding · verifying' : job.audioMasterBound ? 'Audio master bound · verified' : 'No audio selected'}</span>
              {job.audioMasterBound ? <p>{job.audio.displayName} · {job.audio.durationSeconds?.toFixed(3)} seconds · {job.audio.sampleRateHz?.toLocaleString()} Hz · {job.audio.channels} channels</p> : <p>No verified audio is bound to this saved video job.</p>}
              {job.audio.source ? <small>Source: {job.audio.source === 'analysis-retained' ? 'retained analysis audio' : 'local selection'} · verified: {job.audio.verified ? 'yes' : 'no'}</small> : null}
              {job.audio.sha256 ? <small>SHA-256 <code>{job.audio.sha256}</code></small> : null}
            </div>
            <div className="mc-button-row">
              {catalog?.analyses.find((item) => item.analysisJobId === job.analysisJobId)?.retainedAudioAvailable ? <Button busy={busy === 'audio-retained'} onClick={() => { void bindRetainedAudio() }}>Use retained analysis audio</Button> : null}
              <Button busy={busy === 'audio-selecting'} icon={<FolderOpen aria-hidden="true" />} onClick={() => { void pickAudio() }}>{job.audioMasterBound ? 'Replace audio…' : 'Browse for audio…'}</Button>
              {job.audioMasterBound ? <Button tone="quiet" busy={busy === 'audio-clearing'} onClick={() => { void clearAudio() }}>Clear binding</Button> : null}
            </div>
          </div>
          {!job.audioMasterBound ? <Notice tone="warning" title="Audio master not bound"><p>Choose the original local master. Mission Control verifies it, stores an immutable private copy, and binds it to this job without changing the provider plan.</p></Notice> : null}
          <div className="mc-button-row">
            <Button disabled={!job.audioMasterBound || job.verifiedShotCount !== job.totalShotCount} onClick={() => { void run('resolve', () => videoGenerationClient.action(job.jobId, 'resolve')) }}>Resolve timeline</Button>
            <Button disabled={!job.audioMasterBound || !job.artifacts.timelineReady} onClick={() => { void run('export', () => videoGenerationClient.action(job.jobId, 'export')) }}>Export Resolve package</Button>
            <Button tone="primary" disabled={!job.audioMasterBound || !job.artifacts.davinciPackageReady} onClick={() => { void run('assemble', () => videoGenerationClient.action(job.jobId, 'assemble')) }}>Assemble full preview</Button>
            <Button icon={<FolderOpen aria-hidden="true" />} onClick={() => { void videoGenerationClient.openOutput(job.jobId).catch((caught) => setError(errorFromUnknown(caught, 'Output folder could not open'))) }}>Open output</Button>
          </div>
          {job.artifacts.previewUrl ? <video className="mc-video-preview" controls preload="metadata" src={job.artifacts.previewUrl} aria-label="Complete assembled music video" /> : null}
          {artifactLinks.length > 0 ? <div className="mc-video-artifacts">{artifactLinks.map(([name, url]) => <a key={name} href={url} download><ExternalLink aria-hidden="true" />{name}</a>)}</div> : null}
        </section>
      </> : null}
    </div>
  )
}
