# Andromeda V2 production runbook

This runbook is the operator boundary for the final Trip to Andromeda V2 render
package. It documents inspection, preflight, authorization, start/resume, safe
stop, rollback, and the optional vertical workflow.

The implementation sprint does not authorize or start the full 13,029-frame
render. `Inspect` and `Preflight` are safe planning actions. `StartOrResume` is
a production action and must remain blocked until the exact technical release
and a separate operator start gate both authorize it.

## Fixed output contract

| Variant | State in the default production matrix | Output |
| --- | --- | --- |
| `horizontal-16x9-1080p` | Required and enabled | Authored 1920 × 1080, 30 FPS primary master |
| `vertical-9x16-1080p` | Optional and disabled | Independently authored 1080 × 1920, 30 FPS social output |

Both variants use frames 1 through 13029 and the same exact song clock when
enabled. They do not share a frame directory, camera identity, render profile,
encode manifest, QA result, or preview stream. Vertical is never a crop of the
horizontal master.

The 24-hour gate applies to the exact enabled matrix. The horizontal-only
forecast cannot authorize horizontal plus vertical.

## Authorization model

| Gate | What it proves | What it does not prove |
| --- | --- | --- |
| Owner creative acceptance | R13.1 is the project-level look and motion target | Technical readiness or permission to render |
| V2 technical authorization | Exact source/package/evidence/output-matrix identities pass objective gates | Operator consent to spend the time and machine capacity |
| Operator start gate | The owner explicitly authorizes this exact technical release | Permission for a changed matrix, scene, profile, or output folder |
| Scene/profile token | The low-level renderer receives the exact authorized scene/profile pair | Package-level authorization for another release or variant set |

Historical R13.1 review evidence remains immutable and may still record a
Codex-assisted `REVISE` recommendation and pending human-artist field. The
separate owner-attested acceptance records the owner's project decision; it
does not claim Codex supplied human approval and does not rewrite history.

Any changed byte in a bound scene, profile, plan, look/composition profile,
authorization, or enabled output matrix requires a new identity, forecast, and
authorization. Do not repair an identity mismatch by editing a JSON artifact or
copying a token.

## Before inspection

1. Work from the intended branch and review `git status`; do not reset, clean,
   stash, or overwrite unrelated work.
2. Confirm that no production Blender process or managed render is active.
   Read-only planning is allowed during an active render, but do not restart
   Mission Control, replace its code, clear a mutex, or edit active inputs.
3. Confirm the exact package, scene, horizontal profile, technical
   authorization, and evidence paths exist. Private audio remains local and is
   represented in committed records only by identity metadata.
4. Confirm AC power, sleep risk, temperature, free disk, and the measured
   hardware/software fingerprint. Do not silently change system power settings.
5. Confirm the intended enabled matrix in words. The default is
   `horizontal-16x9-1080p` only.
6. Use a new or identity-compatible managed output directory. Never point a
   different release or variant at an existing frame namespace.

## Read-only inspection and preflight

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action Inspect

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action Preflight
```

`Inspect` uses the canonical dry-run path. `Preflight` verifies the exact scene
and profile, Blender/tool availability, managed-output compatibility, storage,
and active-render safety. Neither action may start Blender or create production
frames.

A passing low-level preflight is necessary but not sufficient. Before a start,
also verify that the V2 technical authorization:

- has `technicalReady: true`;
- binds the exact source revision and artifact hashes;
- binds only the intended enabled output variants;
- binds the scene, composition, and render profile for every enabled variant;
- records complete dependency, deterministic-effect, disk/VRAM, dashboard,
  animatic, transition, exposure/readability, calibration, encode, and QA
  evidence;
- has aggregate P90 at or below 24 hours for the exact matrix;
- retains at least 25% disk safety headroom;
- records `productionStartAllowed: true` only when the separate operator gate
  is also authorized for the same release identity.

If any value is missing, stale, different, or indeterminate, stop. Do not
convert a warning into an approval by hand.

## Local dashboard

Launch or reopen the loopback application with:

```powershell
.\WZHK-Media-Launcher.cmd
```

Do not restart an active backend merely to load a new UI build. Wait for a safe
pause or use isolated test ports for bounded deployment proof.

For a running matrix, Mission Control must show the enabled variants, the real
latest completed frame for the selected variant, the exact full-resolution
frame link, current frame, latest rendered/in-flight frame, latest safe frame,
act, shot, song timestamp, active workers, chunk, retry/failure counts,
resource telemetry, stage progress, per-variant P50/P90 ETA, and aggregate ETA.

The output-variant selector changes the visible stream only. It does not enable
vertical or alter the authorized job. Browser refresh/reconnect must restore
persistent state and must not start a second renderer.

The current backend supports safe stop after the active chunk, cancellation of
that pending stop request, and exact resume. It does not expose destructive
cancel-render or targeted failed-chunk retry endpoints; those controls remain
disabled and must not be represented as available.

## Optional vertical opt-in

`-EnableVertical` is intentionally a fail-closed guard in the current Andromeda
wrapper. It is not a shortcut that adds a second output to an existing
authorization.

Before the optional vertical variant can be enabled in the same job or a
separately authorized job, all of the following must exist for the final
vertical identity:

1. an independently authored scene/composition/camera and render profile;
2. separate framing, safe-zone, landmark, occupancy, mobile-readability, motion,
   and encode QA;
3. representative final-resolution calibration across every act and expensive
   effect class;
4. a vertical P50/P90 forecast plus a recalculated aggregate selected-matrix
   forecast;
5. storage and worker scheduling that include the additional 13,029-frame
   stream;
6. a new technical authorization binding the vertical identity and exact
   enabled matrix;
7. a new explicit operator start gate for that technical release;
8. distinct frame, preview, encode, QA, and resume namespaces.

Until all eight conditions pass, the authorized job remains horizontal-only.
Never silently disable vertical to make an over-budget matrix fit, reuse the
horizontal-only forecast, or generate vertical from the horizontal frames.

## Production start

The following is a reference command, not an instruction to run it during the
implementation sprint:

```powershell
# PRODUCTION ACTION: run only after explicit operator confirmation for the
# exact release identity and output matrix.
$sourceAudioPath = "<private source-audio path>"
$sourceCuePath = "<private visual-cues path>"
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action StartOrResume `
  -SourceAudioPath $sourceAudioPath `
  -SourceCuePath $sourceCuePath `
  -AuthorizationToken "<exact scene/profile token>"
```

