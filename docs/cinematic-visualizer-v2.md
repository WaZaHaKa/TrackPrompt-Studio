# Cinematic Visualizer V2

## Status and safety boundary

`space-journey-story` is an additive, deterministic, local-only cinematic preset. It is preview-only. It does not inherit the calibration, scene hash, authorization token, or production approval of `space-journey` V1. A V2 production render remains blocked until a separately frozen V2 scene/profile pair is calibrated, reviewed, and explicitly authorized.

The V1 identifiers and defaults remain unchanged:

- default and legacy preset: `abstract-geometry`;
- existing cinematic preset: `space-journey`;
- story preset: `space-journey-story`;
- V2 scene output: `trackprompt-space-journey-story.blend`;
- V2 bounded preview: `space-journey-story-preview.mp4`.

No V2 component sends audio, analysis, images, plans, or reviews off the machine.

## Story and shot contracts

The backend compiles two versioned artifacts from the local `TrackPromptVisualCueSheet` and resolved V2 configuration:

- `StoryPlan` schema `1.0.0`: exactly seven contiguous acts—Signal, Awakening, Departure, Gates, Rupture, Transformation, Arrival;
- `ShotPlan` schema `1.0.0`: contiguous, non-overlapping shots covering the same frame range, FPS, seed, and input digest.

Every shot declares its story purpose, protagonist state, narrative environment, planned camera rig, composition, lighting, bounded motion profile, reactive micro-layers, transition type, and representative review frames. Privacy validation rejects source filenames, physical paths, lyrics, transcripts, credentials, model paths, and other private source fields from cinematic artifacts.

Local API:

```text
POST /api/analyses/{job_id}/cinematic/plan
GET  /api/analyses/{job_id}/cinematic/plan
GET  /api/analyses/{job_id}/cinematic/story-plan/export
GET  /api/analyses/{job_id}/cinematic/shot-plan/export
GET  /api/analyses/{job_id}/cinematic/reviews
PUT  /api/analyses/{job_id}/cinematic/reviews/{shot_id}
```

The canonical `run-trackprompt-to-blender.ps1` runner detects these routes for V2, writes `story-plan.json` and `shot-plan.json`, and passes the shot plan to `blender/build_visualizer.py --shot-plan`. V1 runs do not require or accept a shot plan.

## Motion and camera rules

Continuous audio controls follow a bounded pipeline:

```text
robust percentile normalization
→ deadband
→ asymmetric attack/release envelope
→ low-pass smoothing
→ response curve
→ clamp to 0..1
```

The camera hierarchy separates authored intent from audio response:

- `TP_STORY_CAMERA_ROOT`: planned shot-to-shot position and composition;
- target/aim object: planned focal point;
- `TP_STORY_CAMERA_MICRO`: bounded micro audio response only.

Raw audio never drives the major camera root. Declared cuts are the only intentional discontinuities. Timeline health checks inspect velocity, angular velocity, acceleration, overshoot, lens bounds, undeclared jumps, and raw-audio major-camera connections. Blender 5.2 layered actions are traversed through the shared action-FCurve compatibility helper.

## Blender MCP authoring workflow

The MCP entrypoints in `blender.trackprompt_visualizer.mcp_entrypoints` provide stable high-level operations:

```text
build_story_scene
load_story_revision
build_shot
set_review_shot
set_review_frame
apply_shot_revision
validate_current_shot
capture_review_state
save_revision_snapshot
render_preview_stills
render_preview_clip
```

Recommended loop:

```text
edit source/config
→ compile deterministic plans
→ call build_story_scene or apply_shot_revision
→ select a representative shot/frame
→ inspect the Blender viewport or bounded still
→ validate_current_shot
→ store an approve/revise review
→ save a new ignored .blend revision
```

MCP entrypoints validate every input before the explicit scene-clear/build boundary. They never initiate the production renderer. Scene reset removes prior objects, collections, sound strips, and sound datablocks so repeated bounded builds do not accumulate private audio strips.

For `space-journey-story`, `render_preview_clip(output_path, ffmpeg_path=...)` consumes the six declared non-contiguous `reviewSegments` itself. It renders only those source frames, assembles the matching six local audio excerpts, and atomically publishes the bounded H.264/AAC edit through an argument-array FFmpeg call. The runner does not fall back to a contiguous or unrelated clip for this preset.

