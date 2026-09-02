# Andromeda newest-release production helper

## Purpose

`tools/Invoke-AndromedaLatestProduction.ps1` locates the newest **coherent**
Andromeda V2 release bundle, launches WZHK Media Mission Control, runs the
canonical read-only inspection and preflight, and—only after all existing
operator gates pass—starts or resumes the render.

The script is deliberately fail-closed. It never turns “newest file” into
“approved release.” Modification time orders candidates only after the release
manifest has resolved exact role-bound, hash-valid artifacts. It requires one
complete bundle containing:

- `package-manifest-v2.json`;
- `v2-calibration.json`;
- `technical-authorization-v2.json`;
- `evidence/release-report.json`;
- the exact horizontal final scene and scene-bound profile;
- human visual-QA approval and hold-closure records;
- private source-audio and visual-cue identities;
- a technical authorization that explicitly reports `technicalReady: true`;
- an enabled output matrix whose exact aggregate total P90 is present and no
  more than 24 hours.

The repository’s own `invoke-production.ps1` remains authoritative. This helper
cannot bypass a release hold, stale hash, missing evidence, insufficient disk,
wrong source audio/cues, mismatched scene/profile token, GPU mutex, missing
operator authorization, or a horizontal-only release when vertical is
requested.

## Install

Extract the ZIP into the repository root so the paths become:

```text
TrackPrompt-Studio/
├── tools/Invoke-AndromedaLatestProduction.ps1
├── docs/andromeda-latest-production-runner.md
├── RUN-ANDROMEDA-PREFLIGHT.cmd
└── RUN-ANDROMEDA-START-AND-ENCODE.cmd
```

The files do not contain credentials, media, generated frames, or authorization
artifacts.

## Safe first run

From Windows PowerShell:

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\Invoke-AndromedaLatestProduction.ps1 `
  -Mode Discover
```

This writes an ignored local discovery report below. It records source
identities and whether they were resolved, but deliberately does not persist
the private physical audio/cue paths:

```text
.trackprompt-data/andromeda-latest-production-runner/<timestamp>/
```

It does not launch Blender, render, or encode.

## Preflight and open the UI

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\Invoke-AndromedaLatestProduction.ps1 `
  -Mode Preflight
```

This performs:

1. newest coherent release discovery;
2. Mission Control launcher validation;
3. Mission Control launch/reopen;
4. exact scene/profile path harness when available;
5. canonical `Inspect`;
6. canonical `Preflight`.

No production render or encode starts in this mode.

## Render and encode the horizontal master

The guarded double-click entry point is:

```text
RUN-ANDROMEDA-START-AND-ENCODE.cmd
```

The equivalent PowerShell command is:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\Invoke-AndromedaLatestProduction.ps1 `
  -Mode StartAndEncode
```

The helper attempts to locate private source audio and `visual-cues.json` by
the exact SHA-256 and byte length recorded in the selected release. It checks
the known *Trip to Andromeda* audio location first, then the selected proof,
`test-output`, `.trackprompt-data`, and any `-SourceSearchRoot` values. When no
match is found, it asks for an explicit path and verifies it before proceeding.

Explicit paths can be supplied:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\Invoke-AndromedaLatestProduction.ps1 `
  -Mode StartAndEncode `
  -SourceAudioPath "C:\Users\theon\OneDrive\Desktop\Gratis Project\DJ WaZaHaKa - Trip to Andromeda.wav" `
  -SourceCuePath "C:\path\to\visual-cues.json"
```

Before rendering, two separate human boundaries remain:

1. `new-operator-authorization.ps1` requires the release/matrix-bound phrase
   when no valid operator artifact exists;
2. the helper asks for a final package/matrix confirmation immediately before
   `StartOrResume`.

These are intentional, not obstacles to remove.

## Watching real render progress

Mission Control is opened before production starts. Its Live view is the
primary progress display and should show:

- exact enabled output variant;
- current frame;
- newest completed rendered/in-flight frame;
- newest validated safe frame;
- actual completed-frame preview;
- act, shot, and song timestamp;
- active worker/chunk, retries, and failures;
- per-stage and per-variant progress;
- P50/P90 ETA;
- aggregate ETA for the enabled matrix.

Closing or refreshing the browser does not restart the backend job. Re-running
`WZHK-Media-Launcher.cmd` should reopen the healthy instance.

## Encoding through Mission Control

After the exact frame count is validated, the helper reopens Mission Control
and asks the operator to choose:

```text
Encode → Encode delivery + master
```

That single UI action is deliberately retained. The published operator
contract requires explicit encode confirmation, while no stable public
“auto-click Encode” endpoint is documented. The helper therefore does not
invent one or call the standalone encoder behind Mission Control’s back.

After the UI confirmation, leave the PowerShell window running. No additional
terminal response is required: the helper detects managed encode activity from
the output state and then waits for:

- the H.264/AAC delivery MP4 when enabled;
- the ProRes/PCM master MOV when enabled;
- the required encode manifest(s);
- a completed QA JSON;
- no final filename marked partial or temporary.

Detailed FFmpeg rate, speed, encoded frame count, and ETA remain visible in the
UI during that wait.

## Optional vertical output

Vertical is not generated from horizontal frames. It is an independently
authored, calibrated, packaged, and authorized output variant.

Use:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\Invoke-AndromedaLatestProduction.ps1 `
  -Mode StartAndEncode `
  -EnableVertical
```

The command fails before either variant starts unless the newest release
itself enables both:

```text
horizontal-16x9-1080p
vertical-9x16-1080p
```

and supplies matching scenes, profiles, aggregate forecast, technical
authorization, operator artifact, and both low-level tokens. It never silently
falls back to horizontal-only after vertical was requested.

## Modes

| Mode | Effect |
|---|---|
| `Discover` | Select and report the newest coherent bundle; no UI/preflight/render/encode. |
| `Preflight` | Open Mission Control and run canonical Inspect + Preflight only. |
| `Start` | Authorize, start/resume, and wait for all validated frames; no encode handoff. |
| `StartAndEncode` | Render, open the UI for its required encode confirmation, detect managed encode activity, then wait for media + QA completion. |

## Important safety behavior

- The newest complete but held/blocked release stops the run by default.
- `-AllowOlderCompatibleRelease` is available for deliberate diagnostics, but
  every candidate still passes current Inspect/Preflight and authorization.
- No file is selected solely by modification time; release roles, hashes,
  source bindings, matrix, and canonical wrapper checks are required.
- Existing published frames are never deleted or overwritten by this helper.
- No mutex, stop marker, in-flight directory, manifest, profile, hold, or
  authorization is edited to force progress.
- A failed render does not trigger encoding.
- A bad encode does not trigger rerendering.
- Horizontal and vertical keep separate frame, preview, encode, QA, and resume
  namespaces.
- The helper does not provision cloud resources or contact a provider.

## Current release-hold note

A historical Andromeda package was explicitly held because its horizontal
full-track evidence did not yet prove the R13.1 visual level across all acts.
This helper intentionally rejects any bundle still bound by that hold. A newer
properly finalized bundle can supersede the held identity only through its
human visual approval, human closure, fresh calibration, package, and technical
authorization. The helper never deletes or edits `release-hold.json`.
