import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FolderOpen,
  HardDrive,
  Image as ImageIcon,
  LockKeyhole,
  Monitor,
  Play,
  SearchCheck,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { errorFromUnknown } from '../api'
import {
  AdvancedDetails,
  Button,
  CheckMark,
  ErrorCard,
  Modal,
  Notice,
  SectionHeading,
  StatusBadge,
} from '../components'
import { formatDuration, formatGiB, sentenceCase, shortHash } from '../format'
import type {
  AuthorizationResult,
  ConnectionState,
  DashboardSnapshot,
  DryRunResult,
  LogEntry,
  MissionControlClient,
  OutputInspection,
  PreflightResult,
  RenderJob,
  RenderProfileSummary,
  RenderSelection,
  StructuredError,
} from '../types'
import { LiveProgress } from './LiveProgress'

const wizardSteps = [
  { id: 1, label: 'Project' },
  { id: 2, label: 'Profile' },
  { id: 3, label: 'Output' },
  { id: 4, label: 'Preflight' },
  { id: 5, label: 'Authorize' },
  { id: 6, label: 'Start' },
] as const

type WizardStep = typeof wizardSteps[number]['id']

function defaultOutputVariantIds(profile: RenderProfileSummary | undefined): string[] {
  return profile?.outputVariants
    .filter((variant) => variant.required || variant.enabledByDefault)
    .map((variant) => variant.id) ?? []
}

function authorized(profile: RenderProfileSummary | undefined, result: AuthorizationResult | null): boolean {
  return result?.authorized === true || profile?.authorizationStatus === 'authorized'
}

