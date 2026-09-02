# Andromeda V2 production runbook

This runbook is the operator boundary for the final Trip to Andromeda V2 render
package. It documents inspection, preflight, authorization, start/resume, safe
stop, rollback, and the optional vertical workflow.

The implementation sprint does not authorize or start the full 13,029-frame
render. `Inspect` and `Preflight` are safe planning actions. `StartOrResume` is
a production action and must remain blocked until the immutable technical
release and a separate local operator-start artifact bind the same exact
release and output matrix.

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
| V2 technical authorization | Exact source/package/evidence/output-matrix identities pass objective gates while remaining `productionStartAllowed: false` | Operator consent to spend the time and machine capacity |
| Separate local operator-start artifact | The owner explicitly authorizes this exact package-manifest hash, technical-authorization hash, calibration hash, release identity, and output matrix; the typed phrase includes the package hash | Permission for a changed package, matrix, scene, profile, output folder, or stale technical artifact |
| Scene/profile token | The low-level renderer receives the exact authorized scene/profile pair | Package-level authorization for another release or variant set |

Historical R13.1 review evidence remains immutable and may still record a
Codex-assisted `REVISE` recommendation and pending human-artist field. The
separate owner-attested acceptance records the owner's project decision; it
does not claim Codex supplied human approval and does not rewrite history.

### Current visual release hold

The seven horizontal calibration frames measure render performance but do not
prove visual equivalence to the preserved R13.1 contact sheets. In the
2026-07-23 human visual audit, the V2 frames remained sparse and blockout-like:
Awakening retained floating or isolated elements, and Arrival lacked an
unmistakable destination.

Accordingly, the current package and technical authorization are historical
technical evidence only. They must not be described as proof of R13.1 artistic
equivalence, finish-line sprint completion, or current production readiness.
Do not create or use a separate operator-start artifact while this hold is
open. First produce and review corrected bounded final-quality visual proof.
Any corrective source, scene, profile, or package change requires a new exact
identity, calibration, package manifest, and technical authorization; never
rewrite the existing immutable evidence.

The additive hold is recorded in
`production/andromeda-v2/release-hold.json`. The operator-authorization CLI
requires the record and checks its exact release, package, calibration, and
technical-authorization bindings before inspect, create, or validate, so the
held release fails closed even if a stale local operator artifact exists.
Deleting or editing the hold cannot authorize production.

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
3. Confirm the exact package, scene, generated horizontal and vertical profile,
   technical authorization, and evidence paths exist. A fresh release must not
   fall back to either tracked historical profile. Private audio remains local
   and is represented in committed records only by identity metadata.
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
and active-render safety. `Inspect` does not invoke Blender. The real
`Preflight` invokes the Blender executable only with `--version`; neither action
opens the scene, renders a frame, starts an encode, or mutates production
output.

For a corrected fresh release, pass the generated scene and profile paths
explicitly. The tracked wrapper defaults exist only for historical
compatibility:

```powershell
$freshRoot = ".\test-output\<fresh-proof>"
$freshProfiles = Join-Path $freshRoot "release\profiles"
$freshHorizontalProfile = Join-Path $freshProfiles `
  "andromeda-v2-horizontal-1080p-final-<release-tag>.json"
$freshVerticalProfile = Join-Path $freshProfiles `
  "andromeda-v2-vertical-1080x1920-final-<release-tag>.json"
$freshHorizontalScene = Join-Path $freshRoot `
  "andromeda-v2-master-horizontal.blend"
$freshVerticalScene = Join-Path $freshRoot `
  "andromeda-v2-master-vertical.blend"

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action Inspect `
  -ScenePath $freshHorizontalScene `
  -RenderProfilePath $freshHorizontalProfile

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action Preflight `
  -ScenePath $freshHorizontalScene `
  -RenderProfilePath $freshHorizontalProfile
```

