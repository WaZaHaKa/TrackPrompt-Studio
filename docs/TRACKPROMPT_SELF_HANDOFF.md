# TrackPrompt Studio — Self-Handoff

## Cinematic Visualizer V2 handoff (2026-07-22)

`space-journey-story` is registered as a distinct preview-only preset with deterministic story/shot compilation, Blender MCP authoring entrypoints, bounded motion/camera health checks, exact-prefix frame telemetry, and a Mission Control Director workflow. The earlier synthetic proof under `test-output/cinematic-v2-proof-20260722-0905/` remains preserved with its honest `revise` decision.

R11 and R12 are separate proofs and must not be conflated. The preserved R11 proof remains under `test-output/cinematic-v2-andromeda-revision-20260722-134911/`; it contains six 640×360 stills, six 320×180 phone stills, and a six-second edit assembled from six authored-motion excerpts. Its historical Codex-assisted approval remains immutable evidence, but the operator's artistic override is `REVISE`.

The current bounded continuous-motion proof is R12 under `test-output/cinematic-v2-andromeda-r12-20260722-164623/`. Its responsive vertical artifact is a native 1080×1920, 30 fps, 529-frame, 17.633333-second H.264/yuv420p + AAC preview, not an R11 excerpt or crop. `r12-continuous-vertical-proof-manifest.json` independently hash-binds the R12 scene, StoryPlan, ShotPlan, source-to-preview frame mapping, output ordering, eight vertical stills, preview, motion/exposure reports, and review status. The verified vertical preview SHA-256 is `c53996521114496f69da011154dc4e2ded00695fd5044fba1bfebfa583ee2efc`; the immutable proof-manifest SHA-256 is `80da1b97ce91f240e6bdb1ef638d6279db78690a5cc861f55751209de978e316`. Structural, continuous-motion, and vertical proof status pass; the Codex-assisted artistic recommendation remains `REVISE`, human approval is pending, calibration readiness is blocked, and production authorization is false. V1/R11 evidence was not changed, and V2 calibration, cloud provisioning, future-act development, and full-track rendering were not started. See `docs/cinematic-visualizer-v2.md` for exact evidence, verification, limitations, and review commands.

**Date:** 2026-07-19  
**Repository:** `C:\Users\theon\GitHub\TrackPrompt-Studio`  
**Status:** Full TrackPrompt → cue-sheet → selectable Blender preset → bounded preview workflow verified end to end, including Space Journey.

> Verification note: the original Abstract Geometry baseline below remains a
> historical record. Space Journey was independently rebuilt and rerun from
> this checkout on 2026-07-19 with the canonical `-BuildStack` workflow; exact
> current evidence is recorded below.

## 2026-07-20 catalogue and long-form milestone

The current source also contains the professional local catalogue milestone:
clients/projects/batches, 32 MiB resumable chunks, 12-hour source admission,
durable cancellable streaming multi-signal segmentation with bounded local refinement, reviewable
virtual segments, persistent fair child scheduling, mastering comparison
reports, hash-chained audit/revisions/artifacts, explicit deletion, and verified
backup/restore tooling. Ordinary single-track analysis remains the default UI
and retains its 1,200-second bound.

Current source verification passed 255 backend tests, 44 frontend tests, 114
Blender/tool tests, two Chromium E2E scenarios, the canonical PowerShell runner
tests, and backup/verify/restore wrapper smoke. No production Blender render was
started. The final live full-GPU smoke was blocked by Docker Desktop’s NVIDIA
prestart hook reporting `WSL environment detected but no adapters were found`;
host `nvidia-smi` still reported the RTX 3060. The local backend/frontend were
restored healthy in base mode using the existing named data volume, and the
prompt-writer/model volume was preserved. Restart Docker Desktop/WSL GPU
integration before rerunning `verify-full-gpu.ps1`; do not delete volumes.

---

## 1. System purpose

TrackPrompt Studio is a local-first music analysis application that:

1. accepts a permitted local audio file;
2. performs Fast or Deep audio analysis;
3. uses the RTX 3060 for Demucs, genre tagging, and lyrics analysis where enabled;
4. stores a fresh asynchronous TrackPrompt job;
5. exports a minimized Blender visual cue sheet;
6. builds a procedural, audio-reactive Blender scene;
7. renders bounded preview stills and a short preview clip;
8. exposes narrow Blender MCP entrypoints for Codex orchestration.

No normal analysis path should send audio, lyrics, cue data, or prompts to an external service.

---

## 2. Current verified working state

The latest Codex pass reported the following as working:

- Full-GPU Docker profile.
- NVIDIA GeForce RTX 3060 access.
- Deep Demucs four-stem analysis on CUDA.
- CLAP genre analysis.
- Faster-Whisper lyrics analysis.
- Private lyrics behavior and safe `no_reliable_words` handling.
- Visual-feature extraction at 20 Hz.
- Visual cue-sheet API.
- Fresh job-ID capture and persistence.
- Automatic stale-backend detection and one bounded rebuild attempt.
- Blender 5.2.0 LTS headless scene construction.
- Abstract Geometry preset.
- Space Journey preset with a typed `1.0.0` configuration, role-labelled stills,
  deterministic procedural geometry, and bounded parameter revisions.
- Bounded preview stills and 10-second MP4 preview with muxed audio.
- Cached no-rebuild runner path.
- Controlled stale-backend recovery path.
- Backend, frontend, Blender, PowerShell, Compose, and E2E checks.

The original failure was a healthy but stale backend image whose live OpenAPI did not expose the visual-cue routes. The canonical runner now detects this and can rebuild/recreate the backend without deleting named volumes.

### Fresh Space Journey verification

The latest verified run is:

```text
Run:    test-output\system-runs\run-20260719-151848-5ba6f8d7
Job:    5ec62fcc-8230-4581-9d2c-60f1484b0879
Status: completed
Mode:   deep
```

It used `-BuildStack`, created and polled only that fresh job, exported cue
schema `1.1.0`, resolved Space Journey config schema `1.0.0`, built the original
39-object Space Journey scene, rendered the six ordered roles, and verified a
10.0-second H.264/AAC preview at 640×360 and 30 fps. The job and named volumes
were preserved.

The premium visual-quality-only upgrade established the approved visual
baseline under `test-output\space-journey-premium-pass-20260719\iteration-08\`.
The subsequent cinematic/emotional direction pass re-used the same verified cue
sheet and private audio without changing analysis, APIs, configuration, runner,
or cue contracts. Its final deterministic scene and review artifacts are:

```text
test-output\space-journey-cinematic-pass-20260719\iteration-04\
```

The cinematic scene has 75 objects, 25 materials, 14 collections, 154 F-curves,
and the same 10 audio-bus curves. It retains six primary rings, nine companion
lanes, 304 stars in four combined layers, 124 combined orbital-dust elements,
four nebula layers, and no build warnings. Fifteen deterministic macro states
stage mystery, approach, breath, rebuild, revelation, and release over the
unchanged micro audio response. The final refinement replaces the flat optical
aperture with a dimensional violet/cyan portal, adds a threshold hold before
the accelerated release, and moves the existing foreground bracket plus
near/far travel layers through bounded depth during the rising transition.
The six final review stills are 1280×720 under
`preview-stills\`. The bounded clip under
`preview-final\space-journey-preview.mp4` covers only frames 6930–7229 and is
verified as exactly 10.0 seconds, 640×360, 30 fps, H.264 with AAC audio. Its
external FFmpeg frame sequence was removed after the verified mux. No full-track
render was started.

---

## 3. Canonical local paths

### Repository

```text
C:\Users\theon\GitHub\TrackPrompt-Studio
```

### Blender executable

```text
C:\Program Files\Blender Foundation\Blender 5.2\blender.exe
```

### Real validation track

```text
C:\Users\theon\OneDrive\Desktop\Gratis Project\DJ WaZaHaKa - Trip to Andromeda.wav
```

### Canonical whole-system runner

```text
C:\Users\theon\GitHub\TrackPrompt-Studio\run-trackprompt-to-blender.ps1
```

### Core implementation locations

```text
backend\app\visualizer\
backend\app\main.py
backend\app\schemas.py
blender\
frontend\src\components\BlenderVisualizerPanel.tsx
tools\test-run-trackprompt-runner.ps1
```

### Primary documentation

```text
docs\architecture.md
docs\analysis-methods.md
docs\blender-visual-cue-sheet.md
docs\blender-visualizer-mvp.md
docs\codex-blender-mcp-preview.md
docs\space-journey-visualizer.md
```

---

## 4. Canonical commands

### Fresh or source-changing run

Use this when backend/frontend source changed, the live API is stale, or the visual-cue routes are missing:

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio"

powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\Users\theon\OneDrive\Desktop\Gratis Project\DJ WaZaHaKa - Trip to Andromeda.wav" `
  -BlenderExe "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
  -VisualizerPreset "space-journey" `
  -ConfirmPermission `
  -ConfirmLyricsConsent `
  -BuildStack
```

