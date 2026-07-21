import {
  Activity,
  BarChart3,
  Cloud,
  Film,
  Gauge,
  History,
  Home,
  LockKeyhole,
  Menu,
  Moon,
  Play,
  Settings,
  SlidersHorizontal,
  Sun,
  Wifi,
  WifiOff,
  X,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createMissionControlClient, errorFromUnknown } from './api'
import { Button, ErrorCard, Modal, Skeleton, StatusBadge } from './components'
import { createRenderEventSubscriber } from './events'
import { sentenceCase } from './format'
import { CalibrationScreen } from './screens/CalibrationScreen'
import { CloudScreen } from './screens/CloudScreen'
import { EncodeScreen } from './screens/EncodeScreen'
import { HomeScreen } from './screens/HomeScreen'
import { JobsScreen } from './screens/JobsScreen'
import { ProfilesScreen } from './screens/ProfilesScreen'
import { RenderWorkspace } from './screens/RenderWorkspace'
import { SettingsScreen } from './screens/SettingsScreen'
import type {
  ConnectionState,
  DashboardSnapshot,
  EncodeCandidate,
  EncodeJob,
  LogEntry,
  MissionControlClient,
  MissionSection,
  RenderEvent,
  RenderEventSubscriber,
  RenderJob,
  StructuredError,
} from './types'

const ADVANCED_KEY = 'wzhk.mission-control.advanced'

const navItems: Array<{ id: MissionSection; label: string; icon: typeof Home }> = [
  { id: 'home', label: 'Home', icon: Home },
  { id: 'render', label: 'Render', icon: Play },
  { id: 'profiles', label: 'Profiles', icon: SlidersHorizontal },
  { id: 'calibration', label: 'Calibration', icon: BarChart3 },
  { id: 'jobs', label: 'Jobs', icon: History },
  { id: 'encode', label: 'Encode', icon: Film },
  { id: 'cloud', label: 'Cloud', icon: Cloud },
  { id: 'settings', label: 'Settings', icon: Settings },
]

const liveStates = new Set(['starting', 'running', 'stop_requested', 'finishing_current_chunk', 'encoding', 'verifying'])
const reconnectableStates = new Set([...liveStates, 'paused_safely', 'resumable'])

function readAdvancedPreference(): boolean {
  try {
    return window.localStorage.getItem(ADVANCED_KEY) === 'true'
  } catch {
    return false
  }
}

function mergeJobEvent(job: RenderJob, event: RenderEvent): RenderJob {
  return {
    ...job,
    ...event,
    outputPath: job.outputPath,
    projectName: job.projectName,
    sceneName: job.sceneName,
    profileName: job.profileName,
    previewUrl: event.previewUrl ?? job.previewUrl,
    previewFrame: event.previewFrame ?? job.previewFrame,
    lastCompletedFrame: event.lastCompletedFrame ?? job.lastCompletedFrame ?? event.previewFrame,
    rendererActive: event.rendererActive ?? job.rendererActive,
    watcherActive: event.watcherActive ?? job.watcherActive,
    currentFrameStartedAt: event.currentFrameStartedAt ?? job.currentFrameStartedAt,
    lastOutputAt: event.lastOutputAt ?? (event.previewFrame !== null ? event.timestamp : job.lastOutputAt),
    canResume: event.state === 'paused_safely' || event.state === 'resumable' || job.canResume,
    canEncode: event.state === 'complete' || job.canEncode,
    updatedAt: event.timestamp,
  }
}

export interface MissionControlAppProps {
  client?: MissionControlClient
  eventSubscriber?: RenderEventSubscriber
  initialSection?: MissionSection
  idleHealthIntervalMs?: number
}