Before execution, compare the technical authorization's release identity,
operator-start identity, enabled variant list, scene/profile hashes, output
directory, frame range, FPS, forecasts, and headroom with the just-completed
preflight. The wrapper locally verifies the private source-audio and visual-cues
hashes plus the committed creative-acceptance and encoding-profile hashes. It
must fail closed when any value disagrees; private source paths remain local.

There is no authorized horizontal-plus-vertical launch command while
`-EnableVertical` remains guarded. Do not bypass that guard or invoke low-level
workers manually.

## Safe stop, resume, and rollback

Use **Request stop after current chunk** for a recoverable pause. The renderer
finishes the active chunk, validates it, publishes it atomically, records the
safe-stop state, and exits before leasing another chunk. **Cancel stop request**
only cancels the pending pause; it does not cancel the render.

Resume uses the same exact `StartOrResume` command and identity. It rescans the
managed output, trusts only verified published frames, and renders missing
contiguous ranges. It must not replace valid frames. Never delete an in-flight
directory, stop marker, lock, manifest, or successful frame to force resume.

Rollback depends on the failure point:

- **Before production start:** keep the gate blocked and select or build a
  different immutable package. No output cleanup is required.
- **After an identity change:** create a new package and authorization. Preserve
  the earlier output as evidence; do not mix or migrate its frames into the new
  namespace.
- **After a worker/application interruption:** leave published frames in place,
  inspect the persisted job and managed manifest, correct the external cause,
  then exact-resume.
- **After a dashboard deployment problem:** do not restart or replace an active
  render service. Use a separate port for diagnosis or wait for a safe pause,
  then deploy the last verified application revision through normal source
  control. Do not use destructive Git commands in a dirty checkout.
- **After disk, dependency, VRAM, or deterministic-effect failure:** safe-stop
  if possible, preserve all artifacts, invalidate readiness, and recalibrate.
  Do not lower quality, remove an enabled variant, or rewrite authorization
  silently.
- **After a bad or incomplete encode:** preserve the complete validated frame
  sequence. Diagnose and rerun encoding into a new partial/final namespace;
  never rerender frames merely to repair FFmpeg output.

An earlier package may be selected only with its own compatible runtime and
authorization. Rollback never carries forward a newer authorization token.

## Encoding and final QA

Encoding starts only after every expected frame for that variant validates.
Each enabled variant is encoded separately from its lossless sequence with
exact source-synchronized audio. FFmpeg writes to a temporary name, reports
machine-readable progress, is ffprobe-verified, and publishes atomically.

Final QA must confirm exact frame count, dimensions, FPS, codec/pixel format,
audio stream and duration, A/V sync, color metadata, variant identity, and no
temporary residue. Technical media QA never manufactures human artistic
approval.

## End-of-sprint handoff

The handoff must record:

- branch and exact source revision;
- exact enabled output matrix;
- scene, profile, package, calibration, authorization, and evidence hashes;
- measured per-stage and per-variant P50/P90 plus aggregate forecast;
- disk and VRAM headroom;
- dashboard and latest-frame proof;
- checks actually run and any skips;
- operator start and resume reference commands;
- push status and any authentication blocker;
- an explicit statement that the full production render was not started.

The presence of this runbook, a successful build, local dashboard proof, or a
technical authorization does not itself cross the production-start boundary.
