# WZHK Media Mission Control

Mission Control is TrackPrompt Studio's modern local production-render
interface. It keeps the validated PowerShell/Python/Blender engine authoritative
while presenting profiles, preflight, authorization, progress, safe stop, and
resume as a guided React workflow.

## Launch

Double-click:

```text
WZHK-Media-Launcher.cmd
```

The launcher:

1. serializes concurrent launcher attempts;
2. reopens a healthy existing Mission Control instance;
3. rebuilds the ignored local React bundle when its content fingerprint changes,
   including source deletion, root configuration, and public assets;
4. selects port 8765 or the next available loopback port;
5. starts the backend independently of the browser;
6. waits for `/api/mission-control/health`;
7. opens the correct local URL;
8. shows a native Windows error only when startup genuinely fails.

Runtime descriptors and logs stay under the ignored
`.trackprompt-data\mission-control\` directory. The API binds to `127.0.0.1`
and is not intended for LAN or public exposure.

Validate startup without opening the browser or starting Blender:

```powershell
.\WZHK-Media-Launcher.cmd -ValidateOnly
```

Validation executes each Python candidate until one can import the Mission
Control server and print its CLI help without starting it. It also requires
either an exact current React build fingerprint or a discoverable npm command
that can be used by normal startup to rebuild the interface. It does not run
the frontend build itself.

## Normal workflow

```text
Double-click WZHK-Media-Launcher.cmd
-> START A NEW RENDER
-> choose the recommended saved profile
-> browse for an output folder
-> review preflight
-> AUTHORIZE NOW when required
-> complete both confirmations
-> START RENDER
-> follow reconnectable live progress
-> OPEN OUTPUT
```

After a complete sequence, **Encode video** opens an honest readiness view. The
React encode/mux adapter is not connected in this increment, so the production
encode action stays disabled and the legacy interface remains the path for the
reviewed encoder. Calibration candidate run/review and cloud package mutations
have the same explicit unavailable boundary.

Simple mode hides file hashes, local paths, JSON, authorization token, process
identity, and raw logs. Advanced details makes those available without changing
the underlying safety rules.

## Preserved render contract

The React interface does not replace or weaken:

- exact frozen scene and saved-file profile SHA-256 binding;
- separate two-confirmation authorization records;
- renderer preflight and storage policy;
- one-GPU mutex and one full Blender process by default;
- resumable calibrated chunks and in-flight checkpoints;
- validation and atomic frame publication;
- no-overwrite policy for valid frames;
- identity-bound stop after the current chunk;
- exact frame-sequence encoding and local private-audio mux;
- calibration evidence and reversible Exclusive Performance Mode;
- provider-neutral/offline cloud preparation boundaries.

An unauthorized valid profile is presented as **Authorization required** with
an inline **Authorize now** action. The backend writes the exact sibling record
only after both confirmations; the user never types the token.

## Primary sections

- **Home:** readiness, current work, recommendation, time, and storage.
- **Render:** guided setup and live progress.
- **Profiles:** discovered saved JSON profiles and authorization state.
- **Calibration:** measured evidence and bounded offline plan creation; candidate run/review unavailable in React.
- **Jobs:** persisted history, safe stop, and exact resume.
- **Encode:** verified sequence/readiness view; production encode/mux disabled until its adapter is connected.
- **Cloud:** honest offline readiness; package and live actions disabled until connected and verified.
- **Settings:** local paths, diagnostics, theme, and reversible performance mode.

## Legacy fallback

The former keypad-driven PowerShell interface remains available at:

```text
WZHK-Media-Launcher-Legacy.cmd
```

It is a temporary fallback, not the preferred workflow. Its underlying modules
remain in `tools\wzhk-launcher\`, and `wzhk-media-control-center.ps1
-ValidateOnly` continues to validate those modules and the render engine.

## Tests

The implementation has separate backend state/event/authorization tests,
React workflow tests, launcher tests, and a fake-renderer Playwright flow. The
launcher suite covers unusable-Python fallback and content-addressed rebuild
invalidation for source deletion, root entry files, and public assets. The fake
renderer covers heartbeat, atomic preview publication, safe stop, browser
reload, exact resume, and completion without launching a complete Blender
timeline; the browser test also verifies that unsupported encode is labelled
and disabled honestly.

Relevant commands:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\test-wzhk-react-launcher.ps1

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\test-wzhk-mission-control.ps1

cd backend
.\.venv\Scripts\python.exe -m pytest

cd ..\frontend
npm test -- --run
npm run test:e2e
```

Automated workflows do not provision Brev, contact billable services, or start
the full production frame range.

## Documentation

- [React UI architecture](docs/mission-control-react-ui.md)
- [User guide](docs/mission-control-user-guide.md)
- [Real-time events](docs/mission-control-realtime-events.md)
- [Troubleshooting](docs/mission-control-troubleshooting.md)
- [Render profiles](docs/render-profiles.md)
- [Calibration results](docs/render-calibration-results-20260720.md)
- [Local performance mode](docs/local-performance-mode.md)
- [NVIDIA Brev boundary](docs/nvidia-brev-rendering.md)