export function MissionControlApp({
  client: suppliedClient,
  eventSubscriber: suppliedSubscriber,
  initialSection = 'home',
  idleHealthIntervalMs = 5_000,
}: MissionControlAppProps) {
  const clientRef = useRef<MissionControlClient>(suppliedClient ?? createMissionControlClient())
  const subscriberRef = useRef<RenderEventSubscriber>(suppliedSubscriber ?? createRenderEventSubscriber())
  const client = clientRef.current
  const subscriber = subscriberRef.current

  const [section, setSection] = useState<MissionSection>(initialSection)
  const [data, setData] = useState<DashboardSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<StructuredError | null>(null)
  const [actionError, setActionError] = useState<StructuredError | null>(null)
  const [connection, setConnection] = useState<ConnectionState>('reconnecting')
  const [activeJob, setActiveJob] = useState<RenderJob | null>(null)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [advanced, setAdvanced] = useState(readAdvancedPreference)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [wizardResetKey, setWizardResetKey] = useState(0)
  const [initialProfileId, setInitialProfileId] = useState<string | null>(null)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [activeEncode, setActiveEncode] = useState<EncodeJob | null>(null)
  const [performanceTarget, setPerformanceTarget] = useState<boolean | null>(null)
  const [performanceConfirmed, setPerformanceConfirmed] = useState(false)

  const loadDashboard = useCallback(async (showLoading = false): Promise<void> => {
    if (showLoading) setLoading(true)
    setLoadError(null)
    try {
      const [system, paths, projects, scenes, profiles, jobs] = await Promise.all([
        client.getSystemStatus(),
        client.getSystemPaths(),
        client.listProjects(),
        client.listScenes(),
        client.listProfiles(),
        client.listJobs(),
      ])
      const [calibrationResult, cloudResult, settingsResult, encodeResult] = await Promise.allSettled([
        client.listCalibrations(),
        client.getCloudReadiness(),
        client.getSettings(),
        client.listEncodeCandidates(),
      ])
      const settings = settingsResult.status === 'fulfilled' ? settingsResult.value : null
      const cloud = cloudResult.status === 'fulfilled' ? cloudResult.value : null
      const fakeModeRequested = settings?.fakeRendererAvailable === true
        && new URLSearchParams(window.location.search).get('renderer') === 'fake'
      const enrichedSystem = {
        ...system,
        ffmpegReady: paths.ffmpegPath !== null,
        rendererBusy: jobs.some((job) => liveStates.has(job.state)),
        capabilities: {
          ...system.capabilities,
          renderExecution: system.capabilities.renderExecution || fakeModeRequested,
          encode: false,
          performanceMode: settings?.performance.supported ?? false,
          cloudPreparation: false,
          cloudLive: cloud?.liveProvisioningVerified === true && cloud.liveFleetVerified,
          demoMode: fakeModeRequested,
        },
      }
      const enrichedJobs = jobs.map((job) => ({
        ...job,
        projectName: projects.find((project) => project.id === job.projectId)?.displayName ?? job.projectName,
        sceneName: scenes.find((scene) => scene.id === job.sceneId)?.displayName ?? job.sceneName,
        profileName: profiles.find((profile) => profile.id === job.profileId)?.displayName ?? job.profileName,
      }))
      const reportedJob = system.activeJobId ? enrichedJobs.find((job) => job.jobId === system.activeJobId) : undefined
      const reconnectableJob = reportedJob ?? enrichedJobs.find((job) => reconnectableStates.has(job.state))
      const next: DashboardSnapshot = {
        system: enrichedSystem,
        paths,
        projects,
        scenes,
        profiles,
        jobs: enrichedJobs,
        calibrations: calibrationResult.status === 'fulfilled' ? calibrationResult.value : [],
        cloud,
        settings,
        encodeCandidates: encodeResult.status === 'fulfilled' ? encodeResult.value : [],
      }
      setData(next)
      setActiveJob((current) => {
        if (current) return enrichedJobs.find((job) => job.jobId === current.jobId) ?? current
        return reconnectableJob ?? null
      })
      if (reconnectableJob) setSection((current) => current === 'home' ? 'render' : current)
      setConnection('connected')
    } catch (error) {
      setLoadError(errorFromUnknown(error, 'Mission Control could not load'))
      setConnection('offline')
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => { void loadDashboard(true) }, [loadDashboard])

  const refreshEncodeCandidates = useCallback(async (): Promise<EncodeCandidate[]> => {
    const encodeCandidates = await client.listEncodeCandidates()
    setData((current) => current ? { ...current, encodeCandidates } : current)
    return encodeCandidates
  }, [client])

  const applyJob = useCallback((job: RenderJob): void => {
    setActiveJob(job)
    setData((current) => current ? {
      ...current,
      jobs: current.jobs.some((item) => item.jobId === job.jobId)
        ? current.jobs.map((item) => item.jobId === job.jobId ? job : item)
        : [job, ...current.jobs],
    } : current)
  }, [])

  const activeJobId = activeJob?.jobId ?? null
  const activeJobIsLive = activeJob ? liveStates.has(activeJob.state) : false

  useEffect(() => {
    if (!activeJobId || !activeJobIsLive) {
      return
    }
    const subscription = subscriber.subscribe({
      jobId: activeJobId,
      afterSequence: 0,
      onConnection: setConnection,
      onEvent: (event) => {
        setActiveJob((current) => {
          if (!current || current.jobId !== event.jobId) return current
          const next = mergeJobEvent(current, event)
          setData((snapshot) => snapshot ? {
            ...snapshot,
            jobs: snapshot.jobs.map((job) => job.jobId === next.jobId ? next : job),
          } : snapshot)
          return next
        })
        if (event.state === 'complete') {
          void refreshEncodeCandidates().catch(() => undefined)
        }
        if (event.latestLogLine) {
          setLogs((current) => {
            if (current.some((entry) => entry.sequence === event.sequence && entry.message === event.latestLogLine)) return current
            return [...current.slice(-499), {
              sequence: event.sequence,
              timestamp: event.timestamp,
              level: event.error ? 'error' : event.warning ? 'warning' : 'info',
              message: event.latestLogLine ?? '',
              technicalDetails: null,
            }]
          })
        }
      },
      onError: () => {
        // Connection state and the last authoritative job remain visible while the stream reconnects.
      },
    })
    return subscription.close
  }, [activeJobId, activeJobIsLive, refreshEncodeCandidates, subscriber])

  const dashboardLoaded = data !== null
  useEffect(() => {
    if (!dashboardLoaded || activeJobIsLive) return
    let cancelled = false
    let consecutiveFailures = 0
    let nextCheck: number | undefined

    const checkHealth = async (): Promise<void> => {
      try {
        await client.checkHealth()
        if (cancelled) return
        consecutiveFailures = 0
        setConnection('connected')
      } catch {
        if (cancelled) return
        consecutiveFailures += 1
        setConnection(consecutiveFailures >= 3 || navigator.onLine === false ? 'offline' : 'reconnecting')
      } finally {
        if (!cancelled) {
          nextCheck = window.setTimeout(() => { void checkHealth() }, idleHealthIntervalMs)
        }
      }
    }

    void checkHealth()
    return () => {
      cancelled = true
      if (nextCheck !== undefined) window.clearTimeout(nextCheck)
    }
  }, [activeJobIsLive, client, dashboardLoaded, idleHealthIntervalMs])

  useEffect(() => {
    if (!activeJobId) {
      setLogs([])
      return
    }
    let cancelled = false
    let lastSequence = 0
    const refreshLogs = async (): Promise<void> => {
      try {
        const next = await client.getRenderLogs(activeJobId, lastSequence)
        if (cancelled || next.length === 0) return
        lastSequence = Math.max(lastSequence, ...next.map((entry) => entry.sequence))
        setLogs((current) => {
          const known = new Set(current.map((entry) => `${entry.sequence}:${entry.message}`))
          return [...current, ...next.filter((entry) => !known.has(`${entry.sequence}:${entry.message}`))].slice(-500)
        })
      } catch {
        // The live render status remains primary; log retrieval retries on the next bounded interval.
      }
    }
    setLogs([])
    void refreshLogs()
    const timer = activeJobIsLive ? window.setInterval(() => { void refreshLogs() }, 5000) : undefined
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearInterval(timer)
    }
  }, [activeJobId, activeJobIsLive, client])

  const navigate = (next: MissionSection): void => {
    setSection(next)
    setMobileNavOpen(false)
  }

  const startWizard = (profileId?: string): void => {
    if (activeJob && reconnectableStates.has(activeJob.state)) {
      navigate('render')
      return
    }
    setActiveJob(null)
    setInitialProfileId(profileId ?? null)
    setWizardResetKey((value) => value + 1)
    navigate('render')
  }

  const runAction = async <T,>(key: string, action: () => Promise<T>, success: (value: T) => void): Promise<void> => {
    setBusyAction(key)
    setActionError(null)
    try {
      success(await action())
    } catch (error) {
      setActionError(errorFromUnknown(error))
    } finally {
      setBusyAction(null)
    }
  }

  const openPath = (path: string): void => {
    void runAction('open-path', () => client.openPath(path), () => setToast('Opened in File Explorer.'))
  }

  const runJobAction = (key: string, action: () => Promise<RenderJob>): void => {
    void runAction(key, action, applyJob)
  }

  const setAdvancedPreference = (value: boolean): void => {
    setAdvanced(value)
    try { window.localStorage.setItem(ADVANCED_KEY, String(value)) } catch { /* local preference remains in memory */ }
    if (data?.settings) {
      void client.updateSettings({ simpleMode: !value }).then((settings) => setData((current) => current ? { ...current, settings } : current)).catch(() => undefined)
    }
  }

  const updateTheme = (theme: 'system' | 'light' | 'dark'): void => {
    if (!data?.settings) return
    void runAction('theme', () => client.updateSettings({ theme }), (settings) => setData((current) => current ? { ...current, settings } : current))
  }

  const requestPerformance = (enabled: boolean): void => {
    setPerformanceConfirmed(false)
    setPerformanceTarget(enabled)
  }

  const applyPerformance = (): void => {
    if (performanceTarget === null || !data?.settings || !performanceConfirmed) return
    const target = performanceTarget
    setPerformanceTarget(null)
    void runAction('performance', () => client.updateSettings({ performance: { ...data.settings!.performance, enabled: target } }), (settings) => {
      setData((current) => current ? { ...current, settings } : current)
      setToast(target ? 'Performance mode enabled for local renders.' : 'Original performance settings restored.')
    })
  }

  const createCalibration = (): void => {
    void runAction('calibration', () => client.createCalibrationPlan(), (calibration) => {
      setData((current) => current ? { ...current, calibrations: [calibration, ...current.calibrations] } : current)
      setToast('Bounded calibration plan created.')
    })
  }

  const runCalibrationCandidate = (calibrationId: string, candidateId: string): void => {
    void runAction('calibration', () => client.startCalibrationCandidate(calibrationId, candidateId), (calibration) => {
      setData((current) => current ? { ...current, calibrations: current.calibrations.map((item) => item.id === calibration.id ? calibration : item) } : current)
    })
  }

  const startEncode = (jobId: string, includeAudio: boolean): void => {
    void runAction(`encode:${jobId}`, () => client.startEncode(jobId, includeAudio), (job) => {
      setActiveEncode(job)
      setToast('Encode started in the local service.')
    })
  }

  const openEncode = (): void => {
    void runAction('encode-candidates', refreshEncodeCandidates, () => navigate('encode'))
  }

  const prepareCloud = (): void => {
    const project = data?.projects.find((item) => item.current) ?? data?.projects[0]
    const profile = data?.profiles.find((item) => item.recommended) ?? data?.profiles[0]
    const scene = data?.scenes.find((item) => item.id === project?.recommendedSceneId)
      ?? data?.scenes.find((item) => item.approved)
    const outputPath = data?.paths.outputDefault
    if (!profile || !scene || !outputPath) {
      setActionError(errorFromUnknown(new Error('A profile, approved scene, and default output folder are required.'), 'Cloud package is not ready'))
      return
    }
    void runAction('cloud-package', () => client.prepareCloudPackage(profile.id, scene.id, outputPath), (result) => {
      setToast(result.message)
      void loadDashboard()
    })
  }

  const currentTheme = data?.settings?.theme ?? 'system'
  const pageLabel = navItems.find((item) => item.id === section)?.label ?? 'Mission Control'

  const content = data ? (() => {
    switch (section) {
      case 'home':
        return <HomeScreen data={data} onNavigate={navigate} onStartRender={() => startWizard()} onOpenOutput={(job) => { if (job.outputPath) openPath(job.outputPath) }} />
      case 'render':
        return (
          <RenderWorkspace
            data={data}
            client={client}
            advanced={advanced}
            initialProfileId={initialProfileId}
            resetKey={wizardResetKey}
            activeJob={activeJob}
            connection={connection}
            logs={logs}
            jobBusyAction={busyAction}
            onJobStarted={(job) => { applyJob(job); setConnection('reconnecting') }}
            onRefreshJob={(job) => runJobAction('refresh', () => client.getRenderJob(job.jobId))}
            onStopAfterChunk={(job) => runJobAction('stop', () => client.requestStopAfterChunk(job.jobId))}
            onCancelStop={(job) => runJobAction('cancel-stop', () => client.cancelStopRequest(job.jobId))}
            onResumeJob={(job) => runJobAction('resume', () => client.resumeRender(job.jobId))}
            onRefreshData={() => loadDashboard()}
            onOpenOutput={openPath}
            onEncode={openEncode}
            onDismissJobError={() => setActiveJob((job) => job ? { ...job, error: null } : job)}
          />
        )
      case 'profiles':
        return <ProfilesScreen profiles={data.profiles} advanced={advanced} onUseProfile={startWizard} />
      case 'calibration':
        return <CalibrationScreen calibrations={data.calibrations} busy={busyAction === 'calibration'} onCreatePlan={createCalibration} onRunCandidate={runCalibrationCandidate} onDismissError={(calibrationId) => setData((current) => current ? { ...current, calibrations: current.calibrations.map((item) => item.id === calibrationId ? { ...item, recoverableError: null } : item) } : current)} />
      case 'jobs':
        return <JobsScreen jobs={data.jobs} busyJobId={busyAction?.startsWith('resume:') ? busyAction.slice(7) : null} onView={(job) => { setActiveJob(job); navigate('render') }} onResume={(job) => runJobAction(`resume:${job.jobId}`, () => client.resumeRender(job.jobId))} onOpenOutput={(job) => { if (job.outputPath) openPath(job.outputPath) }} />
      case 'encode':
        return <EncodeScreen candidates={data.encodeCandidates} capabilityAvailable={data.system.capabilities.encode} activeEncode={activeEncode} busyJobId={busyAction?.startsWith('encode:') ? busyAction.slice(7) : null} onStartEncode={startEncode} onOpenOutput={openPath} />
      case 'cloud':
        return <CloudScreen readiness={data.cloud} preparationAvailable={data.system.capabilities.cloudPreparation} busy={busyAction === 'cloud-package'} onRefresh={() => { void loadDashboard() }} onPreparePackage={prepareCloud} />
      case 'settings':
        return <SettingsScreen settings={data.settings} paths={data.paths} system={data.system} advanced={advanced} busy={busyAction === 'performance'} onAdvancedChange={setAdvancedPreference} onThemeChange={updateTheme} onPerformanceChange={requestPerformance} />
    }
  })() : null

  return (
    <div className="mc-root" data-theme={currentTheme}>
      <a className="mc-skip-link" href="#mission-control-main">Skip to main content</a>
      <aside className={`mc-sidebar ${mobileNavOpen ? 'is-open' : ''}`} aria-label="Mission Control navigation">
        <div className="mc-brand">
          <span className="mc-brand__mark"><Activity aria-hidden="true" /></span>
          <span><strong>WZHK</strong><small>MEDIA · MISSION CONTROL</small></span>
          <button className="mc-icon-button mc-sidebar__close" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)}><X aria-hidden="true" /></button>
        </div>
        <nav className="mc-nav">
          {navItems.map((item) => {
            const Icon = item.icon
            return <button key={item.id} className={section === item.id ? 'is-active' : ''} aria-current={section === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><Icon aria-hidden="true" /><span>{item.label}</span>{item.id === 'render' && activeJobIsLive ? <i aria-label="Active render" /> : null}</button>
          })}
        </nav>
        <div className="mc-sidebar__footer">
          <div className="mc-sidebar__status">
            {connection === 'connected' ? <Wifi aria-hidden="true" /> : <WifiOff aria-hidden="true" />}
            <span><strong>{sentenceCase(connection)}</strong><small>{data?.system.machineName ?? 'Local service'}</small></span>
          </div>
          <div className="mc-sidebar__privacy"><LockKeyhole aria-hidden="true" /><span>Local & private<small>No telemetry</small></span></div>
        </div>
      </aside>
      {mobileNavOpen ? <button className="mc-nav-scrim" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)} /> : null}

      <div className="mc-workspace">
        <header className="mc-topbar">
          <div className="mc-topbar__title"><button className="mc-icon-button mc-menu-button" aria-label="Open navigation" onClick={() => setMobileNavOpen(true)}><Menu aria-hidden="true" /></button><span>{pageLabel}</span></div>
          <div className="mc-topbar__actions">
            {activeJobIsLive ? <button className="mc-render-pill" onClick={() => navigate('render')}><span /><strong>Render active</strong><small>{activeJob?.currentFrame ? `Frame ${activeJob.currentFrame.toLocaleString()}` : sentenceCase(activeJob?.phase ?? '')}</small></button> : null}
            <label className="mc-mode-toggle" title="Show exact hashes, paths, and metrics"><span>{advanced ? 'Advanced' : 'Simple'}</span><input type="checkbox" role="switch" checked={advanced} onChange={(event) => setAdvancedPreference(event.target.checked)} /></label>
            <button className="mc-icon-button" aria-label={currentTheme === 'dark' ? 'Use light theme' : 'Use dark theme'} onClick={() => updateTheme(currentTheme === 'dark' ? 'light' : 'dark')}>{currentTheme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}</button>
            <span className={`mc-topbar__connection mc-topbar__connection--${connection}`} title={`Backend ${connection}`}>{connection === 'connected' ? <Wifi aria-hidden="true" /> : <WifiOff aria-hidden="true" />}</span>
          </div>
        </header>

        <main id="mission-control-main" tabIndex={-1}>
          {loading && !data ? <div className="mc-loading"><Skeleton lines={5} /><Skeleton lines={8} /></div> : null}
          {loadError ? <ErrorCard error={loadError} onRetry={() => { void loadDashboard(true) }} /> : null}
          {actionError ? <div className="mc-global-error"><ErrorCard error={actionError} onRetry={actionError.retryable ? () => setActionError(null) : undefined} onDismiss={() => setActionError(null)} /></div> : null}
          {content}
        </main>
      </div>

      <Modal
        open={performanceTarget !== null}
        title={performanceTarget ? 'Maximize local render performance?' : 'Restore normal Windows settings?'}
        description={performanceTarget
          ? 'Mission Control will use the Windows High Performance power plan, prevent sleep during rendering, and give Blender higher process priority.'
          : 'Mission Control will restore the power and sleep settings recorded before performance mode was enabled.'}
        onClose={() => setPerformanceTarget(null)}
        footer={<><Button onClick={() => setPerformanceTarget(null)}>Cancel</Button><Button tone="primary" icon={<Zap aria-hidden="true" />} disabled={!performanceConfirmed} onClick={applyPerformance}>{performanceTarget ? 'Enable performance mode' : 'Restore settings'}</Button></>}
      >
        <label className="mc-confirm-checkbox"><input type="checkbox" checked={performanceConfirmed} onChange={(event) => setPerformanceConfirmed(event.target.checked)} /><span><strong>{performanceTarget ? 'I understand these temporary system changes.' : 'Restore the previously recorded settings now.'}</strong><small>Realtime process priority is never used. Original settings are recorded and restored by the backend.</small></span></label>
      </Modal>

      {toast ? <div className="mc-toast" role="status"><CheckCircleToast /><span>{toast}</span><button aria-label="Dismiss notification" onClick={() => setToast(null)}><X aria-hidden="true" /></button></div> : null}
    </div>
  )
}

function CheckCircleToast() {
  return <StatusBadge tone="success"><Gauge aria-hidden="true" /> Done</StatusBadge>
}

export default MissionControlApp
