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

- The bounded proof is a review artifact, not a final artist-approved film.
- V2 needs stronger narrative-environment landmarks and another human art-direction pass.
- V2 needs its own render calibration, frozen candidate/profile hashes, and explicit production authorization.
- Cloud provisioning and a full-track V2 render are outside this implementation and were not run.
