import { Check, Clock3, Cloud, Gauge, LockKeyhole, Monitor, Play } from 'lucide-react'
import { AdvancedDetails, Button, EmptyState, SectionHeading, StatusBadge } from '../components'
import { formatDateTime, formatDuration, formatGiB, shortHash } from '../format'
import type { RenderProfileSummary } from '../types'

function authorizationBadge(profile: RenderProfileSummary) {
  if (profile.authorizationStatus === 'authorized') return <StatusBadge tone="success">Authorized</StatusBadge>
  if (profile.authorizationStatus === 'required') return <StatusBadge tone="warning">Authorization required</StatusBadge>
  if (profile.authorizationStatus === 'invalid') return <StatusBadge tone="error">Identity changed</StatusBadge>
  return <StatusBadge tone="neutral">Not checked</StatusBadge>
}

export function ProfilesScreen({
  profiles,
  advanced,
  onUseProfile,
}: {
  profiles: RenderProfileSummary[]
  advanced: boolean
  onUseProfile: (profileId: string) => void
}) {
  const ordered = [...profiles].sort((left, right) => Number(right.recommended) - Number(left.recommended) || left.height - right.height)
  return (
    <div className="mc-page">
      <SectionHeading
        eyebrow="Saved render profiles"
        title="Choose the right balance."
        description="Measured profiles preserve the render engine’s exact settings. Mission Control recommends the best local option first."
      />
      {ordered.length === 0 ? (
        <EmptyState
          icon={<Gauge aria-hidden="true" />}
          title="No saved profiles found"
          description="Check the profile root in Settings. Mission Control only shows profiles discovered by the local service."
        />
      ) : (
        <div className="mc-profile-grid">
          {ordered.map((profile) => (
            <article key={profile.id} className={`mc-profile-card ${profile.recommended ? 'mc-profile-card--recommended' : ''}`}>
              <div className="mc-profile-card__header">
                <div className="mc-profile-card__icon"><Monitor aria-hidden="true" /></div>
                <div>
                  <div className="mc-badge-row">
                    {profile.recommended ? <StatusBadge tone="info">Recommended</StatusBadge> : null}
                    {profile.calibrated ? <StatusBadge tone="success">Calibrated</StatusBadge> : <StatusBadge tone="neutral">Not measured</StatusBadge>}
                  </div>
                  <h2>{profile.displayName}</h2>
                  <p>{profile.qualityDescription ?? profile.qualityRole}</p>
                </div>
              </div>
              <dl className="mc-profile-facts">
                <div><dt>Resolution</dt><dd>{profile.width.toLocaleString()} × {profile.height.toLocaleString()} <small>{profile.fps} fps</small></dd></div>
                <div><dt>Expected</dt><dd><Clock3 aria-hidden="true" /> {formatDuration(profile.expectedSeconds)}</dd></div>
                <div><dt>Frame storage</dt><dd>{formatGiB(profile.storageGiB)}</dd></div>
                <div><dt>Best for</dt><dd>{profile.qualityRole}</dd></div>
              </dl>
              <div className="mc-profile-card__recommendation">
                {profile.localRecommendation?.toLowerCase().includes('cloud') ? <Cloud aria-hidden="true" /> : <Check aria-hidden="true" />}
                <span>{profile.localRecommendation ?? (profile.recommended ? 'Recommended for this local machine.' : 'Available as an alternate quality profile.')}</span>
              </div>
              <div className="mc-profile-card__authorization">
                <LockKeyhole aria-hidden="true" />
                <div><span>Production approval</span>{authorizationBadge(profile)}</div>
              </div>
              {advanced ? (
                <AdvancedDetails summary="Profile identity">
                  <dl className="mc-technical-list">
                    <div><dt>Profile ID</dt><dd><code>{profile.id}</code></dd></div>
                    <div><dt>Saved-file SHA-256</dt><dd><code title={profile.savedFileSha256 ?? undefined}>{shortHash(profile.savedFileSha256)}</code></dd></div>
                    <div><dt>Saved file</dt><dd><code>{profile.path ?? 'Unavailable'}</code></dd></div>
                    <div><dt>Last used</dt><dd>{formatDateTime(profile.lastUsedAt)}</dd></div>
                  </dl>
                </AdvancedDetails>
              ) : null}
              <Button tone={profile.recommended ? 'primary' : 'secondary'} icon={<Play aria-hidden="true" />} onClick={() => onUseProfile(profile.id)}>
                Use this profile
              </Button>
            </article>
          ))}
        </div>
      )}
    </div>
  )
}
