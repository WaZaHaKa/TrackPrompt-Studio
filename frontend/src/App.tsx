import { useCallback, useEffect, useRef, useState } from 'react'
import { AudioLines, Check, LockKeyhole, Server, WifiOff, X } from 'lucide-react'
import {
  ApiError,
  cancelAnalysis,
  createAnalysis,
  deleteAnalysis,
  generatePrompt,
  getAnalysis,
  getCapabilities,
  patchAnalysis,
  selectPromptCandidate,
  subscribeToAnalysisEvents,
} from './api'
import { ProgressPanel } from './components/ProgressPanel'
import { AnalysisLibrary } from './components/AnalysisLibrary'
import { CatalogueWorkspace } from './components/CatalogueWorkspace'
import { ResultsWorkspace } from './components/ResultsWorkspace'
import { UploadPanel } from './components/UploadPanel'
import type {
  AnalysisJob,
  AnalysisMode,
  AnalysisOptions,
  Capabilities,
  FactUpdate,
  PromptPackage,
  PromptPreferences,
} from './types'
import { DEFAULT_CAPABILITIES } from './types'

function readableError(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message
  return 'Something went wrong while contacting the local service.'
}

function isTerminal(status: AnalysisJob['status']): boolean {
  return ['completed', 'cancelled', 'failed', 'expired'].includes(status)
}

