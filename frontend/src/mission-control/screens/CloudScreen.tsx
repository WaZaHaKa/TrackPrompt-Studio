import { Check, Cloud, PackageCheck, RefreshCw, ServerCog, ShieldOff, X } from 'lucide-react'
import { Button, Notice, SectionHeading, StatusBadge } from '../components'
import type { CloudReadiness } from '../types'

export function CloudScreen({
  readiness,
  preparationAvailable,
  busy,
  onRefresh,
  onPreparePackage,
}: {
  readiness: CloudReadiness | null
  preparationAvailable: boolean
  busy: boolean
  onRefresh: () => void
  onPreparePackage: () => void
}) {
  const packageActionsAvailable = preparationAvailable && readiness?.offlinePreparationAvailable === true
  return (
    <div className="mc-page">
      <SectionHeading
        eyebrow="Optional provider workflow"
        title="Cloud rendering"
        description="Prepare a sanitized, provider-neutral package locally. Mission Control never presents unverified provisioning as a running cloud render."
        actions={<Button icon={<RefreshCw aria-hidden="true" />} onClick={onRefresh}>Refresh readiness</Button>}
      />
      {!readiness ? (
        <Notice tone="warning" title="Cloud readiness is unavailable">
          <p>The local service did not report cloud preparation capability. Local rendering remains unaffected.</p>
        </Notice>
      ) : (
        <>
          {readiness.offlinePreparationAvailable && !preparationAvailable ? (
            <Notice tone="info" title="Preparation tooling detected; mutation not connected">
              <p>The backend can inspect offline tooling, but package creation remains disabled until its privacy confirmations and server-managed registry are connected.</p>
            </Notice>
          ) : null}
          <section className="mc-cloud-hero">
            <div className="mc-cloud-hero__icon"><Cloud aria-hidden="true" /></div>
            <div>
              <span className="mc-eyebrow">{readiness.providerName}</span>
              <h2>{packageActionsAvailable
                ? 'Preparation ready'
                : readiness.offlinePreparationAvailable
                  ? 'Preparation detected'
                  : readiness.status === 'unavailable' ? 'Capability unavailable' : 'Setup required'}</h2>
              <p>Offline preparation and validation stay local. Provisioning is only enabled after the backend records a verified provider connection.</p>
            </div>
            <StatusBadge tone={packageActionsAvailable ? 'success' : readiness.status === 'unavailable' ? 'neutral' : 'warning'}>
              {packageActionsAvailable ? 'ready' : readiness.offlinePreparationAvailable ? 'not connected' : readiness.status.replace(/_/g, ' ')}
            </StatusBadge>
          </section>

          <div className="mc-cloud-grid">
            <section className="mc-card">
              <div className="mc-card__heading"><div><span className="mc-eyebrow">{packageActionsAvailable ? 'Available now' : 'Detected locally'}</span><h2>Offline preparation</h2></div><PackageCheck aria-hidden="true" /></div>
              <ul className="mc-capability-list">
                <li className={packageActionsAvailable ? 'is-available' : ''}>{packageActionsAvailable ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}<span>Create sanitized package</span></li>
                <li className={packageActionsAvailable ? 'is-available' : ''}>{packageActionsAvailable ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}<span>Validate package identity</span></li>
                <li className={readiness.cliReady ? 'is-available' : ''}>{readiness.cliReady ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}<span>Inspect Brev CLI readiness</span></li>
                <li className={packageActionsAvailable ? 'is-available' : ''}>{packageActionsAvailable ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}<span>Prepare benchmark plan and offline cost ranking</span></li>
              </ul>
              <p className="mc-muted">Sanitized package: {readiness.sanitizedPackageStatus}</p>
              <Button tone="primary" busy={busy} disabled={!packageActionsAvailable} onClick={onPreparePackage}>Create sanitized package</Button>
            </section>

            <section className="mc-card mc-cloud-disabled">
              <div className="mc-card__heading"><div><span className="mc-eyebrow">Not yet verified</span><h2>Live cloud operations</h2></div><ShieldOff aria-hidden="true" /></div>
              <ul className="mc-capability-list">
                <li className={readiness.liveProvisioningVerified ? 'is-available' : ''}>{readiness.liveProvisioningVerified ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}<span>Live provisioning</span></li>
                <li className={readiness.liveFleetVerified ? 'is-available' : ''}>{readiness.liveFleetVerified ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}<span>Live fleet control</span></li>
                <li className={readiness.automaticTeardownVerified ? 'is-available' : ''}>{readiness.automaticTeardownVerified ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}<span>Automatic teardown</span></li>
                <li className={readiness.cloudEncodeVerified ? 'is-available' : ''}>{readiness.cloudEncodeVerified ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}<span>Cloud encode and download</span></li>
              </ul>
              <Button disabled icon={<ServerCog aria-hidden="true" />}>Start cloud render</Button>
              <small>Disabled until the local service reports a verified end-to-end provider workflow.</small>
            </section>
          </div>

          {readiness.checklist.length > 0 ? (
            <section className="mc-card mc-setup-checklist">
              <div className="mc-card__heading"><div><span className="mc-eyebrow">Setup checklist</span><h2>Before live provisioning</h2></div></div>
              <ol>
                {readiness.checklist.map((item) => (
                  <li key={item.id} className={item.complete ? 'is-complete' : ''}>
                    <span>{item.complete ? <Check aria-hidden="true" /> : null}</span>
                    <div><strong>{item.label}</strong>{item.detail ? <small>{item.detail}</small> : null}</div>
                  </li>
                ))}
              </ol>
            </section>
          ) : null}
        </>
      )}
    </div>
  )
}
