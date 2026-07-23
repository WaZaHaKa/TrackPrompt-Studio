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

### R13.1 selected refinement and motion proof

The human-facing R13 review records the following direction as **selected for refinement**, not approved: `protagonist-b-ancient-engine`, `weathered-stone-metal-crystal-v1`, `nested-ring-monolith-v1`, and `restrained-teal-cyan-amber-v1`. `artistApproved` remains `false` and `humanArtistApproval` remains `pending`.

R13.1 is one deterministic refinement and one four-second motion proof under the ignored root:

```text
test-output/cinematic-v2-andromeda-r131-20260722-213840/
```

The additive `andromeda-r13.1-selected-refinement` builder loads the immutable R13 scene as hidden source and authors only `TP_R131_*` data through the high-level Blender MCP entrypoints. It reduces protagonist B to one narrow armor band and one atmosphere layer, exposes the purple frontal aperture with asymmetric orientation cues, retains a restrained rear wake, and animates bounded local compression. Repeated load pylons, rails, hinges, recesses, conduits, and routed crystals replace the disconnected-panel reading. The gate separates dark monolith thickness, three moving rings, one localized membrane, destination depth geometry, and four closing locks.

The authoritative motion range is **frames 1-120 inclusive at 30 fps: exactly 4.0 seconds**. The protagonist moves on its own authored path; the camera follows with lag, passes a foreground bay at a different parallax velocity, overtakes after crossing, and looks back as the protagonist recovers and the gate begins sealing. There are no camera cuts, lens changes, one-frame transform jumps, or raw-audio links to major travel. Dense diagnostics report protagonist peak velocity `9.5557` units/s, peak acceleration `33.5821` units/s², camera peak velocity `21.7605` units/s, peak angular velocity `1.9029` rad/s, lag distance `8.8220-11.8094` units, foreground/destination p95 parallax ratio `2.1281`, deformation range `0.0-1.0`, and final seal progress `0.48`.

Final images and motion use Blender 5.2 Eevee at native 1080x1920 with 64 temporal render samples, 32 volumetric samples, temporal reprojection, AgX Medium High Contrast, fixed 40 mm lens, disabled motion blur, DITHERED transparency, one atmosphere layer, and one local membrane. Compositor denoising is not used; the quality improvement is the deterministic increase from the included eight-sample comparison to 64 temporal samples. Phone derivatives are uncropped 180x320. The encoded preview is H.264/yuv420p plus 48 kHz stereo AAC, 120 frames, and 4.0 seconds. Media diagnostics report zero clipped highlights and no cosmic-darkness or mobile-contrast gate failures.

The immutable proof manifest is `r13.1-proof-manifest.json`, SHA-256 `acb896e9d445e2d0f7a187c3667dc22b01e91d9056644b510861e8b555685140`. It binds the authoritative scene, render manifest, exact motion diagnostics, media diagnostics, preview, selected protagonist/gate stills, artistic review, and unchanged R11/R12/R13 proof hashes. The preview SHA-256 is `1b1a02a52c241552e0b4a6c3b3490a04168fdd954b19f17d47e90332997a72c0`.

Codex recommends **`REVISE`**. The bounded story motion now reads, but the destination remains abstract, the connected architecture still has a procedural blockout finish, the protagonist can look spherical when its aperture is hidden, and the late motivated camera arc approaches its declared angular-velocity ceiling. Human approval remains pending; calibration readiness remains blocked and production authorization remains false. R13.1 does not develop Rupture, Transformation, or Arrival, provision cloud resources, calibrate V2, or render a full track.

Open Mission Control, the authoritative phone/native stills, preview, and review without starting another render:

```powershell
$proof = "C:\Users\theon\GitHub\TrackPrompt-Studio\test-output\cinematic-v2-andromeda-r131-20260722-213840"
Start-Process "C:\Users\theon\GitHub\TrackPrompt-Studio\WZHK-Media-Launcher.cmd"
Start-Process "$proof\media-lock-final\stills"
Start-Process "$proof\media-lock-final\r13.1-motion-preview.mp4"
Start-Process "$proof\r13.1-artistic-review.json"
```

### Finish-line owner attestation and output matrix

The historical R13.1 proof remains immutable. Its Codex-assisted `REVISE`
recommendation, pending human-artist field, and `artistApproved: false` value
continue to describe that bounded proof. They are not rewritten to manufacture
approval.

The later owner-attested record at
`production/andromeda-v2/creative-acceptance.json` separately records the
owner's decision to use R13.1 as the project-level visual and motion baseline.
It attributes the acceptance to the operator, not Codex. Its scope is the look
target for this project; it does not waive technical QA, authorize stale
identities, permit cloud provisioning, or start a full render.

The finish-line production contract is an exact output matrix:

- authored horizontal 1920 × 1080 at 30 FPS is required and enabled;
- authored vertical 1080 × 1920 at 30 FPS is optional and disabled by default;
- story timing, audio clock, protagonist state, and deterministic seeds are
  shared;
- camera, lens, framing, occupancy, foreground, safe zones, and environment
  layout may differ by authored composition;
- a vertical crop, stretch, letterbox, or blind reframe is invalid.

Changing the enabled matrix invalidates its calibration, aggregate forecast,
technical authorization, and operator-start authorization. A bounded vertical
composition proof demonstrates authoring capability but does not enable the
vertical production workload.

See [Render operation architecture](render-operation-architecture.md) for the
generic lifecycle and SaaS/privacy boundary, and [Andromeda V2 production
runbook](andromeda-v2-production-runbook.md) for safe operator actions. The full
13,029-frame production render is outside the implementation sprint and must
remain unstarted until the separate exact operator gate is authorized.

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

## Historical proof limitations and current production gate

The following R11–R13.1 bullets are preserved as stage-specific history. They
explain why the finish-line package was needed; they are not a current readiness
manifest for the later seven-act V2 source.

- R13.1 passes its bounded structural, motion, encoding, exposure, and immutable-evidence gates, but its Codex-assisted recommendation is `REVISE`; the provisional look is not artist-approved.
- Its destination is still abstract, mechanically connected architecture remains a procedural blockout, and the late look-back camera arc is the most aggressive motion in the proof.
- R13 is a neutral comparison stage rather than a complete cinematic environment. The chamber is an isolated construction module, the recommended protagonist bands remain visually busy, and the gate membrane is still a stylized disk-and-ring system rather than a final refractive volume.
- R13 has no motion proof. A human must select the protagonist, architectural material language, gate construction, and exposure/lighting treatment before the bounded 3-5 second motion test can be authored.
- The R12 real-analysis bounded proof passes its technical continuity, encoding, exposure, and evidence-integrity gates, but its Codex-assisted artistic decision is `REVISE` and it is not a human artist-approved film.
- The procedural/low-poly machinery, simple green membrane/beyond layer, still-graphic orange chamber aperture, dark corridor, and inherited geometric-orb protagonist remain the principal artistic limitations. The proof uses review-quality Eevee output rather than final-quality shading, volumetrics, motion blur, or production sampling.
- In the R11/R12 bounded proof, only Signal through First Gate was revised and reviewed; the later Rupture, Transformation, and Arrival environments were outside that historical proof.
- Current readiness is governed only by the V2 package, calibration, evidence, and exact technical-authorization artifacts. This chronology must not be used to infer readiness.
- Even when every objective V2 gate passes, the separate exact operator-start gate remains mandatory. Cloud provisioning and the full-track production render were not performed by this documentation update.
