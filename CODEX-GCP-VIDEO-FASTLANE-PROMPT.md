# Codex execution task — ship TrackPrompt GCP Video Fast Lane and first 1080p music-video workflow

You are working in this Windows repository:

```text
C:\Users\theon\GitHub\TrackPrompt-Studio
```

A starter package has already been extracted into the repository. Your assignment is to **inspect, adapt, implement, test, build, locally deploy, commit, push, and ship** an additive GCP/Veo music-video generation fast lane that reuses TrackPrompt Studio's existing analysis and Mission Control architecture.

Do not stop after planning, documentation, interface sketches, scaffolding, or an offline proof that is not integrated with the real application. Work autonomously, make reasonable engineering decisions from existing conventions, and continue until the source release is complete. A missing live GCP model quota may block only a paid smoke request; it must not block implementation, offline tests, local deployment, commits, or the pull request.

## Operator goal and deadline

The operator needs a usable path today, not a week-long authorization exercise.

The desired user experience is:

```text
existing TrackPrompt song analysis
    → existing visual cues / StoryPlan / ShotPlan
    → select a video content package and output profile
    → review exact shots, provider requests, and maximum spend
    → authorize the complete exact batch once
    → generate/resume clips through GCP Veo
    → download and verify them
    → build a complete track-length edit automatically
    → export FCPXML/FCP7 XML/EDL/CSV for DaVinci Resolve
    → operator applies final artistic touches
```

**1080p is an acceptable final delivery and is the default. 4K is optional, not a completion requirement.** Do not turn a successful 1080p pipeline into a blocker because a 4K path has not been used live.

## Mandatory first inspection

Before editing anything:

1. Read `AGENTS.md` and obey all repository-local instructions.
2. Read the extracted starter files:
   - `START-HERE-GCP-VIDEO-FASTLANE.md`
   - `README-GCP-VIDEO-FASTLANE-STARTER.md`
   - `docs/gcp-video-fastlane-architecture.md`
   - `docs/gcp-video-fastlane-api-contract.md`
   - `docs/gcp-video-fastlane-gcp.md`
   - `docs/gcp-video-fastlane-privacy.md`
   - `docs/davinci-video-fastlane-handoff.md`
   - `docs/gcp-video-fastlane-runbook.md`
   - `video-projects/the-glitch-is-me/SHOT-LIST.md`
3. Read the current TrackPrompt architecture and operating documentation, at minimum:
   - `docs/architecture.md`
   - `docs/TRACKPROMPT_SELF_HANDOFF.md`
   - `docs/cinematic-visualizer-v2.md`
   - `docs/render-operation-architecture.md`
   - `docs/mission-control-react-ui.md`
   - `docs/mission-control-realtime-events.md`
   - `docs/mission-control-user-guide.md`
   - `docs/mission-control-troubleshooting.md`
   - `docs/cloud-rendering.md`
   - `docs/cloud-render-privacy.md`
   - `docs/privacy.md`
4. Record, but do not overwrite, the starting state:

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio"
git status --short
git branch --show-current
git remote -v
git log --oneline -n 12
.\WZHK-Media-Launcher.cmd -ValidateOnly
```

5. Inspect existing Mission Control jobs, current renderer/Blender/FFmpeg processes, the production mutex, and managed `.inflight-*` state with read-only repository tooling.
6. Inspect the actual current backend models/store/service/router, SSE protocol, TypeScript decoders, frontend screens, migrations, and tests before choosing exact filenames or database changes.
7. Run the starter's offline focused verification:

```powershell
.\tools\VERIFY-GCP-VIDEO-FASTLANE.ps1
```

If the main checkout is dirty with unrelated user work, preserve it. Do not clean, reset, stash, overwrite, or accidentally commit unrelated changes. Use the repository's established safe branching/worktree approach when needed.

## Existing architecture is authoritative

TrackPrompt already has valuable and mature components:

- local audio upload and analysis;
- visual feature/cue compilation;
- versioned StoryPlan and ShotPlan artifacts;
- persistent Mission Control jobs and state;
- real progress/SSE/reconnect behavior;
- manifests, media validation, FFmpeg tooling, and safe artifact handling;
- a React/FastAPI local application;
- privacy and production-safety boundaries.

**Reuse those components.** Do not create:

- another web application;
- another dashboard;
- another independent scheduler;
- another production database;
- another SSE stack;
- another generic authorization framework;
- a separate copy of audio analysis;
- a GCP-specific fork of TrackPrompt's project model.

The extracted `backend/app/video_generation/` code is a tested starter and contract reference. Adapt it to the actual current repository. The standalone JSON operation files may remain useful as bounded provider receipts, but the real job/task lifecycle and user-facing state must be integrated into existing Mission Control rather than acting as a competing scheduler.

## Permanent architecture to ship

```text
existing completed analysis job
        ↓