## Renderer telemetry

The production Blender chunk worker emits bounded JSON on one exact stdout prefix:

```text
WZHK_RENDER_EVENT {"schemaVersion":"1.0.0","eventType":"frame_started",...}
```

Allowed event types are `frame_started`, `frame_written`, `render_stats`, `chunk_complete`, and `render_cancelled`. Applicable fields include local event sequence, Mission Control job ID, worker/process identity, chunk ID/start/end, frame, elapsed seconds, path-free output identity, act/shot context, and renderer status.

Mission Control parses only lines beginning with the exact prefix. It rejects oversized, malformed, wrong-job, out-of-range, non-finite, control-character, and non-monotonic payloads. Prefixed machine events do not enter human logs. All other bounded stdout remains an ordinary log.

`frame_written` advances rendered/in-flight progress but never safe progress. Only existing chunk validation and atomic publication advance validated/published counts. The UI labels these states:

- **Rendered, not yet safe**;
- **Safe, preserved on resume**.

The SSE store remains the replay authority after browser reconnect. The last structurally valid published preview is retained while a newer chunk is incomplete.

## Director workflow

Mission Control includes a Director destination. It discovers the newest validated local story/shot pair, shows seven acts and shot cards, and provides a native keyboard-accessible form for focal readability, depth, silhouette, color hierarchy, visual density, story clarity, mobile readability, findings, decision, and revision metadata.

Reviews are schema `1.0.0`, job/shot/frame checked, privacy checked, and atomically replaced under the existing UUID job directory. Director has explicit loading, empty, local-error/retry, and reconnecting states. It does not calculate a fake universal aesthetic score.

Mission Control routes:

```text
GET /api/mission-control/director/workspace
PUT /api/mission-control/director/workspace/{analysis_job_id}/reviews/{shot_id}
```

## Bounded proof

The verified synthetic proof is ignored runtime evidence under:

```text
test-output/cinematic-v2-proof-20260722-0905/
```

It contains synthetic cues and click-audio identity, story/shot/review artifacts, build and Blender manifests, an R3 `.blend`, six 640×360 stills for Signal → Awakening → Departure → First Gate, and a 4.5-second 640×360 H.264/yuv420p + AAC preview with hashes in `preview-signal-to-gate/preview-manifest.json`.

The art-direction decision is deliberately `revise`: the protagonist reads clearly, but narrative landmarks need stronger shot-to-shot differentiation. This is not artist approval and is not a V2 calibration result.

### Real-analysis artistic revision

The deterministic revision that answers those findings is ignored runtime evidence under:

```text
test-output/cinematic-v2-andromeda-revision-20260722-134911/
```

It is compiled from the completed deep analysis job `5ec62fcc-8230-4581-9d2c-60f1484b0879` for the real “Trip to Andromeda” timeline. The safe build manifest records only job, schema, mode, duration, frame range, and content identities; it does not disclose the private audio locator. The StoryPlan and ShotPlan remain separately identity-bound to `space-journey-story`, which remains preview-only.

The revision gives the four bounded stages distinct deterministic environment contracts:

- **Signal:** a dead-moon mass, regolith ruins, and a distant amber needle beacon;
- **Awakening:** an enclosed teal chamber whose petals and amber rings visibly reactivate;
- **Departure:** repeated cobalt monumental ribs, converging guide rails, and authored guide packets;
- **First Gate:** a frame-scale split-monolith diamond threshold, black foreground occlusion, an emerald membrane, and an authored shockwave/route-seal consequence.

The proof contains six 640×360 stills, six independently reviewed 320×180 phone stills, six one-second authored-motion excerpts, and their bounded six-second H.264/yuv420p + AAC review edit:

```text
preview-signal-to-gate/frame_000652.png
preview-signal-to-gate/frame_002085.png
preview-signal-to-gate/frame_002867.png
preview-signal-to-gate/frame_004690.png
preview-signal-to-gate/frame_005733.png
preview-signal-to-gate/frame_006775.png
preview-signal-to-gate/phone/
preview-signal-to-gate/signal-to-first-gate-preview.mp4
preview-signal-to-gate/mcp-render-receipt.json
```

