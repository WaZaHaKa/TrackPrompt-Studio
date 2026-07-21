import { ArrowRight, Clock3, FolderOpen, History, Play, ShieldCheck } from 'lucide-react'
import { Button, EmptyState, ProgressBar, SectionHeading, StatusBadge } from '../components'
import { formatDateTime, percent, sentenceCase } from '../format'
import type { RenderJob } from '../types'

function jobTone(job: RenderJob): 'success' | 'warning' | 'error' | 'neutral' | 'info' {
  if (job.state === 'complete') return 'success'
  if (job.state === 'failed' || job.state === 'cancelled') return 'error'
  if (job.state === 'paused_safely' || job.state === 'resumable') return 'warning'
  if (job.state === 'running' || job.state === 'starting' || job.state === 'stop_requested' || job.state === 'finishing_current_chunk') return 'info'
  return 'neutral'
}

export function JobsScreen({
  jobs,
  busyJobId,
  onView,
  onResume,
  onOpenOutput,
}: {
  jobs: RenderJob[]
  busyJobId: string | null
  onView: (job: RenderJob) => void
  onResume: (job: RenderJob) => void
  onOpenOutput: (job: RenderJob) => void
}) {
  const ordered = [...jobs].sort((left, right) => new Date(right.updatedAt).valueOf() - new Date(left.updatedAt).valueOf())
  return (
    <div className="mc-page">
      <SectionHeading
        eyebrow="Persistent local jobs"
        title="Jobs & history"
        description="Renders continue in the local service when this browser closes. Safely paused jobs retain their exact scene and profile identity."
      />
      {ordered.length === 0 ? (
        <EmptyState
          icon={<History aria-hidden="true" />}
          title="No render jobs yet"
          description="Start a new render and its durable progress will appear here."
        />
      ) : (
        <div className="mc-job-list">
          {ordered.map((job) => {
            const progress = percent(job.publishedFrames, job.totalFrames)
            return (
              <article key={job.jobId} className="mc-job-card">
                <div className="mc-job-card__main">
                  <div className="mc-job-card__heading">
                    <div>
                      <span className="mc-eyebrow">{job.projectName ?? 'Render project'}</span>
                      <h2>{job.profileName ?? job.profileId ?? 'Saved render profile'}</h2>
                    </div>
                    <StatusBadge tone={jobTone(job)}>{sentenceCase(job.state)}</StatusBadge>
                  </div>
                  <p>{job.sceneName ?? job.sceneId ?? 'Approved scene'} · updated {formatDateTime(job.updatedAt)}</p>
                  <div className="mc-job-card__progress">
                    <ProgressBar value={progress} label={`${job.projectName ?? 'Render'} safe progress`} />
                    <strong>{Math.round(progress)}%</strong>
                  </div>
                  <div className="mc-inline-metrics">
                    <span><ShieldCheck aria-hidden="true" /> {job.publishedFrames.toLocaleString()} safe / {job.totalFrames.toLocaleString()} total</span>
                    <span><Clock3 aria-hidden="true" /> {job.estimatedCompletionAt ? `ETA ${formatDateTime(job.estimatedCompletionAt)}` : sentenceCase(job.phase)}</span>
                  </div>
                </div>
                <div className="mc-job-card__actions">
                  {job.canResume ? (
                    <Button tone="primary" icon={<Play aria-hidden="true" />} busy={busyJobId === job.jobId} onClick={() => onResume(job)}>Resume safely</Button>
                  ) : (
                    <Button icon={<ArrowRight aria-hidden="true" />} onClick={() => onView(job)}>View {job.state === 'complete' ? 'details' : 'progress'}</Button>
                  )}
                  {job.outputPath ? <Button tone="quiet" icon={<FolderOpen aria-hidden="true" />} onClick={() => onOpenOutput(job)}>Open output</Button> : null}
                </div>
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}