### Normal cached run

Use this after the current source is already running:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\Users\theon\OneDrive\Desktop\Gratis Project\DJ WaZaHaKa - Trip to Andromeda.wav" `
  -BlenderExe "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
  -VisualizerPreset "space-journey" `
  -ConfirmPermission `
  -ConfirmLyricsConsent
```

### Build scene without rendering the preview

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\Users\theon\OneDrive\Desktop\Gratis Project\DJ WaZaHaKa - Trip to Andromeda.wav" `
  -BlenderExe "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
  -VisualizerPreset "space-journey" `
  -ConfirmPermission `
  -ConfirmLyricsConsent `
  -SkipPreview
```

### Run without lyrics analysis

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\Users\theon\OneDrive\Desktop\Gratis Project\DJ WaZaHaKa - Trip to Andromeda.wav" `
  -BlenderExe "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
  -ConfirmPermission `
  -EnableLyrics:$false
```

### Inspect the most recently saved TrackPrompt job ID

```powershell
Get-Content .\test-output\last-trackprompt-job-id.txt
```

### Inspect current services

```powershell
docker compose `
  -f compose.yaml `
  -f compose.full-gpu.yaml `
  ps
```

### Inspect API health and capabilities

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health |
  ConvertTo-Json -Depth 30

Invoke-RestMethod http://127.0.0.1:8000/api/capabilities |
  ConvertTo-Json -Depth 50
```

---

## 5. Successful real-track run

### Primary successful job

```text
c94fcedc-e7f8-4d88-af75-52c0bbac16e0
```

Reported terminal state:

```text
completed
```

Reported requested/effective analysis:

```text
deep / deep
```

Reported Deep adapter:

```text
Demucs four-stem on CUDA
```

Reported lyrics outcome:

```text
completed safely with no_reliable_words
```

### Primary run folder

```text
C:\Users\theon\GitHub\TrackPrompt-Studio\test-output\system-runs\run-20260719-000700-4db9fbe7
```

Important artifacts:

```text
run-manifest.json
visual-cues.json
trackprompt-abstract.blend
trackprompt-abstract.manifest.json
preview\preview-manifest.json
preview\trackprompt-preview.mp4
```

### Cached-path job

```text
673f1b31-60d3-4b00-81d2-03193c36df54
```

Reported behavior:

```text
buildStack: false
backendRepairAttempted: false
```

Cached run folder:

```text
C:\Users\theon\GitHub\TrackPrompt-Studio\test-output\system-runs\run-20260719-001152-bb0f25af
```

### Controlled stale-backend recovery job

```text
e62ae667-2545-444e-bc6a-4870340beb6d
```

Reported behavior:

- both visual-cue routes absent before recovery;
- one backend-only rebuild;
- both routes present afterward;
- analysis completed;
- Blender scene built.

Recovery run folder:

```text
C:\Users\theon\GitHub\TrackPrompt-Studio\test-output\system-runs\run-20260719-001936-9828c8ca
```

---

## 6. Real-track cue-sheet contract

Reported cue-sheet data:

```text
Schema:       1.1.0
FPS:          30
Frame end:    13029
Beats:        908
Onsets:       1382
Sections:     7
Transitions:  6
```

Reported continuous curves:

```text
masterEnergy
drumEnergy
bassEnergy
vocalEnergy
otherEnergy
brightness
lowBandEnergy
midBandEnergy
highBandEnergy
transientActivity
```

Frame conversion invariants:

```text
434.286 seconds at 30 FPS -> frameEnd 13029
228.8 seconds             -> frame 6865
```

Cue sheets must remain minimized and exclude:

- source filename;
- source path;
- raw lyrics;
- private transcript;
- waveform samples;
- full chord sequence;
- prompt output;
- model-cache information.

---

## 7. Blender scene contract

Reported generated scene:

```text
Preset:             abstract-geometry
Blender:            5.2.0 LTS
Objects:            57
Materials:          5
F-curves:           72
Named collections:  9
Audio-bus controls: 10
Audio strips:       1
Rings:              4
Shards:             42
Lights:             3
```

Required `TP_AUDIO_BUS` controls:

```text
master_energy
drum_energy
bass_energy
vocal_energy
other_energy
low_band
mid_band
high_band
brightness
transient_activity
```

Expected TrackPrompt collections:

```text
TP_WORLD
TP_CAMERAS
TP_LIGHTS
TP_PRIMARY_GEOMETRY
TP_RINGS
TP_SHARDS
TP_VOCAL_ELEMENTS
TP_BACKGROUND
TP_DEBUG
```

The current Space Journey preview uses a displaced dark destination shell with
a camera-facing optical iris, violet crescent, cyan focal seed, sparse lattice,
clustered crystalline facets, inner filaments, and a restrained Fresnel halo.
Its orbit system preserves the six-primary/nine-companion contract while
separating broken structural sweeps, dim continuous ellipses, and hairline
traces with moving light packets. Four point-like parallax star layers,
directional low-frequency nebula depth, and bounded camera target/shift easing
replace the earlier HUD-like dash wall, triangular confetti, and centered zoom.

---

## 8. Preview output

Reported preview validation:

```text
Stills:            6 at 1280x720
Bounded clip:      10.0 seconds at 640x360 and 30 fps
Video codec:       H.264
Audio codec:       AAC, verified muxed
Temporary frames:  0 after successful mux
```

The preview pipeline must remain bounded. Do not start a full 7-minute render during ordinary testing.

---

## 9. Reported test results

### Backend

```text
224 passed
Ruff clean
mypy clean across 51 files
pip check clean
```

### Frontend

```text
29 tests passed
ESLint passed
TypeScript typecheck passed
Production build passed
```

### Blender

```text
9 pure-Python tests passed
Headless scene and sample-render contract passed
```

### Browser and workflow

```text
Chromium E2E: 1 passed
PowerShell 5.1 parser/runner contracts passed
Combined Compose configuration passed
git diff --check passed
```

Reported exact command families included:

```text
backend\.venv\Scripts\python.exe -m pytest
backend\.venv\Scripts\python.exe -m ruff check .
backend\.venv\Scripts\python.exe -m mypy app
backend\.venv\Scripts\python.exe -m pip check

npm.cmd test -- --run
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build

backend\.venv\Scripts\python.exe -m pytest .\blender\tests
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\test-run-trackprompt-runner.ps1

docker compose -f compose.yaml -f compose.full-gpu.yaml config --quiet

$env:E2E_BASE_URL='http://127.0.0.1:5173'
npm.cmd run test:e2e

git diff --check
```

---

## 10. Important runner behavior

`run-trackprompt-to-blender.ps1` is the canonical operator entrypoint.

It must continue to:

1. validate Blender;
2. validate Docker/Compose;
3. check API health;
4. inspect live OpenAPI;
5. detect missing visual-cue/config routes or stale preset capability metadata
   even when the backend is healthy;
6. rebuild/recreate the backend at most once automatically;
7. honor `-BuildStack` even when health succeeds;
8. upload the supplied audio;
9. save the fresh job ID immediately;
10. poll only that fresh job;
11. export analysis and visual cues;
12. resolve and persist a typed visualizer configuration;
13. validate cue privacy and curve integrity;
14. call Blender only after cue/config validation;
15. verify `.blend` and manifest output;
16. enforce six ordered Space Journey roles and verified H.264/AAC media;
17. render only a bounded preview;
18. preserve the TrackPrompt job by default;
19. never remove named volumes.

