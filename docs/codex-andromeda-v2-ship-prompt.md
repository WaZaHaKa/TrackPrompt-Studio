# Codex execution task: ship TrackPrompt Cinematic Visualizer V2

You are working in this repository on Windows:

```text
C:\Users\theon\GitHub\TrackPrompt-Studio
```

Your assignment is to **implement, test, build, commit, locally deploy, push, and ship** the Cinematic Visualizer V2 release described below. Do not stop after planning, scaffolding, documentation, or a partial proof of concept. Work autonomously, inspect the repository before changing it, make reasonable decisions from existing conventions, and continue until the engineering release is complete or a concrete environmental blocker prevents a specific verification step.

## Authorization and limits

You are authorized to:

- edit source, tests, and documentation needed for this task;
- add a new visualizer preset and versioned contracts;
- run bounded local tests, builds, synthetic smoke tests, and bounded Blender previews;
- create a feature branch;
- create commits containing only this task’s changes;
- push the feature branch to the configured remote;
- open a pull request when GitHub CLI or the repository’s normal workflow is available;
- rebuild and relaunch the local loopback application when it is safe to do so.

You are **not** authorized to:

- run a full-track production render;
- provision Brev, contact a billable cloud provider, create a live GPU fleet, or perform destructive cloud operations;
- reuse a V1 calibrated render profile or authorization record for a changed V2 scene;
- delete or overwrite production frames, `.inflight-*` checkpoints, render manifests, approved scenes, profiles, calibration evidence, model volumes, jobs, audio, or databases;
- run `git reset --hard`, `git clean -fd`, `docker compose down --volumes`, force-push, or rewrite unrelated history;
- commit `.env`, `.trackprompt-data`, SQLite files, audio, transcripts, model weights, `.blend` files, frames, videos, logs, `test-output`, `final-output`, `render-packages`, authorization records, caches, or generated runtime state.

If a production render or encode is active, preserve it. Do not stop it, restart its backend, take its GPU mutex, or modify its output. Continue with isolated source work and synthetic tests. A runtime restart may be deferred if it would endanger active work, but code, tests, commits, push, and the pull request must still be completed.

## Mandatory first inspection

Before editing anything:

1. Read `AGENTS.md` and obey all repository-local instructions.
2. Read at minimum:
   - `docs/architecture.md`
   - `docs/TRACKPROMPT_SELF_HANDOFF.md`
   - `docs/codex-blender-mcp-preview.md`
   - `docs/space-journey-visualizer.md`
   - `docs/mission-control-react-ui.md`
   - `docs/mission-control-realtime-events.md`
   - `docs/mission-control-user-guide.md`
   - `docs/mission-control-troubleshooting.md`
   - `docs/final-render-production-tooling.md`
   - `docs/render-profiles.md`
   - `docs/render-calibration.md`
   - `docs/privacy.md`
3. Record, but do not overwrite, the starting state:

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio"
git status --short
git branch --show-current
git remote -v
git log --oneline -n 10
.\WZHK-Media-Launcher.cmd -ValidateOnly
```


4. Inspect current Mission Control jobs, Blender/renderer processes, the production mutex, and managed `.inflight-*` state using existing read-only repository tooling. Do not remove or repair active state by deletion.
5. Inspect existing source before choosing exact filenames. Reuse current architecture instead of creating a second orchestration stack.

## Product goal

Ship an additive **Cinematic Visualizer V2** that turns the existing audio analysis and visual cue sheet into a deterministic story plan and shot plan, builds a story-driven Blender scene, supports safe interactive iteration through Blender MCP, smooths motion, and exposes true frame-level render progress and shot context in Mission Control.

The permanent architecture must be:

```text
existing audio analysis
        ↓
existing minimized visual cue sheet
        ↓
new deterministic cinematic/story compiler
        ↓
versioned story plan + shot plan
        ↓
new story-driven Blender preset
        ↕
existing narrow Blender MCP entrypoints
        ↓
frozen validated .blend revision
        ↓
existing chunked Blender CLI production renderer
        ↓
existing Mission Control backend + enhanced telemetry
        ↓
