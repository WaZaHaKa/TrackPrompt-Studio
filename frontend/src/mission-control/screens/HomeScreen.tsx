import { ArrowRight, CheckCircle2, Clock3, Film, FolderOpen, Gauge, HardDrive, Play, ShieldCheck } from 'lucide-react'
import { Button, EmptyState, Metric, Notice, ProgressBar, SectionHeading, StatusBadge } from '../components'
import { formatDuration, formatGiB, percent, sentenceCase } from '../format'
import type { DashboardSnapshot, MissionSection, RenderJob } from '../types'

const activeStates = new Set(['starting', 'running', 'stop_requested', 'finishing_current_chunk', 'encoding', 'verifying'])

export function HomeScreen({
  data,
  onNavigate,
  onStartRender,
  onOpenOutput,
}: {
  data: DashboardSnapshot
  onNavigate: (section: MissionSection) => void
  onStartRender: () => void
  onOpenOutput: (job: RenderJob) => void
}) {
  const project = data.projects.find((item) => item.current) ?? data.projects[0]
  const recommended = data.profiles.find((item) => item.recommended)
    ?? data.profiles.find((item) => item.id === project?.recommendedProfileId)
    ?? data.profiles[0]
  const scene = data.scenes.find((item) => item.id === project?.recommendedSceneId)
    ?? data.scenes.find((item) => item.projectId === project?.id && item.approved)
    ?? data.scenes[0]
  const activeJob = data.jobs.find((item) => activeStates.has(item.state))
  const recentJob = activeJob ?? data.jobs[0]
  const renderReady = data.system.ready && data.system.blenderReady && scene?.status === 'verified'
  const encodeReadiness = !data.system.ffmpegReady
    ? 'FFmpeg is not configured; local rendering is still available.'
    : data.system.capabilities.encode
      ? 'FFmpeg and verified encoding are ready after rendering.'
      : 'FFmpeg was found; verified encoding control is not connected yet.'

  return (
    <div className="mc-page mc-home">
      <SectionHeading
        eyebrow="Mission Control"
        title="Good to see you."
        description="Prepare, run, and finish a local production render without leaving this workspace."
      />

      {data.system.capabilities.demoMode ? (
        <Notice tone="warning" title="Simulation mode">
          <p>The local service explicitly reports a simulated renderer. Production actions will not start Blender.</p>
        </Notice>
      ) : null}

      {data.system.warnings.length > 0 ? (
        <Notice tone="warning" title="One item needs attention">
          <p>{data.system.warnings[0]}</p>
        </Notice>
      ) : null}

      {!project || !recommended ? (
        <EmptyState
          title="No render project is ready yet"
          description="Mission Control could not find a saved project and render profile. Check Settings, then refresh."
          action={<Button onClick={() => onNavigate('settings')}>Review settings</Button>}
        />
      ) : (
        <section className="mc-hero-card" aria-labelledby="mc-current-project">
          <div className="mc-hero-card__content">
            <div className="mc-hero-card__topline">
              <StatusBadge tone={renderReady ? recommended.authorizationStatus === 'authorized' ? 'success' : 'warning' : 'error'}>
                {renderReady
                  ? recommended.authorizationStatus === 'authorized' ? 'Ready to render' : 'Ready to authorize'
                  : 'Setup needs attention'}
              </StatusBadge>
              <span className="mc-private-label"><ShieldCheck aria-hidden="true" /> Local and private</span>
            </div>
            <h2 id="mc-current-project">{project.displayName}</h2>
            <p>{project.description ?? 'Your approved scene and measured render profile are ready for review.'}</p>
            <div className="mc-hero-card__metrics">
              <Metric label="Recommended" value={recommended.displayName} detail={`${recommended.width} × ${recommended.height} · ${recommended.qualityRole}`} />
              <Metric label="Expected" value={formatDuration(recommended.expectedSeconds)} detail={recommended.conservativeSeconds ? `Allow up to ${formatDuration(recommended.conservativeSeconds, true)}` : undefined} />
              <Metric label="Storage" value={formatGiB(recommended.storageGiB)} detail={recommended.minimumFreeGiB ? `${formatGiB(recommended.minimumFreeGiB)} minimum free` : undefined} />
            </div>
            <div className="mc-button-row mc-button-row--hero">
              <Button tone="primary" icon={<Play aria-hidden="true" />} onClick={onStartRender}>
                {activeJob ? 'View active render' : 'Start a new render'}
              </Button>
              <Button tone="quiet" onClick={() => onNavigate('profiles')}>View profile</Button>
            </div>
          </div>
          <div className="mc-hero-card__visual" aria-hidden="true">
            {project.thumbnailUrl || scene?.thumbnailUrl ? (
              <img src={project.thumbnailUrl ?? scene?.thumbnailUrl ?? ''} alt="" />
            ) : (
              <div className="mc-orbit-visual"><span /><span /><span /><i /></div>
            )}
          </div>
        </section>
      )}

      <div className="mc-home-grid">
        <section className="mc-card mc-readiness-card">
          <div className="mc-card__heading">
            <div><span className="mc-eyebrow">Local system</span><h2>Render readiness</h2></div>
            <StatusBadge tone={renderReady ? 'success' : 'warning'}>{renderReady ? 'Ready' : 'Review'}</StatusBadge>
          </div>
          <ul className="mc-plain-list mc-readiness-list">
            <li><CheckCircle2 aria-hidden="true" /><span><strong>Blender</strong><small>{data.system.blenderReady ? 'Found and available' : 'Needs setup'}</small></span></li>
            <li><CheckCircle2 aria-hidden="true" /><span><strong>Approved scene</strong><small>{scene?.status === 'verified' ? 'Identity verified' : scene ? sentenceCase(scene.status) : 'Not found'}</small></span></li>
            <li><CheckCircle2 aria-hidden="true" /><span><strong>Recommended profile</strong><small>{recommended?.calibrated ? 'Measured on this workflow' : 'Calibration status unavailable'}</small></span></li>
            <li><Film aria-hidden="true" /><span><strong>Video encoding</strong><small>{encodeReadiness}</small></span></li>
          </ul>
          <Button tone="quiet" icon={<Gauge aria-hidden="true" />} onClick={onStartRender}>Run preflight <ArrowRight aria-hidden="true" /></Button>
        </section>

        <section className="mc-card mc-job-summary">
          <div className="mc-card__heading">
            <div><span className="mc-eyebrow">Current activity</span><h2>{recentJob ? recentJob.projectName ?? 'Render job' : 'No render running'}</h2></div>
            {recentJob ? <StatusBadge tone={activeJob ? 'info' : recentJob.state === 'complete' ? 'success' : recentJob.state === 'failed' ? 'error' : 'neutral'}>{sentenceCase(recentJob.state)}</StatusBadge> : null}
          </div>
          {recentJob ? (
            <>
              <div className="mc-job-summary__progress">
                <div><strong>{Math.round(percent(recentJob.publishedFrames, recentJob.totalFrames))}%</strong><span>{recentJob.publishedFrames.toLocaleString()} safe frames</span></div>
                <ProgressBar value={percent(recentJob.publishedFrames, recentJob.totalFrames)} label="Published render progress" />
              </div>
              <div className="mc-inline-metrics">
                <span><Clock3 aria-hidden="true" /> {recentJob.estimatedCompletionAt ? new Date(recentJob.estimatedCompletionAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : 'ETA calculating'}</span>
                <span><HardDrive aria-hidden="true" /> {recentJob.outputPath ? 'Output selected' : 'No output path'}</span>
              </div>
              <div className="mc-button-row">
                <Button tone="quiet" onClick={() => onNavigate(activeJob ? 'render' : 'jobs')}>{activeJob ? 'View live progress' : 'View history'} <ArrowRight aria-hidden="true" /></Button>
                {recentJob.state === 'complete' && recentJob.outputPath ? <Button tone="quiet" icon={<FolderOpen aria-hidden="true" />} onClick={() => onOpenOutput(recentJob)}>Open output</Button> : null}
              </div>
            </>
          ) : (
            <div className="mc-card-empty">
              <p>Your next render will appear here and remain reconnectable if this tab closes.</p>
              <Button tone="quiet" onClick={onStartRender}>Create a render</Button>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