The existing high-level Blender MCP build/review entrypoints produced and structurally validated `trackprompt-space-journey-story-r11.blend`. Major camera roots and aim targets use authored smooth keyframes; only the camera micro-reaction child has the exact bounded Z-axis audio driver. Each reviewed stage reports a dominant landmark, readable protagonist, foreground/midground/background geometry, a secondary narrative action, and a distinct light identity. A read-only, source-name-redacted Blender 5.2 dependency-graph scan passes all 481 full-timeline samples with no failing sample or global motion issue; it renders and saves nothing.

The canonical preview entrypoint emits `mcp-render-receipt.json` separately from the proof's `preview-manifest.json`. The receipt binds the R11 scene, canonical embedded ShotPlan, exact six source segments, ordered 180-frame PNG render digest, representative stills, FFmpeg strategy, and final MP4 by SHA-256. Before either proof manifest can be written, the verifier also recompiles the StoryPlan and ShotPlan from the bound cue sheet, stored resolved configuration, seed, and repository template and requires exact equality. The preview and build manifests then hash-bind the MCP receipt; a substituted cue, plan, scene, segment edit, still, clip, or receipt fails verification.

The persisted Director revision-6 reviews record specific R11 findings for all four shots. Their decision is `approve`, with every mobile-readability and story-differentiation field `clear`; the proof verifier independently passes those gates without rewriting the review. This is a bounded Codex-assisted Director decision, not human artist approval: `artistApproved` remains `false`.

Finalize a changed proof once, then verify its stored identities without mutation:

```powershell
backend\.venv\Scripts\python.exe .\tools\verify_cinematic_v2_proof.py `
  --root .\test-output\cinematic-v2-andromeda-revision-20260722-134911 `
  --ffprobe (Get-Command ffprobe.exe).Source `
  --scene .\test-output\cinematic-v2-andromeda-revision-20260722-134911\trackprompt-space-journey-story-r11.blend `
  --write-manifests

backend\.venv\Scripts\python.exe .\tools\verify_cinematic_v2_proof.py `
  --root .\test-output\cinematic-v2-andromeda-revision-20260722-134911 `
  --ffprobe (Get-Command ffprobe.exe).Source `
  --scene .\test-output\cinematic-v2-andromeda-revision-20260722-134911\trackprompt-space-journey-story-r11.blend
