# Render calibration, performance, and remote workers

This workflow optimizes the frozen Space Journey scene without changing its artistic contract. The approved `.blend`, its SHA-256, and an exact saved profile JSON remain the authorities. A calibration result is evidence, not permission to render; a profile edit changes its file hash and invalidates any prior authorization.

## Active-render boundary

`CALIBRATE THIS PC` first checks Blender processes, the production renderer, `Local\TrackPromptFinalRenderGpu`, and `.inflight-*` directories. If a production render is active, planning and read-only log inspection are allowed, but all Blender benchmarks are blocked. Never kill Blender, remove the mutex, delete an in-flight directory, or edit an active scene, profile, or output.

`REQUEST STOP AFTER CURRENT CHUNK` writes an identity-bound marker beneath the selected managed output. A renderer started with the updated script reads it only before a new chunk or after the current chunk validates and publishes. It then records `stopped-after-current-chunk-by-operator` and exits. `CANCEL STOP REQUEST` removes only that marker after confirmation. A renderer process that began before this feature was installed cannot observe the new marker; do not use the action for that process.

## Local calibration

Calibration evidence is local and ignored by Git:

```text
test-output\render-calibration\<machine-id>\<scene-sha12>\<calibration-id>\
```

The plan binds the machine, GPU and driver, CPU and RAM, Blender build, exact scene and source-profile hashes, output drive/filesystem, candidate settings, six representative stills, and the two required 30-frame ranges. A candidate render reuses the production profile application path and is bounded to at most 120 frames. Candidate reports separate cold and warm timing, mean/median/p90, image-write timing, frame sizes, checksums, and the pending human visual gate.

Use staged elimination. Review representative frames and both consecutive sequences for hero readability, rails and stars, temporal stability, glow, gradients/banding, AgX color, composition, and clipping. Mark each candidate `PASS`, `PASS WITH DOCUMENTED CAVEAT`, or `FAIL`. Finalization rejects failures, chooses highest throughput, and when candidates are within 5 percent prefers visual quality, lower storage, then simpler recovery.

Only finalization of measured, reviewed evidence may create:

```text
render-profiles\trip-to-andromeda\trip-to-andromeda-720p-hyper-optimized.json
render-profiles\trip-to-andromeda\trip-to-andromeda-calibrated-recommended.json
render-profiles\trip-to-andromeda\trip-to-andromeda-1080p-recommended-calibrated.json
render-profiles\trip-to-andromeda\trip-to-andromeda-1440p-balanced-calibrated.json
render-profiles\trip-to-andromeda\trip-to-andromeda-4k-balanced-optimized.json
render-profiles\trip-to-andromeda\recommended-profile.json
```

No file in that list is generated from guessed performance. The 720p profile must be native 1280x720, 30 fps, frames 1-13029, square-pixel RGB; an upscale is never labeled native 4K. The original Ultra profile is not edited.

Adaptive chunk size targets a configurable duration and uses warm median seconds/frame, clamped to 15-600 frames and aligned to a practical multiple. The profile records the resolved frame count, predicted duration, chunk count, maximum unpublished work, and rationale.

## Dashboard and output folders

The watcher derives speed and ETA from live, completed frame timestamps. It reports rolling mean, median, p90, expected and conservative remaining time, cold/calibrating state, and confidence. Storage is separated into published logical bytes, in-flight logical bytes, support/checkpoint/log bytes, actual output-directory bytes, free space, and profile reserves. After 20 frames the primary live projection uses actual mean/median/p90 sizes and is labeled with confidence. Hard-linked publication is counted once in the physical-output total.

Output selection uses `Get-ChildItem -Force`. A truly empty directory is accepted. A compatible initialized directory may resume only when scene/profile hashes, resolution, FPS, frame range, format, bit depth, and published subdirectory agree. Incompatible or unrelated entries are listed exactly. `CREATE A NEW UNIQUE RENDER SUBFOLDER HERE` creates a collision-safe timestamped child and never removes hidden files.

## Exclusive performance mode

This mode is explicit and reversible. It records the active Windows power scheme before optionally selecting High Performance, raises only Blender to `High` or `AboveNormal` (never `Realtime`), records GPU telemetry, and restores the prior scheme on exit. It does not terminate unrelated applications and keeps one GPU render process by default. A critical thermal state requests a safe chunk-boundary stop; it does not interrupt an in-flight chunk.