existing verified FFmpeg encoding pipeline
```

Blender MCP is an interactive authoring and review surface. It must not replace the headless CLI production renderer, production profiles, manifest identity, chunk publication, safe stop, resume, or encoding architecture.

## Non-negotiable compatibility requirements

- Keep `abstract-geometry` unchanged as the default preset.
- Keep existing `space-journey` reproducible as V1.
- Add a new preset named `space-journey-story` unless an existing naming constraint makes that impossible. If it does, use `andromeda-story-v2` and document the reason.
- Preserve cue schema compatibility, current `TP_AUDIO_BUS` controls, existing runner behavior, privacy filtering, fresh-job capture, bounded preview behavior, named volumes, and current production render safety.
- Do not silently migrate, overwrite, or reauthorize V1 scenes or profiles.
- A V2 scene has a new scene hash. Production rendering must remain blocked until V2 receives its own calibration, saved profile, operator approval, and exact authorization. This task ships V2 as a preview/authoring release, not as a falsely calibrated production profile.
- Network API changes require matching Pydantic models, OpenAPI behavior, strict TypeScript mirrors/parsers, tests, and docs in the same patch.
- No story-plan, shot-plan, review, telemetry, or export contract may expose source filenames, source paths, raw lyrics, transcript text, prompts, credentials, model paths, or physical runtime paths.

# Required implementation

## 1. Freeze V1 and register a separate V2 preset

Inspect the current preset registry and current Space Journey implementation. Add `space-journey-story` additively in the existing backend and Blender preset registries. Do not refactor V1 in a way that changes its deterministic output unless a shared bug fix is proven compatible by tests.

Expected areas include, after verifying actual paths:

```text
backend/app/visualizer/presets.py
blender/trackprompt_visualizer/preset_registry.py
blender/trackprompt_visualizer/preset_space_journey.py
blender/trackprompt_visualizer/preset_space_journey_story.py
```

Add explicit tests proving:

- V1 identifiers and defaults remain unchanged;
- V2 is discoverable and resolves a typed versioned configuration;
- V1 and V2 outputs/manifests cannot be confused or resumed across scene/profile hashes.

## 2. Add a deterministic cinematic planning package

Create an isolated package following existing import-boundary conventions, preferably:

```text
backend/app/cinematic/
├── __init__.py
├── schemas.py
├── planner.py
├── compiler.py
├── validation.py
├── store.py
├── router.py
└── templates/
    └── trip_to_andromeda_story_v1.json