Before the real preflight, the PowerShell 5.1 path harness can prove forwarding
of both exact fresh profile/scene paths through the wrapper without invoking
Blender, rendering, encoding, or creating an operator artifact:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\tools\test-andromeda-production-paths.ps1 `
  -RenderProfilePath $freshHorizontalProfile `
  -VerticalRenderProfilePath $freshVerticalProfile `
  -HorizontalScenePath $freshHorizontalScene `
  -VerticalScenePath $freshVerticalScene
```

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
- remains `productionStartAllowed: false`, with its embedded operator gate
  `not-authorized`, because the operator decision is stored separately;
- has a separately created local operator-start artifact only when the owner
  typed the exact release/matrix-bound confirmation phrase.

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
that pending stop request, exact resume, terminal cancel at a safe chunk
boundary, and exact retry of a retryable failed render. **Cancel render**
requires operator confirmation bound to the saved scene and profile hashes. It
finishes, validates, and publishes the active chunk before entering the
terminal `cancelled` state; it preserves already valid output, job history,
logs, and counters. It is not an output-deletion command. **Retry failed
chunk** is available only for a retryable `failed` job with the exact original
identity and authorization. It rescans the managed output and fills only
missing or invalid work while preserving validated published frames and the
failure/retry history. **Retry current chunk** is separately available for a
watched active chunk (and for its saved retryable failure). After explicit
confirmation it enters `retry_requested`, stops only the isolated in-flight
attempt, preserves every validated prior chunk, and requeues the exact saved
chunk bounds and scene/profile identity. If the chunk became authoritative
before the attempt stopped, Mission Control preserves it instead of deleting
frames to manufacture a retry.

## Optional vertical opt-in

`-EnableVertical` is a fail-closed opt-in in the Andromeda wrapper. It selects
the independently authored vertical scene, profile, output, token, and resume
namespace; it is not a shortcut that adds a second output to the current
horizontal-only authorization.

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
6. a new package manifest binding every artifact for the dual matrix;
7. a new technical authorization binding the vertical identity and exact
   enabled matrix;
8. a new separate local operator-start artifact for that technical release;
9. distinct frame, preview, encode, QA, and resume namespaces.

Until all nine conditions pass, the authorized job remains horizontal-only.
Never silently disable vertical to make an over-budget matrix fit, reuse the
horizontal-only forecast, or generate vertical from the horizontal frames.

Inspection and preflight may exercise both authored variants without
authorizing or starting them:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action Inspect `
  -EnableVertical `
  -ScenePath $freshHorizontalScene `
  -VerticalScenePath $freshVerticalScene `
  -RenderProfilePath $freshHorizontalProfile `
  -VerticalRenderProfilePath $freshVerticalProfile

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action Preflight `
  -EnableVertical `
  -ScenePath $freshHorizontalScene `
  -VerticalScenePath $freshVerticalScene `
  -RenderProfilePath $freshHorizontalProfile `
  -VerticalRenderProfilePath $freshVerticalProfile
```

Creating the dual-matrix operator artifact requires explicit paths to the
separately authored package manifest, calibration, and technical authorization.
The full package is revalidated against the repository before the command
prints the exact release, matrix, and enabled variants. It then writes a new
non-overwriting ignored local artifact only after the owner types the entire
confirmation phrase:

```powershell
$dualCalibration = "<exact dual-matrix calibration path>"
$dualPackageManifest = "<exact dual-matrix package-manifest path>"
$dualTechnicalAuthorization = "<exact dual-matrix technical-authorization path>"

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\new-operator-authorization.ps1 `
  -EnableVertical `
  -CalibrationPath $dualCalibration `
  -PackageManifestPath $dualPackageManifest `
  -TechnicalAuthorizationPath $dualTechnicalAuthorization
```

## Production start

The following is a reference command for a future release that has cleared the
current visual hold, not an instruction to run the present V2 package during
the implementation sprint:

```powershell
# PRODUCTION ACTION: run only after explicit operator confirmation for the
# exact release identity and horizontal-only output matrix.
$sourceAudioPath = "<private source-audio path>"
$sourceCuePath = "<private visual-cues path>"
$freshRelease = Join-Path $freshRoot "release\horizontal-only"
$freshCalibration = Join-Path $freshRelease "v2-calibration.json"
$freshPackageManifest = Join-Path $freshRelease "package-manifest-v2.json"
$freshTechnicalAuthorization = Join-Path (
  $freshRelease
) "technical-authorization-v2.json"
$freshMatrixId = (
  Get-Content -Raw -LiteralPath $freshCalibration | ConvertFrom-Json
).identity.outputMatrix.matrixId
$operatorAuthorizationPath = Join-Path `
  ".\production\andromeda-v2\.operator-authorizations" `
  "$freshMatrixId.operator-start-authorization.json"
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action StartOrResume `
  -ScenePath $freshHorizontalScene `
  -RenderProfilePath $freshHorizontalProfile `
  -CalibrationPath $freshCalibration `
  -PackageManifestPath $freshPackageManifest `
  -TechnicalAuthorizationPath $freshTechnicalAuthorization `
  -SourceAudioPath $sourceAudioPath `
  -SourceCuePath $sourceCuePath `
  -OperatorAuthorizationPath $operatorAuthorizationPath `
  -AuthorizationToken "<exact scene/profile token>"
```

Before execution, compare the technical authorization's release identity, the
separate operator artifact's package-manifest, technical-authorization, and
calibration hashes, enabled variant list, scene/profile hashes, output
directory, frame range, FPS, forecasts, and headroom with the just-completed
preflight. The wrapper locally verifies the full package manifest and every
bound repository artifact, private source-audio
and visual-cues hashes, and the committed creative-acceptance and
encoding-profile hashes. It must fail closed when any value disagrees; private
source paths remain local.

After all dual-matrix gates pass and its separate operator artifact exists, the
same wrapper can start or resume both isolated variants:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action StartOrResume `
  -EnableVertical `
  -ScenePath $freshHorizontalScene `
  -VerticalScenePath $freshVerticalScene `
  -RenderProfilePath $freshHorizontalProfile `
  -VerticalRenderProfilePath $freshVerticalProfile `
  -CalibrationPath $dualCalibration `
  -PackageManifestPath $dualPackageManifest `
  -TechnicalAuthorizationPath $dualTechnicalAuthorization `
  -OperatorAuthorizationPath "<exact dual-matrix operator artifact path>" `
  -SourceAudioPath $sourceAudioPath `
  -SourceCuePath $sourceCuePath `
  -AuthorizationToken "<exact horizontal scene/profile token>" `
  -VerticalAuthorizationToken "<exact vertical scene/profile token>"
```

The current committed package, calibration, and technical authorization are
horizontal-only, so this dual command intentionally fails before any render
unless separately authored matching documents are supplied. Do not bypass the
guard or invoke low-level workers manually.

## Safe stop, resume, and rollback

Use **Request stop after current chunk** for a recoverable pause. The renderer
finishes the active chunk, validates it, publishes it atomically, records the
safe-stop state, and exits before leasing another chunk. **Cancel stop request**
only cancels the pending pause; it does not cancel the render.

Resume uses the same exact `StartOrResume` command and identity. It rescans the
managed output, trusts only verified published frames, and renders missing
contiguous ranges. It must not replace valid frames. Never delete an in-flight
directory, stop marker, lock, manifest, or successful frame to force resume.

Use **Cancel render** only when the active job should end rather than pause.
After explicit confirmation for the exact saved scene/profile identity, the
backend terminates at the next safe chunk boundary and records a terminal
`cancelled` job. Validated output and the persistent audit history remain
intact. Use **Retry failed chunk** only after the backend records a retryable
`failed` state; retry revalidates the same identity and authorization, retains
failure counters, and restarts only the missing or invalid work. Neither
control authorizes deleting prior outputs.

The earlier removal of V1 output was a separate, user-requested manual cleanup.
It was not a side effect of Mission Control cancellation or retry and must not
be used as evidence that these controls delete render assets.

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