Never regress to manually reusing an old job ID.

---

## 11. Known operational limits

- Demucs, CLAP, lyrics, and local prompt-writer models must already be provisioned.
- `-BuildStack` rebuilds current source; it intentionally does not redownload model weights.
- `abstract-geometry` remains the default; `space-journey` is the second
  registered preset.
- Preview rendering is bounded to approximately 10 seconds.
- No full-track render starts automatically. Characters, lip sync, and a
  photoreal narrative film remain out of scope.
- Blender 5.2's compositor-node-group path is supported and verified with
  bounded Fog Glow. Unsupported Blender compositor APIs still report
  `controlled_compositor_glow_unavailable` and retain the material fallback.
- Existing unrelated worktree changes were preserved.
- Nothing was staged or committed by the latest Codex run.
- The worktree may still be dirty.
- Local paths are Windows-specific.
- Docker, Blender, FFmpeg, model volumes, and the original WAV are local dependencies.
- Space Journey passed iterative authored still review, native 640×360 motion
  review, verified mux probing, and a direct comparison with the preceding
  premium version. The cinematic pass is emotionally stronger and ready for
  artistic review, but is not represented as a final mastered YouTube render.

---

## 12. Safety and preservation rules

Do not run:

```text
docker compose down --volumes
git reset --hard
git clean -fd
```

without explicit operator approval.

Do not delete:

```text
trackprompt-data volume
Ollama model volume
deep-models/
completed job folders
real WAV files
generated .blend files
```

Do not commit:

```text
real audio
private transcripts
model weights
test-output/
*.blend
*.blend1
preview media
setup logs
```

Review `.gitignore` before committing.

---

## 13. First checks on the next session

Run:

```powershell
Set-Location "C:\Users\theon\GitHub\TrackPrompt-Studio"

git status --short

docker compose `
  -f compose.yaml `
  -f compose.full-gpu.yaml `
  ps

Invoke-RestMethod http://127.0.0.1:8000/api/health |
  ConvertTo-Json -Depth 30

Invoke-RestMethod http://127.0.0.1:8000/api/capabilities |
  ConvertTo-Json -Depth 50
```

Then inspect the latest successful run:

```powershell
Get-Content `
  .\test-output\system-runs\run-20260719-151848-5ba6f8d7\run-manifest.json `
  -Raw
```

Do not rerun the full setup installer merely to begin work.

---

## 14. Recommended next milestone

The strongest next step is:

```text
Space Journey artistic approval
+ optional bounded palette/parameter comparison
+ explicit final-render preparation after approval
```

Suggested focus:

1. Review the latest six stills and 10-second clip with the artist.
2. Compare only bounded configuration revisions such as palette, glow, fog,
   ring occlusion, and camera distance; keep each revision deterministic.
3. Keep the verified Blender 5.2 compositor-node-group glow path and the
   material fallback intact.
4. Render any higher-resolution hero still only after artistic approval.
5. Keep `abstract-geometry`, cue schema `1.1.0`, `TP_AUDIO_BUS`, fresh-job
   capture, privacy validation, and named-volume preservation unchanged.
6. Create any final-quality/full-track command as a separate explicit operator
   action only after artistic approval; never make it the default runner path.

---

## 15. Concise resume instruction for Codex

```text
Read the repository documentation and this handoff before changing anything.

The TrackPrompt-to-Blender whole-system runner, typed visualizer configuration,
stale-backend recovery, visual cue export, Abstract Geometry, Space Journey,
six-role still review, and verified bounded preview are working.

Preserve the canonical runner and all current contracts. First inspect git
status, service health, the latest successful run manifest, and existing tests.

The next intended milestone is the operator's Space Journey artistic decision
and, if approved, an explicit final-render handoff. Do not regress the Abstract
Geometry default, cue/config
schemas, private visual features, model volumes, strict preview evidence, or the
fresh-job workflow. Do not render the complete track without a separate
explicit operator decision.
```
