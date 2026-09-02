# Mission Control React interface

WZHK Media Mission Control is the primary local render experience. It is a
React control plane over the existing PowerShell, Python, Blender, and FFmpeg
production tooling; it does not reimplement render safety in the browser.

## Product boundary

- The browser connects only to the loopback backend that served the page.
- Saved profile JSON remains the source of truth.
- Authorization remains bound to the exact saved-file and scene SHA-256 values.
- Production preflight, the GPU mutex, resumable chunks, atomic publication,
  validation, safe stop, encoding, and local audio mux remain backend-owned.
  The React adapter starts the reviewed local encoder through the backend and
  only displays persisted backend progress.
- Closing or refreshing the browser does not stop a render.
- Cloud pages report offline preparation honestly and do not expose an
  unverified provisioning action.

The original PowerShell interface remains available through
`WZHK-Media-Launcher-Legacy.cmd` during the migration.

## Navigation

The application has eight primary destinations:

| Section | Purpose |
| --- | --- |
| Home | Readiness, recommended profile, current work, time, storage, and one next action |
| Render | Guided setup, authorization, start, and live progress |
| Profiles | Discovered saved profiles with calibration and authorization status |
| Calibration | Measured machine evidence, finalist comparison, and offline plan creation; run/review adapters are explicitly unavailable |
| Jobs | Persistent running, stopped, resumable, failed, and complete jobs |
| Encode | Verified sequence, Delivery-then-Master encode, private-audio mux, verification, and live progress |
| Cloud | Offline readiness inspection and explicit unavailable package/live actions |
| Settings | Local tool paths, theme, output defaults, diagnostics, and reversible performance mode |

Simple mode is the default. Exact hashes, paths, authorization token, process
identity, raw logs, and detailed telemetry are grouped under **Advanced
details**. The choice is stored in the browser and never changes backend safety.

## Guided render flow

The Render workspace uses six steps:

1. Select the approved project and scene.
2. Select a saved profile, with the calibrated 720p profile recommended first.
3. Browse for an output folder using the native backend bridge. Mission Control
   classifies every entry before offering a new render, a compatible resume, or
   a unique child folder.
4. Run authoritative preflight and review plain-language checks.
5. When needed, authorize inline through both required confirmations. The
   backend creates the exact sibling authorization record atomically.
6. Start the exact saved configuration, or run an inspection-only dry run.

An unauthorized but otherwise valid profile is a recoverable state. The UI
offers **Authorize now** in place and continues directly to Start after success.

The current React production boundary includes verified frame-sequence encode
and local private-audio mux. Calibration candidate execution/review and cloud
package mutation remain unavailable rather than calling unverified adapters.

## Visual and accessibility system

The interface uses the existing React, TypeScript, Vite, and Lucide stack. Its
styles are component-scoped under the Mission Control shell and use neutral
surfaces with one restrained cyan/violet WZHK accent. Layouts collapse for
narrow windows and remain compact at 1366x768 while using available space at
1080p and 1440p.

Native controls, visible focus rings, a skip link, labelled landmarks, live
status regions, keyboard-operable dialogs, meaningful empty states, and
reduced-motion styles are part of the interface contract. Color is never the
only representation of ready, warning, or failed states.

## Development entry points

The production launcher rebuilds the frontend when a content fingerprint of
root build files, `src/`, and `public/` differs from the local ignored build.
This detects edits, additions, and deletions without relying on filesystem
timestamps. It then starts the loopback backend, waits for Mission Control
health, and opens the resulting URL. The ordinary analysis workspace is
retained at `/?workspace=analysis`.

For frontend development, run the repository's normal Vite command and point
its `/api` proxy at a Mission Control-enabled backend. Generated builds,
instance descriptors, logs, test evidence, profiles, scenes, audio, frames, and
videos remain ignored local state.