export default function App() {
  const [capabilities, setCapabilities] = useState<Capabilities>(DEFAULT_CAPABILITIES)
  const [capabilitiesLoading, setCapabilitiesLoading] = useState(true)
  const [capabilitiesError, setCapabilitiesError] = useState<string>()
  const [job, setJob] = useState<AnalysisJob>()
  const [sourceFile, setSourceFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [uploadError, setUploadError] = useState<string>()
  const [streamConnected, setStreamConnected] = useState(false)
  const [streamRecovering, setStreamRecovering] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [savingPath, setSavingPath] = useState<string>()
  const [deleting, setDeleting] = useState(false)
  const [toast, setToast] = useState<string>()
  const [workspace, setWorkspace] = useState<'single' | 'library' | 'catalogue'>('single')
  const recoveryTimer = useRef<number | undefined>(undefined)
  const coreToolsAvailable = capabilities.fastMode.available && capabilities.ffmpeg.available && capabilities.ffprobe.available
  const serviceProblem = capabilitiesError ?? (!capabilitiesLoading && !coreToolsAvailable ? 'Required local media tools are unavailable.' : undefined)
  const activeJobId = job?.jobId
  const activeJobStatus = job?.status

  useEffect(() => {
    let active = true
    void getCapabilities()
      .then((value) => {
        if (!active) return
        setCapabilities(value)
        setCapabilitiesError(undefined)
      })
      .catch((error: unknown) => {
        if (!active) return
        setCapabilitiesError(readableError(error))
      })
      .finally(() => {
        if (active) setCapabilitiesLoading(false)
      })
    return () => { active = false }
  }, [])

  const refreshJob = useCallback(async (jobId: string): Promise<AnalysisJob | undefined> => {
    try {
      const current = await getAnalysis(jobId)
      setJob(current)
      if (isTerminal(current.status)) {
        setStreamConnected(false)
        setStreamRecovering(false)
      }
      return current
    } catch (error) {
      setUploadError(readableError(error))
      return undefined
    }
  }, [])

  useEffect(() => {
    if (!activeJobId || !activeJobStatus || isTerminal(activeJobStatus)) return
    setStreamRecovering(false)
    const close = subscribeToAnalysisEvents(activeJobId, {
      onOpen: () => {
        setStreamConnected(true)
        setStreamRecovering(false)
      },
      onEvent: (event) => {
        setUploadError(undefined)
        setStreamConnected(true)
        setStreamRecovering(false)
        setJob((current) => current?.jobId === event.jobId
          ? {
              ...current,
              status: event.status,
              mode: event.mode ?? current.mode,
              stage: event.stage,
              message: event.message,
              updatedAt: event.timestamp,
              progress: event.progress ?? current.progress,
            }
          : current)
      },
      onTerminal: () => { void refreshJob(activeJobId) },
      onConnectionError: () => {
        setStreamConnected(false)
        setStreamRecovering(true)
        if (recoveryTimer.current) window.clearTimeout(recoveryTimer.current)
        recoveryTimer.current = window.setTimeout(() => { void refreshJob(activeJobId) }, 1200)
      },
    })
    return () => {
      close()
      if (recoveryTimer.current) window.clearTimeout(recoveryTimer.current)
    }
  }, [activeJobId, activeJobStatus, refreshJob])

  const startAnalysis = async (
    file: File,
    mode: AnalysisMode,
    options: AnalysisOptions,
  ): Promise<void> => {
    setSubmitting(true)
    setUploadError(undefined)
    try {
      const created = await createAnalysis(file, mode, options)
      setSourceFile(file)
      setJob(created)
      setToast(undefined)
    } catch (error) {
      setUploadError(readableError(error))
    } finally {
      setSubmitting(false)
    }
  }

  const cancel = async (): Promise<void> => {
    if (!job) return
    setCancelling(true)
    try {
      setJob(await cancelAnalysis(job.jobId))
    } catch (error) {
      setUploadError(readableError(error))
    } finally {
      setCancelling(false)
    }
  }

  const updateFacts = async (updates: FactUpdate[]): Promise<void> => {
    if (!job) return
    if (updates.length === 0) return
    setSavingPath(updates[0]?.path)
    try {
      const updated = await patchAnalysis(job.jobId, updates)
      setJob(updated)
    } finally {
      setSavingPath(undefined)
    }
  }

  const updateFact = async (update: FactUpdate): Promise<void> => updateFacts([update])

  const requestPrompt = async (preferences: PromptPreferences): Promise<PromptPackage> => {
    if (!job) throw new Error('The analysis job is no longer available.')
    const next = await generatePrompt(job.jobId, preferences)
    setJob((current) => current ? { ...current, promptPackage: next } : current)
    return next
  }

  const persistPromptCandidate = async (candidateId: string): Promise<PromptPackage> => {
    if (!job) throw new Error('The analysis job is no longer available.')
    const next = await selectPromptCandidate(job.jobId, candidateId)
    setJob((current) => current ? { ...current, promptPackage: next } : current)
    return next
  }

  const removeAnalysis = async (): Promise<void> => {
    if (!job) return
    setDeleting(true)
    try {
      await deleteAnalysis(job.jobId)
      setJob(undefined)
      setSourceFile(null)
      setUploadError(undefined)
      setToast('Analysis, audio, and temporary data deleted.')
    } finally {
      setDeleting(false)
    }
  }

  const startOver = async (): Promise<void> => {
    if (!job) return
    try {
      await deleteAnalysis(job.jobId)
      setJob(undefined)
      setSourceFile(null)
      setUploadError(undefined)
      setStreamConnected(false)
      setStreamRecovering(false)
    } catch (error) {
      setUploadError(readableError(error))
    }
  }

  const openLibraryAnalysis = async (analysisId: string): Promise<void> => {
    const archived = await getAnalysis(analysisId)
    setJob(archived)
    setSourceFile(null)
    setUploadError(undefined)
    setWorkspace('single')
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="site-header">
        <div className="brand"><span><AudioLines aria-hidden="true" /></span><div><strong>TrackPrompt</strong><small>STUDIO</small></div></div>
        <div className="site-header__status">
          <nav className="workspace-switcher" aria-label="Workspace">
            <button className={workspace === 'single' ? 'is-active' : ''} onClick={() => setWorkspace('single')}>Single track</button>
            <button className={workspace === 'library' ? 'is-active' : ''} onClick={() => setWorkspace('library')}>Analysis Library</button>
            <button className={workspace === 'catalogue' ? 'is-active' : ''} onClick={() => setWorkspace('catalogue')}>Client catalogue</button>
          </nav>
          <span className={`service-chip ${serviceProblem ? 'service-chip--offline' : ''}`} title={serviceProblem}>
            {serviceProblem ? <WifiOff aria-hidden="true" /> : <Server aria-hidden="true" />}
            {capabilitiesLoading ? 'Checking local tools…' : capabilitiesError ? 'Service unavailable' : !coreToolsAvailable ? 'Analysis tools unavailable' : 'Local service ready'}
          </span>
          <span className="privacy-chip"><LockKeyhole aria-hidden="true" /> Private · No telemetry</span>
        </div>
      </header>

      {workspace === 'library' ? (
        <AnalysisLibrary onOpen={openLibraryAnalysis} />
      ) : workspace === 'catalogue' ? (
        <CatalogueWorkspace capabilities={capabilities} />
      ) : !job ? (
        <UploadPanel
          capabilities={capabilities}
          capabilitiesLoading={capabilitiesLoading}
          capabilitiesError={capabilitiesError}
          uploadError={uploadError}
          submitting={submitting}
          onAnalyze={(file, mode, options) => void startAnalysis(file, mode, options)}
        />
      ) : job.status === 'completed' && job.analysis ? (
        <ResultsWorkspace
          jobId={job.jobId}
          analysis={job.analysis}
          promptPackage={job.promptPackage}
          capabilities={capabilities}
          sourceFile={sourceFile}
          requestedMode={job.requestedMode}
          effectiveMode={job.mode}
          savingPath={savingPath}
          deleting={deleting}
          onUpdateFact={updateFact}
          onUpdateFacts={updateFacts}
          onGeneratePrompt={requestPrompt}
          onSelectPromptCandidate={persistPromptCandidate}
          onRefreshAnalysis={async () => Boolean(await refreshJob(job.jobId))}
          onDelete={removeAnalysis}
        />
      ) : (
        <ProgressPanel
          job={job}
          streamConnected={streamConnected}
          streamRecovering={streamRecovering}
          cancelling={cancelling}
          actionError={uploadError}
          onCancel={() => void cancel()}
          onRefresh={() => void refreshJob(job.jobId)}
          onStartOver={() => { void startOver() }}
        />
      )}

      {toast ? (
        <div className="toast" role="status">
          <Check aria-hidden="true" /><span>{toast}</span>
          <button aria-label="Dismiss notification" onClick={() => setToast(undefined)}><X aria-hidden="true" /></button>
        </div>
      ) : null}
    </div>
  )
}