```

Open Mission Control and the bounded review folder without invoking calibration or a render:

```powershell
.\WZHK-Media-Launcher.cmd
explorer.exe "C:\Users\theon\GitHub\TrackPrompt-Studio\test-output\cinematic-v2-andromeda-revision-20260722-134911\preview-signal-to-gate"
```

Choose **Director** in Mission Control and select the newest local story plan. Do not use the render-start, calibration, authorization, or cloud actions for this review.

### R12 continuous-motion art-direction contract

The operator's human artistic review overrides the persisted R11 Codex-assisted decision for all subsequent work: R11 remains immutable structural evidence, but its effective artistic decision is **`REVISE`**. R12 does not rewrite or replace any R11 artifact, manifest, review, identifier, or test.

The deterministic R12 implementation and proof contract are rooted at:

```text
test-output/cinematic-v2-andromeda-r12-20260722-164623/
```

Its current status is **bounded media proof complete; Codex-assisted Director decision `REVISE`; human review pending**. The completed proof was independently verified against the real source audio without changing R11. Completion here means that the technical media/evidence contract passed; it is not artistic approval.

R12 derives its cue sheet from the real-analysis source window **224.000-266.000 seconds** and rebases that exact 42-second window to local frames 1-1260 at 30 fps. The private full source audio and the ignored 42-second audio extract are separately SHA-256-bound; committed and public-safe records use only the generic label `r12-source-window-audio`, never a private filename or locator. The result continues to use the existing `StoryPlan` and refined 12-shot `ShotPlan` contracts and remains separately identity-bound to the preview-only `space-journey-story` preset.

Only local frames **127-655 inclusive** are in the continuous artistic review: **529 frames / 17.633 seconds**. That range must play continuously as **Awakening -> Departure -> Gate approach -> Gate crossing -> Gate sealing**. It replaces the R11 excerpt edit with authored close-up, wide reveal, rear follow, side track, foreground-obstructed travel, low-angle approach, threshold push, and consequence pullback grammar. Signal is retained only as frames 1-126 of pre-roll plan context. The remaining plan ranges are contract-only compatibility coverage; Rupture, Transformation, and Arrival must not be artistically developed or rendered in R12.

The completed media proof contains two responsive compositions, not a crop-derived pair:

- `landscape`: native 1920x1080, with one continuous H.264/AAC clip, one MCP render receipt, eight full-resolution stills, eight independently downscaled 320x180 phone stills, one exact-range motion report, and one exposure report;
- `vertical`: native 1080x1920, with one continuous H.264/AAC clip, one MCP render receipt, eight full-resolution stills, eight independently downscaled 180x320 phone stills, one exact-range motion report, and one exposure report.

Across both profiles, that is two receipts, two H.264/AAC clips, 16 full-resolution stills, 16 phone stills, two motion reports, and two exposure reports. Every reference is SHA-256-bound in `build-manifest.json`, whose `mediaProof.status` is `complete`. The clips are `landscape/r12-continuous-preview.mp4` and `vertical/r12-continuous-preview.mp4`; the proof-local `r12-director-review.json` binds the exact still, phone, motion, and exposure evidence.

The separately reconciled vertical authority is `r12-continuous-vertical-proof-manifest.json` in the same ignored proof directory. It is deterministic and overwrite-resistant: creation is idempotent only while every calculated local hash and inspected media field remains identical, and verification reconstructs the complete manifest instead of trusting stored metadata. Its SHA-256 is `80da1b97ce91f240e6bdb1ef638d6279db78690a5cc861f55751209de978e316`. It binds the R12 scene and scene-manifest hashes, StoryPlan and ShotPlan hashes, original analysis frames 6721-7980, reviewed source frames 6847-7375, local frames 127-655, the exact affine mapping to output frames 1-529, ordered-render hashes, native vertical media metadata, eight representative still identities and hashes, motion/exposure evidence, and the Director/human status. The calculated local vertical-preview SHA-256 is `c53996521114496f69da011154dc4e2ded00695fd5044fba1bfebfa583ee2efc`.

Reconcile or independently verify the ignored vertical manifest with:

```powershell
backend\.venv\Scripts\python.exe tools\reconcile_cinematic_v2_r12_evidence.py `
  --root test-output\cinematic-v2-andromeda-r12-20260722-164623 `
  --ffprobe C:\path\to\ffprobe.exe `
  --verify-only
```

The reconciled status is structural `pass`, continuous-motion proof `pass`, vertical proof `pass`, Codex-assisted artistic recommendation `revise`, human artist approval `pending`, calibration readiness `blocked-pending-human-artistic-approval`, and production authorization `false`.

Each motion report covers camera and protagonist velocity, angular velocity, acceleration discontinuities, one-frame jumps, lens jumps, undeclared cuts, overshoot, and forbidden raw-audio macro-motion links; both exact 529-frame reports pass. Each exposure report decodes all 529 encoded frames and makes clipping and phone-size silhouette separation reviewable; both pass their technical thresholds. The R12 Director packet carries 12 specific criteria covering cinematic appeal, physical believability, protagonist agency, shot-scale variation, depth/parallax, exposure, materials, smoothness, story clarity, native vertical mobile readability, landscape readability, and stimulation without clutter. Its Codex-assisted decision is **`revise`**; human review remains **pending**, `approved` is false, and `artistApproved` remains false.

R12 remains preview-only. It does not start V2 calibration, create production authorization, provision cloud resources, render the full track, or extend the art pass into future acts. Generated audio extracts, Blender scenes, rendered frames, clips, phone derivatives, and proof/report outputs remain private ignored runtime evidence and must not be committed.

### R13 look-development lock

R13 is a look-development comparison derived from the immutable R12 source scene, not a continuation render. Its ignored proof root is:

```text
test-output/cinematic-v2-andromeda-r13-lookdev-20260722-201803/
```

The persistent `andromeda-r13-lookdev-lock` builder is invoked only through the existing high-level Blender MCP entrypoints. It creates three protagonist alternatives under one fixed camera and lighting setup, one chamber construction module, one gate monolith, and three gate-action diagnostic states: approach, whole-vessel compression, and post-crossing seal. The same deterministic construction system supplies shared bevels, weathered stone/metal surfaces, panel seams, buttresses, crystals, nested gate rings, and seal elements. No competing orchestration path was added.