Performance mode is not considered beneficial until a bounded A/B calibration shows equal or better validated frames/hour. CPU validation concurrency, service pauses, double buffering, scratch/publish drive separation, and scene/compositor changes remain disabled by default unless separately benchmarked and shown recoverable.

## Provider-neutral remote rendering

No tool uploads, purchases, or contacts a provider. Export requires explicit privacy and scene-disclosure confirmations. The package is written under:

```text
render-packages\trip-to-andromeda\<scene-sha12>\<profile-sha12>\package\
```

The Blender sanitizer creates a copy, verifies baked `TP_AUDIO_BUS` animation, removes sounds, sequencer audio, local audio paths, and private properties, and preserves the original frozen scene. The package contains the sanitized scene, exact safe profile JSON, relative worker scripts, hashes, Blender/color/frame contracts, non-overlapping chunk assignments, and validation metadata. It excludes authorization secrets, private audio, lyrics/transcripts, cue and analysis JSON, prompts, model paths, credentials, and local output paths.

The Windows-first worker verifies package, scene, profile, Blender version, and assigned range; renders only that range; writes six-digit frames, per-frame SHA-256, and worker/GPU metadata; and never encodes or needs the private WAV. Returned files enter quarantine. The importer checks identity, range/count, dimensions, format/bit depth/color mode, integrity, checksums, duplicates, gaps, and unexpected files before atomic publication. It does not overwrite a valid local frame. Final audio muxing and completeness/visual QA remain local.

## Operator sequences

Calibrated local profile:

```text
WZHK-Media-Launcher.cmd
-> CALIBRATE THIS PC
-> RUN CANDIDATE (repeat staged finalists)
-> REVIEW CANDIDATE
-> GENERATE RECOMMENDED PROFILE
-> LOAD / EDIT SAVED PROFILE -> REVIEW
-> AUTHORIZE
-> FINAL RENDER PREFLIGHT
-> DRY-RUN / RESUME PLAN
-> RENDER WITH SAVED PROFILE -> START / RESUME RENDER
```

Native 720p:

```text
WZHK-Media-Launcher.cmd
-> CALIBRATE THIS PC
-> run and review native 720p candidates
-> GENERATE 720P HYPER PROFILE
-> LOAD / EDIT SAVED PROFILE -> REVIEW
-> AUTHORIZE
-> FINAL RENDER PREFLIGHT
-> DRY-RUN / RESUME PLAN
-> RENDER WITH SAVED PROFILE -> START / RESUME RENDER
```

Remote package:

```text
WZHK-Media-Launcher.cmd
-> OUTSOURCE / REMOTE RENDER
-> CREATE REMOTE RENDER PACKAGE
-> acknowledge privacy and scene-design disclosure
-> VALIDATE REMOTE PACKAGE
-> GENERATE CHUNK DISTRIBUTION
-> ESTIMATE OUTSOURCE TIME / COST
-> manually transfer the package after separate operator authorization
-> IMPORT RETURNED FRAMES
-> VERIFY RETURNED FRAMES
-> compare selected frames visually
-> MERGE LOCAL AND REMOTE FRAMES
-> RESUME MISSING FRAMES LOCALLY
-> run normal completeness verification and local encoding
```

## Commands

Plan calibration (safe while a render is active; it records a blocked status):

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\calibrate-trackprompt-render.ps1 `
  -Mode Plan `
  -ApprovedScenePath <approved.blend> `
  -BaseProfilePath <saved-profile.json>
```

Remote package operations:

```powershell
.\tools\export-trackprompt-render-package.ps1 -ApprovedScenePath <approved.blend> -RenderProfilePath <profile.json> -PrivacyConfirmed -AcknowledgeSceneDisclosure
.\tools\validate-trackprompt-render-package.ps1 -PackageDirectory <package>
.\render-trackprompt-worker.ps1 -PackageDirectory <package> -BlenderExecutable <blender.exe> -ChunkId <chunk-id> -OutputDirectory <empty-worker-output>
.\tools\import-trackprompt-remote-frames.ps1 -PackageDirectory <package> -ReturnDirectory <worker-return> -LocalProfilePath <profile.json> -LocalScenePath <approved.blend> -OutputDirectory <managed-output> -OperatorConfirmed
```