export function RenderWorkspace({
  data,
  client,
  advanced,
  initialProfileId,
  resetKey,
  activeJob,
  connection,
  logs,
  jobBusyAction,
  onJobStarted,
  onRefreshJob,
  onStopAfterChunk,
  onCancelStop,
  onCancelRender,
  onRetryCurrentChunk,
  onRetryFailedRender,
  onResumeJob,
  onRefreshData,
  onOpenOutput,
  onEncode,
  onDismissJobError,
}: {
  data: DashboardSnapshot
  client: MissionControlClient
  advanced: boolean
  initialProfileId: string | null
  resetKey: number
  activeJob: RenderJob | null
  connection: ConnectionState
  logs: LogEntry[]
  jobBusyAction: string | null
  onJobStarted: (job: RenderJob) => void
  onRefreshJob: (job: RenderJob) => void
  onStopAfterChunk: (job: RenderJob) => void
  onCancelStop: (job: RenderJob) => void
  onCancelRender: (job: RenderJob) => void
  onRetryCurrentChunk: (job: RenderJob) => void
  onRetryFailedRender: (job: RenderJob) => void
  onResumeJob: (job: RenderJob) => void
  onRefreshData: () => Promise<void>
  onOpenOutput: (path: string) => void
  onEncode: () => void
  onDismissJobError: () => void
}) {
  const defaultProject = data.projects.find((item) => item.current) ?? data.projects[0]
  const defaultScene = data.scenes.find((item) => item.id === defaultProject?.recommendedSceneId)
    ?? data.scenes.find((item) => item.projectId === defaultProject?.id && item.approved)
    ?? data.scenes[0]
  const defaultProfile = data.profiles.find((item) => item.id === initialProfileId)
    ?? data.profiles.find((item) => item.id === defaultProject?.recommendedProfileId)
    ?? data.profiles.find((item) => item.recommended)
    ?? data.profiles[0]

  const [step, setStep] = useState<WizardStep>(1)
  const [projectId, setProjectId] = useState(defaultProject?.id ?? '')
  const [sceneId, setSceneId] = useState(defaultScene?.id ?? '')
  const [profileId, setProfileId] = useState(defaultProfile?.id ?? '')
  const [enabledOutputVariantIds, setEnabledOutputVariantIds] = useState<string[]>(
    defaultOutputVariantIds(defaultProfile),
  )
  const [outputPath, setOutputPath] = useState('')
  const [inspection, setInspection] = useState<OutputInspection | null>(null)
  const [preflight, setPreflight] = useState<PreflightResult | null>(null)
  const [authorization, setAuthorization] = useState<AuthorizationResult | null>(null)
  const [dryRunResult, setDryRunResult] = useState<DryRunResult | null>(null)
  const [confirmation, setConfirmation] = useState<'closed' | 'review' | 'approve'>('closed')
  const [fullRenderChecked, setFullRenderChecked] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<StructuredError | null>(null)

  useEffect(() => {
    setStep(1)
    setProjectId(defaultProject?.id ?? '')
    setSceneId(defaultScene?.id ?? '')
    setProfileId(defaultProfile?.id ?? '')
    setEnabledOutputVariantIds(defaultOutputVariantIds(defaultProfile))
    setOutputPath('')
    setInspection(null)
    setPreflight(null)
    setAuthorization(null)
    setDryRunResult(null)
    setConfirmation('closed')
    setFullRenderChecked(false)
    setActionError(null)
  // resetKey intentionally represents an explicit request to start a fresh wizard.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  const project = data.projects.find((item) => item.id === projectId)
  const scene = data.scenes.find((item) => item.id === sceneId)
  const profile = data.profiles.find((item) => item.id === profileId)
  const scenes = data.scenes.filter((item) => !projectId || item.projectId === projectId)
  const visibleProfiles = useMemo(
    () => data.profiles.filter(
      (item) => (!projectId || !item.projectId || item.projectId === projectId)
        && (advanced || !/4k.*ultra|ultra.*4k/i.test(item.displayName)),
    ),
    [advanced, data.profiles, projectId],
  )
  const selection: RenderSelection = {
    projectId,
    sceneId,
    profileId,
    outputPath,
    enabledOutputVariantIds,
  }
  const outputVariantSelectionValid = !profile?.outputVariants.length
    || (
      enabledOutputVariantIds.length > 0
      && profile.outputVariants
        .filter((variant) => variant.required)
        .every((variant) => enabledOutputVariantIds.includes(variant.id))
    )
  const selectedOutputVariants = profile?.outputVariants.filter(
    (variant) => enabledOutputVariantIds.includes(variant.id),
  ) ?? []
  const renderer = data.system.capabilities.demoMode ? 'fake' as const : 'production' as const
  const preflightCanAuthorize = preflight?.authorizationRequired === true
    && preflight.checks.every((check) => check.status !== 'fail')

  if (activeJob) {
    const activeProfile = data.profiles.find((item) => item.id === activeJob.profileId)
    const activeScene = data.scenes.find((item) => item.id === activeJob.sceneId)
    return (
      <LiveProgress
        job={activeJob}
        connection={connection}
        logs={logs}
        busyAction={jobBusyAction}
        advanced={advanced}
        fallbackFps={activeProfile?.fps ?? activeScene?.fps ?? null}
        onRefresh={() => onRefreshJob(activeJob)}
        onStopAfterChunk={() => onStopAfterChunk(activeJob)}
        onCancelStop={() => onCancelStop(activeJob)}
        onCancelRender={() => onCancelRender(activeJob)}
        onRetryCurrentChunk={() => onRetryCurrentChunk(activeJob)}
        onRetryFailedRender={() => onRetryFailedRender(activeJob)}
        onResume={() => onResumeJob(activeJob)}
        onOpenOutput={() => { if (activeJob.outputPath) onOpenOutput(activeJob.outputPath) }}
        onEncode={onEncode}
        onDismissError={onDismissJobError}
      />
    )
  }

  const run = async <T,>(key: string, action: () => Promise<T>, complete: (value: T) => void): Promise<void> => {
    setBusy(key)
    setActionError(null)
    try {
      complete(await action())
    } catch (error) {
      setActionError(errorFromUnknown(error))
    } finally {
      setBusy(null)
    }
  }

  const chooseProject = (nextProjectId: string): void => {
    const nextProject = data.projects.find((item) => item.id === nextProjectId)
    const nextScene = data.scenes.find((item) => item.id === nextProject?.recommendedSceneId)
      ?? data.scenes.find((item) => item.projectId === nextProjectId && item.approved)
      ?? data.scenes.find((item) => item.projectId === nextProjectId)
    setProjectId(nextProjectId)
    setSceneId(nextScene?.id ?? '')
    if (nextProject?.recommendedProfileId) {
      const nextProfile = data.profiles.find((item) => item.id === nextProject.recommendedProfileId)
      setProfileId(nextProject.recommendedProfileId)
      setEnabledOutputVariantIds(defaultOutputVariantIds(nextProfile))
    }
    setInspection(null)
    setPreflight(null)
    setAuthorization(null)
  }

  const chooseProfile = (nextProfileId: string): void => {
    const nextProfile = data.profiles.find((item) => item.id === nextProfileId)
    setProfileId(nextProfileId)
    if (nextProfile?.sceneId) setSceneId(nextProfile.sceneId)
    setEnabledOutputVariantIds(defaultOutputVariantIds(nextProfile))
    setInspection(null)
    setPreflight(null)
    setAuthorization(null)
  }

  const toggleOutputVariant = (variantId: string, enabled: boolean): void => {
    setEnabledOutputVariantIds((current) => (
      enabled
        ? Array.from(new Set([...current, variantId]))
        : current.filter((id) => id !== variantId)
    ))
    setInspection(null)
    setPreflight(null)
    setAuthorization(null)
    setDryRunResult(null)
  }

  const browseOutput = (): void => {
    void run('browse', () => client.selectFolder(data.paths.outputDefault), (selected) => {
      if (selected.cancelled || !selected.path) return
      const nextSelection = { ...selection, outputPath: selected.path }
      setOutputPath(selected.path)
      void run('inspect', () => client.inspectOutput(nextSelection), setInspection)
    })
  }

  const createChild = (): void => {
    if (!outputPath) return
    void run('create-child', () => client.createOutputChild(selection), (result) => {
      setOutputPath(result.path)
      setInspection(result)
    })
  }

  const runPreflight = (): void => {
    void run('preflight', () => client.preflight(selection, renderer), setPreflight)
  }

  const openAuthorization = (): void => {
    setFullRenderChecked(false)
    setConfirmation('review')
  }

  const authorizeRender = (): void => {
    if (!project || !scene || !profile || !preflight || !fullRenderChecked) return
    setConfirmation('closed')
    setBusy('authorize')
    setActionError(null)
    void (async () => {
      try {
        const result = await client.authorize({
          projectId,
          sceneId,
          profileId,
          outputPath,
          expectedSeconds: profile.expectedSeconds,
          totalFrames: scene.totalFrames,
          storageGiB: profile.storageGiB,
          exactOperation: preflight.exactOperation,
          enabledOutputVariantIds,
        }, { configurationReviewed: true, fullRenderApproved: true })
        if (!result.authorized) throw new Error('The local service did not save an authorization record.')
        setAuthorization(result)
        const refreshed = await client.preflight(selection, renderer)
        setPreflight(refreshed)
        if (!refreshed.ready) {
          throw new Error('Authorization was saved, but the refreshed production preflight is not ready. Review its checks and retry.')
        }
        setStep(6)
        await onRefreshData()
      } catch (error) {
        setActionError(errorFromUnknown(error, 'Authorization could not be completed'))
      } finally {
        setBusy(null)
      }
    })()
  }

  const start = (dryRun: boolean): void => {
    if (!preflight?.ready) return
    const request = {
      ...selection,
      authorizationId: authorization?.authorizationId ?? null,
      performanceMode: data.settings?.performance.enabled ?? false,
      renderer,
      fake: data.system.capabilities.demoMode ? {
        totalFrames: 120,
        framesPerChunk: 30,
        stepDelaySeconds: 0.1,
        longFrameAt: 8,
      } : undefined,
    }
    if (dryRun) {
      void run('dry-run', () => client.dryRun(request), setDryRunResult)
      return
    }
    void run('start', () => client.startRender(request), onJobStarted)
  }

  const goBack = (): void => {
    if (step > 1) setStep((step - 1) as WizardStep)
  }

  const goForward = (): void => {
    if (step === 1 && projectId && sceneId) setStep(2)
    else if (step === 2 && profileId && outputVariantSelectionValid) setStep(3)
    else if (step === 3 && inspection?.usable) setStep(4)
    else if (step === 4 && (preflight?.ready || preflightCanAuthorize)) setStep(preflight?.authorizationRequired || !authorized(profile, authorization) ? 5 : 6)
    else if (step === 5 && authorized(profile, authorization)) setStep(6)
  }

  return (
    <div className="mc-page mc-wizard-page">
      <SectionHeading
        eyebrow="Guided local workflow"
        title="New render"
        description="Mission Control keeps the approved scene, measured profile, authorization, and output identity bound together."
      />

      <nav className="mc-wizard-steps" aria-label="New render progress">
        <ol>
          {wizardSteps.map((item) => (
            <li key={item.id} className={item.id === step ? 'is-current' : item.id < step ? 'is-complete' : ''} aria-current={item.id === step ? 'step' : undefined}>
              <button type="button" disabled={item.id > step} onClick={() => { if (item.id <= step) setStep(item.id) }}>
                <span>{item.id < step ? <Check aria-hidden="true" /> : item.id}</span><small>{item.label}</small>
              </button>
              {item.id < wizardSteps.length ? <ChevronRight aria-hidden="true" /> : null}
            </li>
          ))}
        </ol>
      </nav>

      {actionError ? <ErrorCard error={actionError} onRetry={actionError.retryable ? () => setActionError(null) : undefined} onDismiss={() => setActionError(null)} retryLabel="Review and retry" /> : null}

      <section className="mc-wizard-card">
        {step === 1 ? (
          <div className="mc-wizard-panel">
            <div className="mc-wizard-panel__heading"><span className="mc-step-number">1</span><div><h2>Choose a project and approved scene</h2><p>The current approved scene is selected automatically when available.</p></div></div>
            {data.projects.length > 1 ? (
              <label className="mc-field"><span>Project</span><select value={projectId} onChange={(event) => chooseProject(event.target.value)}>{data.projects.map((item) => <option key={item.id} value={item.id}>{item.displayName}</option>)}</select></label>
            ) : null}
            <div className="mc-scene-grid" role="radiogroup" aria-label="Approved scenes">
              {scenes.map((item) => (
                <label key={item.id} className={`mc-scene-option ${sceneId === item.id ? 'is-selected' : ''}`}>
                  <input type="radio" name="scene" value={item.id} checked={sceneId === item.id} onChange={() => { setSceneId(item.id); setInspection(null); setPreflight(null); setAuthorization(null) }} />
                  <span className="mc-scene-option__image">
                    {item.thumbnailUrl ? <img src={item.thumbnailUrl} alt="" /> : <span><ImageIcon aria-hidden="true" /></span>}
                  </span>
                  <span className="mc-scene-option__content">
                    <span className="mc-badge-row"><StatusBadge tone={item.status === 'verified' ? 'success' : item.status === 'missing' ? 'error' : 'warning'}>{sentenceCase(item.status)}</StatusBadge>{item.approved ? <StatusBadge tone="info">Approved</StatusBadge> : null}</span>
                    <strong>{item.displayName}</strong>
                    <small>{item.totalFrames.toLocaleString()} frames · {item.fps} fps</small>
                  </span>
                </label>
              ))}
            </div>
            {advanced && scene ? (
              <AdvancedDetails summary="Scene identity">
                <dl className="mc-technical-list"><div><dt>Scene ID</dt><dd><code>{scene.id}</code></dd></div><div><dt>SHA-256</dt><dd><code>{scene.sha256 ?? 'Unavailable'}</code></dd></div><div><dt>Saved file</dt><dd><code>{scene.path ?? 'Unavailable'}</code></dd></div></dl>
              </AdvancedDetails>
            ) : null}
          </div>
        ) : null}

        {step === 2 ? (
          <div className="mc-wizard-panel">
            <div className="mc-wizard-panel__heading"><span className="mc-step-number">2</span><div><h2>Select a render profile</h2><p>The measured local recommendation appears first. Higher resolutions may take substantially longer.</p></div></div>
            <div className="mc-wizard-profile-list" role="radiogroup" aria-label="Render profiles">
              {visibleProfiles.map((item) => (
                <label key={item.id} className={`mc-wizard-profile ${profileId === item.id ? 'is-selected' : ''}`}>
                  <input type="radio" name="profile" value={item.id} checked={profileId === item.id} onChange={() => chooseProfile(item.id)} />
                  <span className="mc-wizard-profile__radio" aria-hidden="true" />
                  <span className="mc-wizard-profile__main">
                    <span className="mc-badge-row">{item.recommended ? <StatusBadge tone="info">Recommended</StatusBadge> : null}{item.authorizationStatus === 'authorized' ? <StatusBadge tone="success">Authorized</StatusBadge> : item.authorizationStatus === 'required' ? <StatusBadge tone="warning">Authorization required</StatusBadge> : null}</span>
                    <strong>{item.displayName}</strong>
                    <small>{item.qualityDescription ?? item.qualityRole}</small>
                  </span>
                  <span className="mc-wizard-profile__facts"><span><Monitor aria-hidden="true" /> {item.width} × {item.height}</span><span><Clock3 aria-hidden="true" /> {formatDuration(item.expectedSeconds, true)}</span><span><HardDrive aria-hidden="true" /> {formatGiB(item.storageGiB)}</span></span>
                </label>
              ))}
            </div>
            {profile?.outputVariants.length ? (
              <div className="mc-output-variant-selector" aria-labelledby="mc-output-variant-heading">
                <div>
                  <h3 id="mc-output-variant-heading">Output matrix for this job</h3>
                  <p>Optional formats run only when explicitly enabled here and stay bound to their own authored scene, calibration, preflight, and authorization.</p>
                </div>
                <div className="mc-output-variant-list">
                  {profile.outputVariants.map((variant) => {
                    const checked = enabledOutputVariantIds.includes(variant.id)
                    return (
                      <label key={variant.id} className={`mc-output-variant ${checked ? 'is-enabled' : ''}`}>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={variant.required}
                          onChange={(event) => toggleOutputVariant(variant.id, event.target.checked)}
                          aria-label={`${checked ? 'Disable' : 'Enable'} ${variant.id}`}
                        />
                        <span>
                          <strong>{variant.id}</strong>
                          <small>{variant.width} × {variant.height} · {variant.fps} fps · {sentenceCase(variant.deliverableRole)}</small>
                          <small>Authored composition: {variant.compositionProfileId}</small>
                        </span>
                        <StatusBadge tone={variant.required ? 'info' : checked ? 'warning' : 'neutral'}>
                          {variant.required ? 'Required' : checked ? 'Explicitly enabled' : 'Optional · off'}
                        </StatusBadge>
                      </label>
                    )
                  })}
                </div>
                {!outputVariantSelectionValid ? (
                  <Notice tone="warning" title="Select an output variant">
                    <p>This optional profile is disabled by default. Explicitly enable it to continue; production preflight will still require its separate calibration and exact operator authorization.</p>
                  </Notice>
                ) : null}
              </div>
            ) : null}
            {!advanced && data.profiles.length > visibleProfiles.length ? <p className="mc-muted">Additional profiles that are not recommended locally are available in Advanced mode.</p> : null}
          </div>
        ) : null}

        {step === 3 ? (
          <div className="mc-wizard-panel">
            <div className="mc-wizard-panel__heading"><span className="mc-step-number">3</span><div><h2>Choose an output folder</h2><p>The local service opens a native folder picker, then inspects every conflicting entry safely.</p></div></div>
            {!data.system.capabilities.nativeFolderPicker ? (
              <Notice tone="warning" title="Native Browse is unavailable"><p>The backend has not reported a native folder-picker bridge. Mission Control will not pretend a browser-only picker provides a usable local path.</p></Notice>
            ) : null}
            <div className="mc-folder-picker">
              <div><FolderOpen aria-hidden="true" /><span><small>Render output</small><strong>{outputPath || 'No folder selected'}</strong></span></div>
              <Button icon={<FolderOpen aria-hidden="true" />} busy={busy === 'browse' || busy === 'inspect'} disabled={!data.system.capabilities.nativeFolderPicker} onClick={browseOutput}>Browse…</Button>
            </div>
            {inspection ? (
              <div className={`mc-output-inspection mc-output-inspection--${inspection.usable ? 'success' : 'warning'}`}>
                <div className="mc-output-inspection__heading">
                  {inspection.usable ? <CheckCircle2 aria-hidden="true" /> : <HardDrive aria-hidden="true" />}
                  <div><strong>{inspection.usable ? inspection.resumable ? 'Compatible resumable render' : 'Folder is ready' : 'This folder cannot be used directly'}</strong><p>{inspection.message}</p></div>
                </div>
                {inspection.entries.length > 0 ? (
                  <div><span>This folder contains:</span><ul>{inspection.entries.map((entry) => <li key={entry}><code>{entry}</code></li>)}</ul></div>
                ) : null}
                {!inspection.usable ? <Button tone="primary" icon={<Sparkles aria-hidden="true" />} busy={busy === 'create-child'} onClick={createChild}>Create a new render folder here</Button> : null}
                {advanced && inspection.conflictingIdentity ? (
                  <AdvancedDetails summary="Conflicting render identity">
                    <dl className="mc-technical-list"><div><dt>Project</dt><dd><code>{inspection.conflictingIdentity.projectId ?? 'Unknown'}</code></dd></div><div><dt>Scene</dt><dd><code>{inspection.conflictingIdentity.sceneId ?? 'Unknown'} · {shortHash(inspection.conflictingIdentity.sceneSha256)}</code></dd></div><div><dt>Profile</dt><dd><code>{inspection.conflictingIdentity.profileId ?? 'Unknown'} · {shortHash(inspection.conflictingIdentity.profileSha256)}</code></dd></div></dl>
                  </AdvancedDetails>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        {step === 4 ? (
          <div className="mc-wizard-panel">
            <div className="mc-wizard-panel__heading"><span className="mc-step-number">4</span><div><h2>Check render readiness</h2><p>Production preflight verifies the exact saved scene, profile, folder, storage, and active-job state.</p></div></div>
            {!preflight ? (
              <div className="mc-preflight-start"><SearchCheck aria-hidden="true" /><h3>Ready to inspect</h3><p>This does not start Blender or render a frame.</p><Button tone="primary" busy={busy === 'preflight'} onClick={runPreflight}>Run production preflight</Button></div>
            ) : (
              <>
                <div className="mc-preflight-summary"><StatusBadge tone={preflightCanAuthorize ? 'warning' : preflight.ready ? 'success' : 'error'}>{preflightCanAuthorize ? 'Ready to authorize' : preflight.ready ? 'Ready to start' : 'Preflight needs attention'}</StatusBadge><Button tone="quiet" busy={busy === 'preflight'} onClick={runPreflight}>Run again</Button></div>
                <ul className="mc-check-list">
                  {preflight.checks.map((check) => <li key={check.id}><CheckMark status={check.status} /><div><strong>{check.label}</strong><p>{check.summary}</p>{advanced && check.technicalDetails ? <code>{check.technicalDetails}</code> : null}</div></li>)}
                </ul>
                {advanced && preflight.rawDetails ? <AdvancedDetails summary="Raw preflight details"><pre>{JSON.stringify(preflight.rawDetails, null, 2)}</pre></AdvancedDetails> : null}
              </>
            )}
          </div>
        ) : null}

        {step === 5 ? (
          <div className="mc-wizard-panel mc-authorization-panel">
            <div className="mc-wizard-panel__heading"><span className="mc-step-number">5</span><div><h2>Authorization</h2><p>Approve this exact scene and saved profile without copying a token or leaving the wizard.</p></div></div>
            {authorized(profile, authorization) ? (
              <div className="mc-authorized-state"><span><ShieldCheck aria-hidden="true" /></span><div><StatusBadge tone="success">Authorized</StatusBadge><h3>This exact render configuration is approved.</h3><p>You can continue directly to the final Start screen.</p></div></div>
            ) : (
              <>
                <Notice tone="warning" title="Authorization required"><p>This profile is valid and ready, but it has not yet been authorized for a full production render.</p></Notice>
                <div className="mc-authorization-review">
                  <dl>
                    <div><dt>Project</dt><dd>{project?.displayName ?? projectId}</dd></div>
                    <div><dt>Scene</dt><dd>{scene?.displayName ?? sceneId}</dd></div>
                    <div><dt>Profile</dt><dd>{profile?.displayName ?? profileId}</dd></div>
                    <div><dt>Output matrix</dt><dd>{selectedOutputVariants.map((variant) => variant.id).join(', ') || 'Profile default'}</dd></div>
                    <div><dt>Resolution</dt><dd>{profile ? `${profile.width} × ${profile.height}` : 'Unavailable'}</dd></div>
                    <div><dt>Frames</dt><dd>{scene?.totalFrames.toLocaleString() ?? 'Unavailable'}</dd></div>
                    <div><dt>Expected</dt><dd>{formatDuration(profile?.expectedSeconds ?? null)}</dd></div>
                    <div><dt>Output</dt><dd>{outputPath}</dd></div>
                    <div><dt>Storage</dt><dd>{formatGiB(profile?.storageGiB ?? null)} frames · {formatGiB(profile?.minimumFreeGiB ?? null)} minimum free</dd></div>
                  </dl>
                </div>
                <div className="mc-button-row"><Button tone="primary" icon={<LockKeyhole aria-hidden="true" />} busy={busy === 'authorize'} onClick={openAuthorization}>Authorize now</Button>{advanced ? <Button tone="quiet" onClick={() => setConfirmation('review')}>View details</Button> : null}</div>
              </>
            )}
          </div>
        ) : null}

        {step === 6 ? (
          <div className="mc-wizard-panel mc-start-panel">
            <div className="mc-wizard-panel__heading"><span className="mc-step-number">6</span><div><h2>Ready to start</h2><p>The backend will use the exact saved profile and authorization identity reviewed here.</p></div></div>
            <div className="mc-start-summary">
              <div className="mc-start-summary__icon"><Play aria-hidden="true" /></div>
              <div><span className="mc-eyebrow">{project?.displayName}</span><h3>{profile?.displayName}</h3><p>{scene?.displayName} · {scene?.totalFrames.toLocaleString()} frames · {formatDuration(profile?.expectedSeconds ?? null)}</p><code>{outputPath}</code></div>
              <div className="mc-badge-stack"><StatusBadge tone="success">Preflight passed</StatusBadge><StatusBadge tone="success">Authorized</StatusBadge>{data.settings?.performance.enabled ? <StatusBadge tone="info">Performance mode</StatusBadge> : null}</div>
            </div>
            {!data.system.capabilities.renderExecution ? <Notice tone="warning" title="Render execution is unavailable"><p>The backend has not enabled the production render executor. Start remains disabled; a safe dry-run may still be available.</p></Notice> : null}
            {dryRunResult ? <Notice tone={dryRunResult.ok ? 'success' : 'warning'} title={dryRunResult.ok ? 'Dry-run passed' : 'Dry-run needs attention'}><p>{dryRunResult.ok ? 'The exact production command and safety plan were validated. No renderer was started.' : 'Review the dry-run technical details before starting.'}</p></Notice> : null}
            <div className="mc-start-actions"><Button tone="primary" icon={<Play aria-hidden="true" />} busy={busy === 'start'} disabled={!data.system.capabilities.renderExecution} onClick={() => start(false)}>Start render</Button><Button busy={busy === 'dry-run'} onClick={() => start(true)}>Run dry-run</Button></div>
            {advanced && dryRunResult ? <AdvancedDetails summary="Dry-run plan"><pre>{JSON.stringify(dryRunResult.plan, null, 2)}</pre>{dryRunResult.logLines.length > 0 ? <pre>{dryRunResult.logLines.join('\n')}</pre> : null}</AdvancedDetails> : null}
            {advanced ? (
              <AdvancedDetails summary="Exact authorized identity">
                <dl className="mc-technical-list"><div><dt>Scene SHA-256</dt><dd><code>{authorization?.sceneSha256 ?? preflight?.sceneSha256 ?? scene?.sha256 ?? 'Unavailable'}</code></dd></div><div><dt>Profile SHA-256</dt><dd><code>{authorization?.profileSha256 ?? preflight?.profileSha256 ?? profile?.savedFileSha256 ?? 'Unavailable'}</code></dd></div><div><dt>Authorization record</dt><dd><code>{authorization?.authorizationId ?? 'Existing saved authorization'}</code></dd></div>{authorization?.token ? <div><dt>Generated token</dt><dd><code>{authorization.token}</code></dd></div> : null}</dl>
              </AdvancedDetails>
            ) : null}
          </div>
        ) : null}

        <footer className="mc-wizard-footer">
          <Button tone="quiet" icon={<ArrowLeft aria-hidden="true" />} disabled={step === 1 || busy !== null} onClick={goBack}>Back</Button>
          <span>Step {step} of {wizardSteps.length}</span>
          {step < 6 ? <Button tone="primary" disabled={(step === 1 && (!projectId || !sceneId)) || (step === 2 && (!profileId || !outputVariantSelectionValid)) || (step === 3 && !inspection?.usable) || (step === 4 && !(preflight?.ready || preflightCanAuthorize)) || (step === 5 && !authorized(profile, authorization)) || busy !== null} onClick={goForward}>Continue <ArrowRight aria-hidden="true" /></Button> : <span />}
        </footer>
      </section>

      <Modal
        open={confirmation === 'review'}
        title="Authorize this render configuration?"
        description="Review the full operation before the final production confirmation."
        onClose={() => setConfirmation('closed')}
        footer={<><Button onClick={() => setConfirmation('closed')}>Cancel</Button><Button tone="primary" onClick={() => setConfirmation('approve')}>Review and continue</Button></>}
      >
        <div className="mc-modal-review">
          <dl><div><dt>Project</dt><dd>{project?.displayName}</dd></div><div><dt>Scene</dt><dd>{scene?.displayName}</dd></div><div><dt>Profile</dt><dd>{profile?.displayName}</dd></div><div><dt>Output matrix</dt><dd>{selectedOutputVariants.map((variant) => variant.id).join(', ') || 'Profile default'}</dd></div><div><dt>Resolution</dt><dd>{profile ? `${profile.width} × ${profile.height}` : 'Unavailable'}</dd></div><div><dt>Frame count</dt><dd>{scene?.totalFrames.toLocaleString()}</dd></div><div><dt>Expected duration</dt><dd>{formatDuration(profile?.expectedSeconds ?? null)}</dd></div><div><dt>Output path</dt><dd><code>{outputPath}</code></dd></div><div><dt>Operation</dt><dd>{preflight?.exactOperation ?? 'Full production render'}</dd></div></dl>
          {advanced ? <AdvancedDetails summary="Exact identity"><p><strong>Scene:</strong> <code>{shortHash(preflight?.sceneSha256 ?? scene?.sha256 ?? null)}</code></p><p><strong>Profile:</strong> <code>{shortHash(preflight?.profileSha256 ?? profile?.savedFileSha256 ?? null)}</code></p></AdvancedDetails> : null}
        </div>
      </Modal>

      <Modal
        open={confirmation === 'approve'}
        title="Ready to authorize the exact scene and profile?"
        description="This is the second and final confirmation required by the production engine."
        onClose={() => setConfirmation('closed')}
        footer={<><Button onClick={() => setConfirmation('review')}>Back</Button><Button tone="primary" disabled={!fullRenderChecked} onClick={authorizeRender}>Authorize render</Button></>}
      >
        <label className="mc-confirm-checkbox"><input type="checkbox" checked={fullRenderChecked} onChange={(event) => setFullRenderChecked(event.target.checked)} /><span><strong>I understand this authorizes a full production render.</strong><small>The saved authorization is bound to the exact scene and profile identity.</small></span></label>
      </Modal>
    </div>
  )
}