The authoritative comparison directory is `variants-lock/`. Each of its eight states contains a native 1080x1920 beauty still, an uncropped 180x320 phone derivative, a subject mask, an optional gate mask, and a separately saved revision snapshot. `r13-lookdev-render-manifest.json`, `r13-lookdev-diagnostics.json`, and `r13-lookdev-review.json` are all hash-bound by `r13-lookdev-proof-manifest.json`. The proof-manifest SHA-256 is `ba0f13da116d6d13994c75bc58720153aae13c56147bc0373a6f8d168806365b`; the bound review SHA-256 is `bf13ef0b0e77ccab8e91ccf740cf80675207c9567141760f3d808391659cd476`. Generated `.blend`, PNG, JSON, and snapshot evidence remains ignored.

All eight native and phone frames were visually reviewed. The objective diagnostic summary records zero ordinary near-black flags, zero subject-separation flags, zero gate-separation flags, and a maximum clipped-highlight ratio of `0.0`. These technical results do not constitute artistic approval. Codex recommends `protagonist-b-ancient-engine`, the weathered stone-metal-crystal construction system, the thick-monolith/nested-ring/membrane/lock/seal gate, and restrained teal/cyan/amber lighting. All four operator selections remain null, the human review is `pending`, and `artistApproved` is false.

Because the human look selection is incomplete, the requested 3-5 second motion test is deliberately `blocked-pending-look-selection` and was not rendered. Calibration readiness remains blocked and production authorization remains false. R13 does not build future acts, provision cloud resources, calibrate V2, or render a full sequence or full track.

Open Mission Control, the authoritative variants, and the review record without starting a render:

```powershell
$proof = "C:\Users\theon\GitHub\TrackPrompt-Studio\test-output\cinematic-v2-andromeda-r13-lookdev-20260722-201803"
Start-Process "C:\Users\theon\GitHub\TrackPrompt-Studio\WZHK-Media-Launcher.cmd"
Start-Process "$proof\variants-lock"
Start-Process "$proof\r13-lookdev-review.json"
```

## Local verification

```powershell
backend\.venv\Scripts\python.exe -m pytest
backend\.venv\Scripts\python.exe -m ruff check .
backend\.venv\Scripts\python.exe -m mypy app
backend\.venv\Scripts\python.exe -m pip check
backend\.venv\Scripts\python.exe -m app.diagnostics.imports
backend\.venv\Scripts\python.exe -m pytest .\blender\tests
backend\.venv\Scripts\python.exe -m pytest .\tools\tests
Push-Location .\frontend
npm.cmd test -- --run
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
Pop-Location
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\test-run-trackprompt-runner.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\test-wzhk-mission-control.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\test-wzhk-react-launcher.ps1
docker compose -f compose.yaml -f compose.full-gpu.yaml config --quiet
.\WZHK-Media-Launcher.cmd -ValidateOnly
git diff --check
```

Local deployment means a current frontend build and loopback Mission Control service. If a render or encode is active, do not restart it merely to activate new code; report activation as deferred.

## Known limitations and remaining gates

- R13 is a neutral comparison stage rather than a complete cinematic environment. The chamber is an isolated construction module, the recommended protagonist bands remain visually busy, and the gate membrane is still a stylized disk-and-ring system rather than a final refractive volume.
- R13 has no motion proof. A human must select the protagonist, architectural material language, gate construction, and exposure/lighting treatment before the bounded 3-5 second motion test can be authored.
- The R12 real-analysis bounded proof passes its technical continuity, encoding, exposure, and evidence-integrity gates, but its Codex-assisted artistic decision is `REVISE` and it is not a human artist-approved film.
- The procedural/low-poly machinery, simple green membrane/beyond layer, still-graphic orange chamber aperture, dark corridor, and inherited geometric-orb protagonist remain the principal artistic limitations. The proof uses review-quality Eevee output rather than final-quality shading, volumetrics, motion blur, or production sampling.
- Only Signal through First Gate was revised and reviewed; the later Rupture, Transformation, and Arrival environments still need an equivalent artistic pass before any full-story approval.
- V2 needs its own render calibration, frozen candidate/profile hashes, and explicit production authorization.
- Cloud provisioning and a full-track V2 render are outside this implementation and were not run.
