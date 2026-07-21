import { Activity, ExternalLink, Gauge, HardDrive, Moon, Settings2, Sun, Zap } from 'lucide-react'
import { Button, EmptyState, Notice, SectionHeading, StatusBadge } from '../components'
import type { MissionSettings, SystemPaths, SystemStatus } from '../types'

export function SettingsScreen({
  settings,
  paths,
  system,
  advanced,
  busy,
  onAdvancedChange,
  onThemeChange,
  onPerformanceChange,
}: {
  settings: MissionSettings | null
  paths: SystemPaths
  system: SystemStatus
  advanced: boolean
  busy: boolean
  onAdvancedChange: (advanced: boolean) => void
  onThemeChange: (theme: MissionSettings['theme']) => void
  onPerformanceChange: (enabled: boolean) => void
}) {
  if (!settings) {
    return (
      <div className="mc-page">
        <SectionHeading eyebrow="Local preferences" title="Settings" description="Configure Mission Control on this computer." />
        <EmptyState icon={<Settings2 aria-hidden="true" />} title="Settings are unavailable" description="The local service did not return its saved settings. Try refreshing Mission Control." />
      </div>
    )
  }
  return (
    <div className="mc-page">
      <SectionHeading
        eyebrow="Local preferences"
        title="Settings"
        description="Paths and performance controls are resolved by the local service. Mission Control never executes a path or command supplied by the browser."
      />

      <div className="mc-settings-grid">
        <section className="mc-card mc-settings-card">
          <div className="mc-card__heading"><div><span className="mc-eyebrow">Appearance</span><h2>Interface</h2></div><Sun aria-hidden="true" /></div>
          <fieldset className="mc-segmented-field">
            <legend>Theme</legend>
            <div>
              {(['system', 'light', 'dark'] as const).map((theme) => (
                <label key={theme}>
                  <input type="radio" name="mc-theme" value={theme} checked={settings.theme === theme} onChange={() => onThemeChange(theme)} />
                  {theme === 'dark' ? <Moon aria-hidden="true" /> : <Sun aria-hidden="true" />}{theme}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="mc-toggle-row">
            <span><strong>Show advanced details</strong><small>Reveal hashes, saved paths, raw logs, and performance metrics.</small></span>
            <input type="checkbox" role="switch" checked={advanced} onChange={(event) => onAdvancedChange(event.target.checked)} />
          </label>
        </section>

        <section className="mc-card mc-settings-card">
          <div className="mc-card__heading"><div><span className="mc-eyebrow">Long local renders</span><h2>Performance</h2></div><Zap aria-hidden="true" /></div>
          {!settings.performance.supported ? (
            <Notice tone="warning"><p>Exclusive Performance Mode is not supported by the current local service.</p></Notice>
          ) : (
            <>
              <label className="mc-toggle-row">
                <span><strong>Maximize local render performance</strong><small>Uses Windows High Performance, prevents sleep, and gives Blender higher process priority. Never Realtime priority.</small></span>
                <input type="checkbox" role="switch" checked={settings.performance.enabled} disabled={busy} onChange={(event) => onPerformanceChange(event.target.checked)} />
              </label>
              <dl className="mc-technical-list mc-technical-list--plain">
                <div><dt>Power</dt><dd>{settings.performance.acPower === null ? 'Not reported' : settings.performance.acPower ? 'AC power' : 'Battery power'}</dd></div>
                <div><dt>Current plan</dt><dd>{settings.performance.currentPowerPlan ?? 'Not reported'}</dd></div>
                <div><dt>Previous plan</dt><dd>{settings.performance.previousPowerPlan ?? 'Recorded when enabled'}</dd></div>
                <div><dt>GPU temperature</dt><dd>{settings.performance.gpuTemperatureC === null ? 'Not available' : `${settings.performance.gpuTemperatureC} °C`}</dd></div>
                <div><dt>Restore status</dt><dd>{settings.performance.restoreStatus ?? 'No restore pending'}</dd></div>
              </dl>
            </>
          )}
        </section>

        <section className="mc-card mc-settings-card mc-settings-card--wide">
          <div className="mc-card__heading"><div><span className="mc-eyebrow">Authoritative local paths</span><h2>Render tools & storage</h2></div><HardDrive aria-hidden="true" /></div>
          <dl className="mc-path-list">
            <div><dt>Blender</dt><dd><code>{paths.blenderPath ?? 'Not configured'}</code></dd><StatusBadge tone={system.blenderReady ? 'success' : 'error'}>{system.blenderReady ? 'Found' : 'Missing'}</StatusBadge></div>
            <div><dt>FFmpeg</dt><dd><code>{paths.ffmpegPath ?? 'Not configured'}</code></dd><StatusBadge tone={system.ffmpegReady ? 'success' : 'error'}>{system.ffmpegReady ? 'Found' : 'Missing'}</StatusBadge></div>
            <div><dt>Profiles</dt><dd><code>{paths.profileRoot ?? 'Not configured'}</code></dd></div>
            <div><dt>Default output</dt><dd><code>{paths.outputDefault ?? 'Choose during each render'}</code></dd></div>
            <div><dt>Calibration evidence</dt><dd><code>{paths.calibrationRoot ?? 'Not configured'}</code></dd></div>
            <div><dt>Preferred drive</dt><dd><code>{paths.preferredDrive ?? settings.preferredDrive ?? 'Automatic'}</code></dd></div>
          </dl>
        </section>

        <section className="mc-card mc-settings-card mc-settings-card--wide">
          <div className="mc-card__heading"><div><span className="mc-eyebrow">About this application</span><h2>WZHK Media Mission Control</h2></div><Activity aria-hidden="true" /></div>
          <div className="mc-about-grid">
            <div><span>Service</span><strong>{system.serviceName} {system.version}</strong></div>
            <div><span>Connection</span><strong>{system.ready ? 'Local service ready' : 'Needs attention'}</strong></div>
            <div><span>Instance</span><strong>{system.instanceId ?? 'Not reported'}</strong></div>
            <div><span>Mode</span><strong>{system.capabilities.demoMode ? 'Explicit simulation' : 'Local production'}</strong></div>
          </div>
          <Notice tone="info" title="Need the audio analysis tools?">
            <p>TrackPrompt’s original analysis workspace remains available as a separate local workspace.</p>
            <a className="mc-text-link" href="/?workspace=analysis">Open TrackPrompt analysis workspace <ExternalLink aria-hidden="true" /></a>
          </Notice>
          {advanced ? <Button tone="quiet" icon={<Gauge aria-hidden="true" />} onClick={() => {
            const summary = JSON.stringify({
              service: system.serviceName,
              version: system.version,
              ready: system.ready,
              instanceId: system.instanceId,
              blenderReady: system.blenderReady,
              ffmpegReady: system.ffmpegReady,
              warnings: system.warnings,
            }, null, 2)
            void navigator.clipboard?.writeText(summary)
          }}>Copy diagnostics summary</Button> : null}
        </section>
      </div>
    </div>
  )
}
