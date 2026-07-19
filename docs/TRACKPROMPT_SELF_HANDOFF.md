# TrackPrompt Studio — Self-Handoff

**Date:** 2026-07-19  
**Repository:** `C:\Users\theon\GitHub\TrackPrompt-Studio`  
**Status:** Full TrackPrompt → cue-sheet → Blender scene → bounded preview workflow reported successful end to end.

> Verification note: the completion details below come from the latest Codex run and user-provided output. They were not independently rerun from this ChatGPT environment. On the next work session, preserve the working state and verify only the specific area being changed.

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
- Bounded preview stills and 10-second MP4 preview with muxed audio.
- Cached no-rebuild runner path.
- Controlled stale-backend recovery path.
- Backend, frontend, Blender, PowerShell, Compose, and E2E checks.

The original failure was a healthy but stale backend image whose live OpenAPI did not expose the visual-cue routes. The canonical runner now detects this and can rebuild/recreate the backend without deleting named volumes.

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
  -ConfirmPermission `
  -ConfirmLyricsConsent
```

### Build scene without rendering the preview

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\Users\theon\OneDrive\Desktop\Gratis Project\DJ WaZaHaKa - Trip to Andromeda.wav" `
  -BlenderExe "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
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

The current preview image shows a central wireframe-like sphere surrounded by multiple bright orbital rings against a dark background. The scene is functioning, though the visual framing is still MVP-level: some rings cross heavily in front of the central object and could benefit from camera, thickness, palette, and depth tuning in the next visual-quality pass.

---

## 8. Preview output

Reported preview validation:

```text
Stills:      6
Resolution:  640x360
Clip:        10 seconds
Codec:       H.264
Audio:       AAC muxed
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
5. detect missing visual-cue routes even when the backend is healthy;
6. rebuild/recreate the backend at most once automatically;
7. honor `-BuildStack` even when health succeeds;
8. upload the supplied audio;
9. save the fresh job ID immediately;
10. poll only that fresh job;
11. export analysis and visual cues;
12. validate cue privacy and curve integrity;
13. call Blender only after cue validation;
14. verify `.blend` and manifest output;
15. render only a bounded preview;
16. preserve the TrackPrompt job by default;
17. never remove named volumes.

Never regress to manually reusing an old job ID.

---

## 11. Known operational limits

- Demucs, CLAP, lyrics, and local prompt-writer models must already be provisioned.
- `-BuildStack` rebuilds current source; it intentionally does not redownload model weights.
- The implemented visual preset is only `abstract-geometry`.
- Preview rendering is bounded to approximately 10 seconds.
- No narrative scene generation, characters, lip sync, or photoreal environment exists yet.
- Existing unrelated worktree changes were preserved.
- Nothing was staged or committed by the latest Codex run.
- The worktree may still be dirty.
- Local paths are Windows-specific.
- Docker, Blender, FFmpeg, model volumes, and the original WAV are local dependencies.
- The current visual result is technically successful but still needs an authored visual-quality pass.

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
  .\test-output\system-runs\run-20260719-000700-4db9fbe7\run-manifest.json `
  -Raw
```

Do not rerun the full setup installer merely to begin work.

---

## 14. Recommended next milestone

The strongest next step is:

```text
Space Journey preset
+ visual quality controls
+ MCP revision workflow
+ user-facing preset parameters
```

Suggested focus:

1. Preserve the cue exporter, audio bus, and whole-system runner unchanged.
2. Add a second preset rather than replacing `abstract-geometry`.
3. Create a cinematic space-flight scene suited to long electronic tracks.
4. Add bounded parameters:
   - camera distance;
   - camera orbit speed;
   - ring thickness;
   - ring occlusion;
   - palette;
   - glow strength;
   - shard density;
   - fog depth;
   - bass response;
   - drum response;
   - vocal response.
5. Improve framing so foreground rings do not obscure the core excessively.
6. Render representative stills before any preview clip.
7. Use MCP only for bounded parameter revisions, not individual keyframes.
8. Never begin a full-track render automatically.

---

## 15. Concise resume instruction for Codex

```text
Read the repository documentation and this handoff before changing anything.

The TrackPrompt-to-Blender whole-system runner, stale-backend recovery, visual
cue export, abstract-geometry scene, and bounded preview are already working.

Preserve the canonical runner and all current contracts. First inspect git
status, service health, the latest successful run manifest, and existing tests.

The next intended milestone is a new Space Journey preset and visual-quality
tuning. Do not regress the abstract-geometry preset, cue schema, private visual
features, model volumes, or the fresh-job workflow. Do not render the complete
track during development.
```