```

Keep `__init__.py` dependency-light.

Define versioned typed contracts for at least:

```text
StoryPlan
StoryAct
StoryBeat
ShotPlan
Shot
CameraDirective
EnvironmentDirective
LightingDirective
CompositionDirective
MotionDirective
ReactiveLayer
ArtDirectionReview
```

The first story template is deterministic and contains these acts:

```text
Signal
Awakening
Departure
Gates
Rupture
Transformation
Arrival
```

The compiler consumes only approved/minimized visual cues plus the resolved visualizer configuration and deterministic template. It outputs versioned `story-plan.json` and `shot-plan.json`. Musical analysis controls timing, intensity, and bounded reactive details; the template controls narrative meaning.

Each shot must include, at minimum:

- stable shot ID and act ID;
- frame start/end and duration;
- story purpose and protagonist state;
- environment and secondary action;
- camera rig, lens, framing, and movement profile;
- dominant shape, foreground, midground subject, background landmark, atmosphere, and focal hierarchy;
- lighting/palette directive;
- reactive layers with bounded strengths;
- transition type and whether a discontinuity is intentional;
- representative review frame(s).

Validate positive contiguous/in-range frames, non-overlap, stable ordering, referenced IDs, finite numeric values, bounded strengths, schema version, and privacy. Compilation must be reproducible for identical inputs.

Add the narrowest API routes consistent with current conventions to compile, read, and export the cinematic plan. Do not return physical paths. Apply existing body limits, structured errors, origin/host protection, lifecycle rules, and atomic artifact writes.

## 3. Refactor the new Blender preset around shots

Add reusable modules only where they fit the existing package:

```text
blender/trackprompt_visualizer/shot_plan.py
blender/trackprompt_visualizer/motion.py
blender/trackprompt_visualizer/camera_rigs.py
blender/trackprompt_visualizer/protagonist.py
blender/trackprompt_visualizer/narrative_environments.py
blender/trackprompt_visualizer/art_direction.py
blender/trackprompt_visualizer/preset_space_journey_story.py
```

The protagonist remains orb-like but has persistent narrative states:

```text
dormant
signalled
awakened
travelling
damaged
transforming
transformed
arrived
```

Build reusable environments:

```text
dead_moon
signal_ruins
launch_structure
gate_corridor
broken_void
transformation_megastructure
andromeda_arrival
```

Reuse suitable V1 rings, lanes, nebulae, destination geometry, particles, materials, and lighting primitives as narrative components rather than discarding them.

Provide reusable camera rigs:

```text
establishing_reveal
slow_orbit
subject_follow
gate_approach
threshold_push
rupture_fall
transformation_closeup
scale_pullback
arrival_reveal
```

The shot plan chooses major camera movement. Audio may affect only a separate, tightly bounded micro-motion layer. A kick or onset must never directly jump the production camera.

Generate Blender timeline markers for acts and shots. The current frame must be mappable to active act/shot without guessing.

## 4. Fix sharp motion systematically

Use the existing visualizer curve and Blender import/timeline code. Add a tested signal-processing path:

```text
robust percentile normalization
→ noise floor/deadband
→ asymmetric attack/release envelope
→ low-pass smoothing
→ response curve
→ clamp
```

Retain raw event timing where needed for discrete events, but continuous transforms must consume smoothed controls. Clearly distinguish raw/event and continuous/smoothed signals in code and contracts without breaking existing cue privacy.

Add typed motion profiles with bounded properties such as:

```text
interpolation
ease-in frames
ease-out frames
maximum velocity
maximum acceleration
maximum angular velocity
```

Provide profiles including:

```text
cinematic_drift
slow_acceleration
controlled_chase
weightless_float
impact_recoil
transformation_orbit
micro_audio_response
```

Use explicit Blender interpolation and handles. Separate planned camera root motion, target/aim motion, and micro response. Do not use motion blur to hide discontinuities.

Extend `blender/timeline_health_scan.py` and its tests to detect/report:

- undeclared one-frame transform jumps;
- position/angular velocity outliers;
- acceleration discontinuities;
- unexpected F-curve overshoot;
- camera clipping or invalid lens values;
- shot-boundary discontinuities not declared as cuts;
- direct raw-audio control of major camera transforms.

Intentional cuts declared by the shot plan must not be false positives.

## 5. Extend existing Blender MCP entrypoints

Extend the existing narrow `blender/trackprompt_visualizer/mcp_entrypoints.py` integration. Do not create a competing orchestration service unless the current code proves it is strictly necessary.

Expose stable high-level operations equivalent to:

```text
load_story_revision
build_story_scene
build_shot
set_review_shot
set_review_frame
apply_shot_revision
validate_current_shot
capture_review_state
save_revision_snapshot
```

Use existing naming and result-envelope conventions. All persistent MCP-driven changes must be represented by source-controlled configuration or builder code before saving a `.blend` snapshot. A Blender-only manual mutation is not the source of truth.

A successful MCP authoring cycle is:

```text
edit source/config
→ invoke stable MCP entrypoint
→ inspect viewport screenshot
→ run shot/scene validation
→ record review result
→ save versioned ignored .blend snapshot
```

MCP must never initiate a full-track production render.

## 6. Implement true frame-level render telemetry

Keep Mission Control as the control plane. Extend the existing production worker, backend process parser/store/service/router, SSE events, TypeScript decoders, and live-progress UI.

Expected source areas, after verifying actual paths, include:

```text
blender/render_final_chunk.py
backend/app/mission_control/models.py
backend/app/mission_control/processes.py
backend/app/mission_control/renderers.py
backend/app/mission_control/service.py
backend/app/mission_control/store.py
backend/app/mission_control/router.py
frontend/src/mission-control/events.ts
frontend/src/mission-control/types.ts
frontend/src/mission-control/screens/LiveProgress.tsx
```

Emit machine-readable stdout events using one exact prefix, for example:

```text
WZHK_RENDER_EVENT {"type":"frame_started",...}
WZHK_RENDER_EVENT {"type":"frame_written",...}
WZHK_RENDER_EVENT {"type":"render_stats",...}
WZHK_RENDER_EVENT {"type":"chunk_complete",...}
WZHK_RENDER_EVENT {"type":"render_cancelled",...}
```

Use the current Blender handler/callback architecture and do not rely solely on directory polling. Events must be bounded, parse-safe JSON and include applicable fields:

```text
schemaVersion
event sequence or timestamp
job ID
worker/process identity
chunk ID/start/end
frame
frame start/end
elapsed seconds
output identity (never an unsafe public physical path)
act ID/name
shot ID/name
renderer status
```

The backend must:

- parse only exact prefixed lines and treat all other output as ordinary bounded logs;
- validate event schema and ignore malformed/untrusted lines safely;
- persist current frame and latest completed frame in existing job state;
- preserve monotonic event sequencing and replay after browser reconnect;
- preserve the current safe/in-flight distinction;
- never mark an in-flight frame as safely published before chunk validation/atomic publication;
- retain the last valid preview while a new image is incomplete;
- generate a bounded thumbnail outside the Blender render handler after verifying a complete, structurally valid, stable file;
- publish a versioned preview URL atomically;
- avoid slowing the renderer with image processing inside callbacks.

The UI must clearly show:

```text
current act
current shot
current frame
latest rendered frame
latest safe/published frame
active chunk
current-frame elapsed time
last completed preview frame and timestamp
renderer/heartbeat status
```

Use wording such as **Rendered, not yet safe** versus **Safe, preserved on resume**. Closing or refreshing the browser must not stop or restart the job. Keep Advanced details for paths/hashes/raw logs. Preserve accessibility, keyboard behavior, reduced motion, responsive layout, and non-color-only status indicators.

## 7. Add a Director/art-direction review surface

Add a Mission Control Director screen or a clearly separated Director workspace using current routing/layout conventions. Update navigation, docs, tests, and accessibility contracts if a new destination is added.

The first release is an explainable human/Codex-assisted review workflow, not a fake universal aesthetic score. Support a typed review artifact with:

```text
shot ID
review frame
focal readability
depth
silhouette
color hierarchy
visual density
story clarity
mobile readability
findings
decision: approve or revise
revision metadata
```

The UI should present story acts, shot cards, representative frames, current revision, findings, and approve/revise state. Store reviews using existing local persistence conventions and atomic writes. Do not call an external service and do not send private media or analysis outside the machine.

## 8. Bounded vertical-slice proof

Create an end-to-end bounded proof for:

```text
Signal → Awakening → Departure → First Gate
```

Use synthetic fixtures for automated tests. If Blender 5.2 is available and no production renderer/GPU mutex is active, also build a V2 scene and produce:

- six representative stills;
- one verified H.264/AAC preview no longer than 10 seconds;
- resolution no greater than the existing bounded preview contract unless an existing test explicitly uses less;
- a build manifest, story plan, shot plan, review artifact, and preview manifest;
- no full timeline render and no production authorization.

If a production render is active, do not run this Blender preview. Complete all pure, backend, frontend, and synthetic worker tests and record the bounded Blender verification as deferred due to protected active work. Do not treat that as permission to interfere.

# Testing and quality gates

Run targeted tests while developing, then the complete relevant validation suite. Use existing environments and locked dependencies; avoid new dependencies unless unavoidable and reviewed.

At minimum run, adjusting only for actual repository commands:

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio"

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

Run relevant Chromium E2E tests when the local app can be launched without disrupting active work. Add tests covering:

- deterministic story/shot compilation;
- schema and privacy validation;
- V1 preset regression and V2 registration;
- smoothing and motion bounds;
- timeline health findings and declared cuts;
- render-event parsing, malformed-line rejection, persistence, monotonic sequence, reconnect replay, and restart recovery;
- in-flight versus safe frame semantics;
- atomic preview replacement and stale-preview retention;
- strict TypeScript decoding of new API/event fields;
- Director screen accessibility and empty/error/reconnecting states;
- launcher/build fingerprint behavior after frontend changes.

Do not weaken or delete existing tests to make the new work pass. Fix regressions at their source.

# Documentation

Update existing architecture, visualizer, Blender MCP, Mission Control realtime, user, troubleshooting, and self-handoff docs. Add a focused document such as:

```text
docs/cinematic-visualizer-v2.md
```

Document:

- story/shot schemas and versions;
- V1/V2 identity boundary;
- motion smoothing and camera rules;
- MCP authoring workflow;
- frame-event schema and safe/in-flight semantics;
- Director review workflow;
- bounded preview command/evidence;
- why V2 cannot use V1 calibration or authorization;
- exact local deployment/verification commands;
- known limitations without claiming a final artist-approved film or live cloud deployment.

# Git, commit, deploy, and ship

## Branch safety

- Do not fail merely because the worktree has unrelated changes.
- Never stash, discard, overwrite, or include unrelated edits.
- Prefer a feature branch named:

```text
feat/andromeda-story-v2
```

- If switching branches would endanger unrelated dirty work, stay on the current branch or create a separate Git worktree from current `HEAD`; choose the safest method and document it.
- Stage explicit task files only. Review `git diff --cached` before every commit.

## Commits

Create logically separated commits after their corresponding tests pass. Recommended sequence:

```text
feat(cinematic): add deterministic story and shot planning
feat(blender): add story-driven Space Journey preset and smooth motion
feat(mission-control): add frame telemetry and director workflow
test(docs): verify and document cinematic visualizer v2
```

Follow the repository’s actual commit convention if different. Do not amend unrelated commits.

## Local deployment

“Deploy” for this local-first project means a verified local Mission Control build and loopback service, not an external hosted service.

1. Run `WZHK-Media-Launcher.cmd -ValidateOnly`.
2. Ensure the production frontend build is current.
3. If no render/encode is active and restart is safe, use the documented launcher/startup path so the changed backend/frontend are loaded.
4. Verify loopback health, capabilities/OpenAPI, new cinematic endpoints, Mission Control load, Director route, live-event compatibility, and the retained ordinary analysis workspace.
5. Do not manually kill a healthy instance. Use documented startup/reuse behavior.
6. If active work makes restart unsafe, leave the tested build ready and explicitly report runtime activation as deferred. Do not claim it was deployed in that case.

## Push and pull request

After all commits and final tests:

```powershell
git status --short
git log --oneline --decorate -n 10
git push -u origin feat/andromeda-story-v2
```

Use the actual branch name if safety required a different one. Never force-push. If `gh` is installed and authenticated, open a pull request with:

```text
Title: Cinematic Visualizer V2: story planning, smooth motion, and live frame telemetry
```

The PR body must summarize architecture, compatibility, tests, local deployment state, bounded preview evidence, privacy/safety boundaries, and remaining artistic/calibration gates. Do not merge a protected branch or bypass review/checks. A pushed tested branch plus PR is the definition of source delivery.

# Definition of done

Do not call this shipped until all applicable items are true:

- existing V1 preset IDs/defaults/tests remain intact;
- new V2 preset is registered, typed, deterministic, and preview-only until separately calibrated;
- cinematic story and shot plans compile reproducibly and pass privacy validation;
- protagonist states, narrative environments, camera rigs, and shot markers exist;
- continuous motion uses smoothed controls and health scanning catches undeclared sharpness;
- Blender MCP exposes stable high-level authoring/review entrypoints;
- production Blender emits validated frame-level events;
- Mission Control persists/replays frame events and distinguishes rendered/in-flight from safe/published;
- latest completed preview updates atomically without manual folder inspection;
- Director review workflow is usable and accessible;
- backend, Blender/tool, frontend, PowerShell, Compose, launcher, and relevant E2E checks pass;
- bounded preview evidence passes when safe to run, or its deferral is truthfully recorded because active production work was protected;
- docs and self-handoff are updated;
- task-only commits exist;
- branch is pushed and PR is opened when remote tooling permits;
- local Mission Control build is activated and health-checked when safe, or activation is explicitly marked deferred rather than falsely claimed;
- no private/generated/runtime artifact is committed;
- no full-track render, cloud provisioning, destructive cleanup, or false production/calibration claim occurred.

# Final response format

At the end, provide a concise but complete handoff containing:

1. **Status:** shipped, or shipped-to-branch with one precisely named environmental verification blocker.
2. **Branch and PR:** branch, commit hashes/messages, push result, PR URL or exact reason PR creation was unavailable.
3. **Implementation:** major components and key files changed.
4. **Verification:** commands and pass/fail counts; include skipped checks and why.
5. **Local deployment:** launcher/build/health results and exact URL/port if activated.
6. **Bounded evidence:** paths to ignored story plan, shot plan, manifests, stills, preview, and review artifact, without exposing private audio.
7. **Compatibility and safety:** V1 preservation, V2 calibration block, active-render preservation, and confirmation that no cloud/full render/destructive command ran.
8. **Remaining gates:** only genuine human artistic review, future V2 calibration/authorization, or specific environment blockers—no vague TODO list.

Do not merely tell me what should be done. Implement it, test it, commit it, deploy it locally when safe, push it, open the PR, and report the exact result.