existing minimized visual cue sheet
        ↓
existing StoryPlan + ShotPlan when available
        ↓
provider-neutral VideoProject / VideoPlan compiler
        ↓
exact plan digest + price snapshot + sanitized request preview
        ↓
one project-level max-spend authorization
        ↓
existing Mission Control scheduler/store/events/UI
        ↓
GCP Veo long-running operations
        ↓
verified local video-only clips
        ↓
original local audio clock + deterministic timeline resolver
        ↓
FFmpeg autonomous preview + Resolve interchange package
```

Project-specific creative direction belongs under `video-projects/`. Generic source must remain track-agnostic and must not contain branches for “The Glitch Is Me,” “The Quantum Siren,” Andromeda, or DJ WaZaHaKa.

## Starter package that must be reviewed and retained/adapted

The extracted source includes:

```text
backend/app/video_generation/
backend/tests/video_generation/
schemas/
video-projects/the-glitch-is-me/
tools/SETUP-GCP-VIDEO-FASTLANE.ps1
tools/RUN-GCP-VIDEO-FASTLANE.ps1
tools/VERIFY-GCP-VIDEO-FASTLANE.ps1
```

It already implements/test-covers:

- versioned project/profile/shot/plan contracts;
- prompt composition and privacy exclusions;
- deterministic plan hashing;
- explicit pricing snapshot and estimates;
- one exact batch-level authorization bound to plan digest and cap;
- raw REST `predictLongRunning` and `fetchPredictOperation` support;
- durable operation records and conservative budget reservation;
- GCS download and `ffprobe` clip verification;
- normalized chapter mapping and StoryPlan/ShotPlan boundary snapping;
- FCPXML 1.11, FCP7 XML, CMX3600 EDL, edit sheet, and marker exports;
- local FFmpeg first assembly and audio mux;
- all 16 required shots for the first project.

Do not discard working starter logic merely to rewrite it in a different style. Refactor it where needed to match repository conventions and strengthen tests.

## GCP/Veo profile contract

Reviewed snapshot date: **2026-08-13**.

Supported model IDs in this release:

```text
veo-3.1-fast-generate-001
veo-3.1-generate-001
```

Required profile behavior:

| Profile | Model | Resolution | Duration | FPS | Default role |
|---|---|---:|---:|---:|---|
| `fast-1080p` | Veo 3.1 Fast | 1920×1080 | 8 s | 24 | default/final-capable |
| `quality-1080p` | Veo 3.1 standard | 1920×1080 | 8 s | 24 | optional quality rerender |
| `quality-4k` | Veo 3.1 standard | 3840×2160 | 8 s | 24 | optional only |

The Fast GA profile must not claim 4K support unless current official model documentation and a live bounded request prove it. The starter deliberately rejects Fast 4K.

All generated clips are video-only:

```text
generateAudio = false
sampleCount = 1 by default
aspectRatio = 16:9
region = us-central1
```

Do not upload the song to GCP. The original local master is the sole audio/timeline clock.

## First content package: “The Glitch Is Me”

Preserve and validate the exact 16-shot bank in:

```text
video-projects/the-glitch-is-me/shot-bank.json
```

The eight chapters and two principal generated clips each are:

1. Digital Ocean — suspended droplet; Siren materializes.
2. The Flicker — cold flickering room; impossible smile reconstruction.
3. Unsolvable Distance — distance equation; house made of bone.
4. The Mirror — mirror without face; distant crowd behind blue glass.
5. The Split — cold hand/broken microphone; time splits out of sync.
6. Liquid Circuit — liquid guitar filaments; satellite pulse.
7. Transcendence — Siren breaks through machine; equation dissolves into stars.
8. System Failure — world dissolves into reverb; pixelated brink.

The invented protagonist must remain clearly adult and must not impersonate a celebrity or real person. Provider prompts must continue to exclude visible text, logos, dialogue, lip-sync, malformed anatomy, generic cyberpunk armor, and unsafe material described by the content package.

Use the shot seeds and continuity tokens as reproducibility/continuity inputs where the provider supports them. Do not invent a claim of character lock across independent text-to-video generations. The UI/review should make consistency limitations clear, and a later optional reference-image workflow may be added only with owned/authorized images and explicit operator selection.

## Plan and privacy contract

The provider plan must be deterministic from its tracked/local inputs and carry hashes for every actual source artifact used. At minimum:

```text
schemaVersion
projectId / title
analysisJobId when applicable
selected profile
all compiled shots and exact requests
pricing snapshot date and per-second rate
base estimate, conservative estimate, maximum spend
source artifact hashes
planDigest
```

Provider requests may include only:

- sanitized visual prompt;
- sanitized negative prompt;
- model/generation parameters;
- generated-output GCS URI;
- optional operator-approved visual references added in a later bounded feature.

Provider requests and browser-visible plans must not include:

- original audio or stems;
- raw transcript or complete lyrics;
- private filenames or physical source paths;
- model/cache paths;
- account identifiers;
- GCP access tokens, cookies, private keys, or service-account JSON;
- unrelated analysis payloads.

The content pack may use reviewed visual concepts derived from the song, but it must not automatically dump the transcript/lyrics into the provider prompt.

## Fast authorization — exactly one project-level cap

Do not reuse the multi-stage Andromeda Blender production authorization ceremony for short provider clips.

Implement this bounded flow:

```text
compile exact plan
→ show sanitized request preview and current price snapshot
→ show base/conservative/max costs
→ operator enters one exact phrase
→ authorization binds projectId + planDigest + maxSpendUsd + expiry
→ first shot smoke request
→ remaining batch and bounded retries proceed without repeated prompts while under the same cap
```

Required invariants:

- no provider submit before authorization;
- authorization expires after a bounded period, default 24 hours;
- changing any plan input/profile/prompt/model/resolution/sample count/cap invalidates it;
- reserve the complete known request cost before submitting;
- refuse any request/retry that would exceed the authorized maximum;
- never silently upgrade quality, add candidates, increase duration, or change model;
- no per-shot confirmation;
- one retry action in the UI is allowed only if budget remains;
- provider failures may be conservatively counted until a reviewed reconciliation rule exists.

Default package estimates from the reviewed snapshot:

```text
Fast 1080p:     16 × 8 s = 128 s; base $12.80; 1.5× $19.20; cap $24.00
Standard 1080p: 16 × 8 s = 128 s; base $25.60; 1.5× $38.40; cap $45.00
Standard 4K:    16 × 8 s = 128 s; base $51.20; 1.5× $76.80; cap $80.00
```

The pricing table must remain versioned and visible. Do not assert that these values are permanently current.

## GCP setup and credentials

Use the active `gcloud` identity and short-lived access tokens. Do not create or commit a service-account JSON key.

The bounded setup path may:

- verify active account/project access;
- enable `aiplatform.googleapis.com` and `storage.googleapis.com` after explicit `-Apply`;
- create the exact named bucket if missing;
- verify the bucket;
- verify `us-central1` configuration.

It must not submit generation during setup.

The live doctor must distinguish and report:

```text
gcloud missing
no active account/access token
project permission denied
Vertex AI API disabled
bucket missing/permission denied
unsupported region
model access unavailable
quota/rate exhausted
provider filtering
transient provider/network failure
```

Do not spend hours inventing authorization workarounds. If model access or quota is externally unavailable, complete the source release, mock/fake-provider proof, setup diagnostics, and exact one-command continuation path, then report the blocker precisely.

## Asynchronous provider orchestration

Use Vertex AI long-running operations:

```text
predictLongRunning
→ durable operation name
→ fetchPredictOperation
→ GCS video URI
→ local download
```

Required behavior:

- operation names persist in backend-owned state;
- submit is idempotent for the exact plan/shot/attempt identity;
- application/terminal restart resumes polling rather than duplicating the request;
- polling uses bounded intervals/backoff and cancellation-aware waits;
- malformed/untrusted response fields fail safely;
- provider error bodies are bounded and redacted before storage/UI display;
- bearer tokens are never logged;
- output GCS URIs are validated before download;
- a downloaded file never silently overwrites a different valid local file;
- provider filtering is a distinct state, not a generic crash;
- successful shots remain available while one shot is retried.

Integrate all of this into the existing Mission Control store/service/events. Suggested task kinds are in `docs/gcp-video-fastlane-architecture.md`.

## Clip verification

Every downloaded clip must pass local checks before editorial use:

- regular readable MP4 file;
- expected video stream;
- expected dimensions for selected profile;
- 24 FPS within sensible probe tolerance;
- expected duration within a bounded tolerance;
- no required provider-generated audio;
- nonzero bytes and stable file identity;
- probe evidence recorded in the artifact manifest.

Keep accepted, rejected, filtered, and failed attempts distinct. Do not delete a returned clip merely because it is artistically rejected; keep it in ignored attempt-scoped runtime storage until the project is finalized or explicitly cleaned.

## Timeline and autonomous editing

The pipeline must create a complete track-length timeline automatically.

Authoritative rules:

- the original local audio duration is the master clock;
- delivery timeline is 24 FPS;
- the normalized chapter map works before exact analysis timing is known;
- when an existing TrackPrompt ShotPlan is available, snap chapter boundaries to the nearest valid boundary within three seconds;
- cover the complete song without gaps or overlaps;
- use deterministic clip alternation and alternate in-points on reuse;
- avoid putting the exact same framing back-to-back;
- preserve shot/chapter IDs and editorial notes;
- generate clear missing-clip diagnostics rather than an opaque FFmpeg failure.

The first autonomous assembly may remain conservative straight cuts plus local audio mux. Do not make sophisticated transition rendering a prerequisite for shipping. Resolve is the finishing surface.

## DaVinci Resolve deliverables

Generate all of the following from one resolved timeline:

```text
resolved-timeline.json
trackprompt-timeline.fcpxml        (FCPXML 1.11)
trackprompt-timeline.xml           (FCP7 XML fallback)
trackprompt-timeline.edl           (CMX3600 fallback)
edit-sheet.csv
davinci-markers.csv
assembly-plan.json
ASSEMBLE-PREVIEW.ps1
autonomous-preview-1080p.mp4       after execution
README-DAVINCI.txt
```

Requirements:

- FCPXML and FCP7 XML must be parseable and reference exact local clip/audio paths;
- FCPXML should target 1.11;
- EDL is a simple straight-cut fallback;
- marker and edit CSV files use Excel/Resolve-friendly UTF-8 encoding;
- the FFmpeg assembly writes a normal H.264/yuv420p preview and muxes the original audio locally;
- the final audio begins at zero and the preview duration matches the song within media tolerance;
- output resolution follows the selected profile; the filename may remain generic rather than claiming 4K when 1080p was selected;
- import instructions explain the fallback order.

## Minimal Mission Control UI

Extend the existing React Mission Control rather than making a new page/app.

The operator needs one focused workflow showing:

1. analysis/project selection;
2. selected profile with 1080p Fast as default;
3. 16 shot cards with chapter, title, prompt preview, seed, status, and output thumbnail;
4. current reviewed price snapshot, base/conservative/max estimate;
5. request-preview link;
6. one exact batch authorization control;
7. smoke/full generation start;
8. total and per-shot progress, provider state, retries, filtering, and budget remaining;
9. clip review: accept/reject/retry;
10. resolve timeline;
11. export Resolve package;
12. run/open autonomous preview.

Do not require approval on every shot. Shot review is creative acceptance after generation, not authorization before every provider call.

Use strict TypeScript response decoding and existing error/loading/reconnect conventions. Accessibility and keyboard operation must remain intact.

## Suggested backend integration surface

Follow actual repository conventions, but implement behavior equivalent to:

```text
POST /api/analyses/{analysis_job_id}/video-generation/plans
GET  /api/analyses/{analysis_job_id}/video-generation/plans/{plan_id}
GET  /api/analyses/{analysis_job_id}/video-generation/plans/{plan_id}/requests
POST /api/analyses/{analysis_job_id}/video-generation/plans/{plan_id}/authorize
POST /api/analyses/{analysis_job_id}/video-generation/plans/{plan_id}/start
GET  /api/mission-control/video-jobs/{job_id}
POST /api/mission-control/video-jobs/{job_id}/resume
POST /api/mission-control/video-jobs/{job_id}/shots/{shot_id}/retry
PUT  /api/mission-control/video-jobs/{job_id}/shots/{shot_id}/review
POST /api/mission-control/video-jobs/{job_id}/resolve-timeline
POST /api/mission-control/video-jobs/{job_id}/export-davinci
POST /api/mission-control/video-jobs/{job_id}/assemble-preview
```

Do not mechanically use these route names if existing naming/ownership conventions call for a better equivalent. Preserve the semantic boundaries.

## Database and artifact behavior

Use the existing persistent store and migration mechanism.

At minimum persist enough to recover:

- project/analysis/plan identity and digest;
- selected profile and pricing snapshot;
- batch authorization identity/expiry/cap, without the plaintext confirmation;
- provider shot attempt and operation name;
- state, timestamps, reserved cost, bounded error;
- output URI and local artifact identity;
- technical verification result;
- human review state;
- timeline/export artifact identities.

Migrations must be transactional/idempotent and must not destroy existing jobs. Runtime media and provider responses remain outside Git under established ignored data roots.

## Safety and repository rules

You are authorized to:

- edit source, tests, schemas, documentation, and local launch tooling needed for this feature;
- add a feature branch/worktree;
- run offline tests, builds, synthetic fixtures, fake-provider operations, XML validation, FFmpeg bounded previews, and local application validation;
- after the operator enters the one exact displayed plan-level phrase, submit one bounded smoke shot and the authorized batch through the configured project;
- commit only this task's source changes;
- push the feature branch and open a PR using the repository's normal workflow;
- safely rebuild/relaunch local loopback services when no active production work would be harmed.

You are not authorized to:

- stop, restart, delete, overwrite, or take the mutex from an active Blender render/encode merely to activate this feature;
- delete `.inflight-*`, existing frames, approved scenes, production manifests, jobs, audio, databases, or unrelated runtime evidence;
- reuse or mutate Andromeda technical authorization as provider authorization;
- submit any provider request before one exact plan-level cap authorization;
- exceed the configured cap;
- create service-account key JSON;
- upload source audio, stems, raw lyrics/transcripts, private analysis paths, or unrelated private files;
- commit `.env`, credentials, tokens, service-account JSON, `.trackprompt-data`, SQLite runtime files, audio, videos, thumbnails, provider responses, authorization records, logs, model weights, Blender scenes, frames, `test-output`, or `final-output`;
- run `git reset --hard`, `git clean -fd`, destructive Docker cleanup, volume deletion, force-push, or unrelated history rewriting;
- claim live model access, quota, generation, XML import, or final quality without evidence.

If an active production render/encode exists, keep it running. Continue source work and tests in isolation. Local activation may be deferred, but implementation, tests, commit, push, and PR must still be completed.

## Tests and evidence required

Retain and expand the focused starter tests. At minimum prove:

### Contracts and planning

- all four project configs validate;
- the 16-shot bank is complete, ordered, unique, and bound to the content package;
- Fast 4K fails closed;
- plan compilation is deterministic;
- changing a prompt/profile/source artifact changes the digest;
- required shots cannot disappear silently;
- provider plans exclude known private/local fields.

### Pricing and authorization

- current snapshot rates calculate the expected profile costs;
- base estimate above cap fails before submission;
- exact phrase and digest binding work;
- expired/wrong-project/wrong-plan authorization fails;
- retries cannot exceed the cap;
- no provider call occurs on compile/doctor/request-preview.

### Provider adapter

- exact REST endpoint and request fields;
- `generateAudio=false`;
- long-running operation submit/poll parsing;
- pending/success/error/filter states;
- idempotent resume after simulated restart;
- transient failure/backoff behavior;
- token/redaction behavior;
- GCS URI/download validation;
- fake provider tests never contact the network.

### Mission Control/API/UI

- state migration and recovery;
- strict API schemas/TypeScript decoding;
- monotonic/replayable progress after reconnect;
- one plan authorization control;
- 16 shot cards and per-shot states;
- blocked budget/provider states;
- retry/accept/reject behavior;
- no second scheduler or dashboard.

### Media/timeline/export

- generated clip `ffprobe` validation;
- full song coverage without gaps/overlaps;
- boundary snapping to fixture ShotPlan;
- stable deterministic reuse/in-points;
- parseable FCPXML/FCP7 XML;
- valid EDL/CSV output;
- FFmpeg command arrays (no shell-string injection);
- bounded synthetic autonomous assembly with local synthetic audio/video;
- final preview duration/audio/video QA.

### Full repository checks

Run the repository's current authoritative equivalents of:

```powershell
backend\.venv\Scripts\python.exe -m pytest
backend\.venv\Scripts\python.exe -m ruff check .
backend\.venv\Scripts\python.exe -m mypy app
backend\.venv\Scripts\python.exe -m pip check
Push-Location .\frontend
npm.cmd test -- --run
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
Pop-Location
docker compose -f compose.yaml -f compose.full-gpu.yaml config --quiet
.\WZHK-Media-Launcher.cmd -ValidateOnly
git diff --check
```

Adapt commands to the actual current repository. Do not weaken tests to make them pass.

## Bounded live execution path

After source integration and offline verification:

1. Run the GCP setup/doctor read-only first.
2. Compile the default `fast-1080p` plan with the actual GCS bucket and exact analysis StoryPlan/ShotPlan when available.
3. Show the operator:
   - exact plan digest;
   - 16 shots;
   - model/resolution/duration/sample count;
   - rate snapshot date;
   - base/conservative/max costs;
   - sanitized request previews.
4. Pause only for the **single exact plan-level maximum-spend phrase**.
5. After the operator enters it, submit `shot-001` first using that same full plan/profile authorization.
6. Poll, download, and technically verify it.
7. Continue the other 15 shots without asking for repeated authorization.
8. Keep successful shots and retry only specific failures while under cap.
9. Resolve the complete timeline from the real audio clock.
10. Export and run the autonomous 1080p preview assembly.
11. Open the Resolve handoff folder.

Do not require a 4K pass to declare the pipeline complete.

## Definition of done

This task is done only when:

- the feature is integrated into the current TrackPrompt/Mission Control architecture;
- existing analysis is reused rather than duplicated;
- provider-neutral contracts are versioned and tested;
- the full 16-shot project compiles deterministically;
- default 1080p cost/cap and one-time authorization work;
- GCP operations persist and resume safely;
- clips download and pass local media QA;
- a full-duration deterministic timeline is produced;
- FCPXML 1.11, FCP7 XML, EDL, edit sheet, and markers are emitted;
- a local autonomous preview can be assembled with the original audio;
- the minimal Mission Control workflow is usable;
- all focused and full repository checks pass, or any unrelated/pre-existing failure is isolated with evidence;
- docs and PowerShell runbooks match the implemented behavior;
- changes are committed cleanly on a feature branch, pushed, and a PR is opened when the repository workflow supports it;
- no secrets or generated private media are committed;
- no active production work was harmed.

## Final report format

Return one concise but complete engineering report containing:

1. branch, commit(s), push, and PR status;
2. starting-state preservation and whether any active render constrained local activation;
3. architecture integrated and exact reusable/project-specific boundaries;
4. backend, Mission Control, frontend, provider, timeline, export, and tooling changes;
5. tests/builds run with exact pass/fail counts;
6. offline GCP doctor result;
7. live smoke/batch status, but only if actually authorized/executed;
8. exact selected profile and cost reservation/remaining cap;
9. generated/verified shot counts and any filter/failure/retry;
10. output paths for clips, autonomous preview, FCPXML, FCP7 XML, EDL, CSV, and manifests;
11. any external model-access/quota blocker with exact continuation command;
12. confirmation that no source audio/lyrics/credentials or generated media were committed.

Begin with the mandatory inspection now. Do not ask the operator questions whose answers already exist in this prompt or repository. Make the best safe decision from current evidence and keep progressing until the source release is complete or one concrete external provider blocker prevents only the live generation step.
