# TrackPrompt Studio

TrackPrompt Studio is a local-first web application that turns a permitted audio
file into a transparent music-analysis report, an editable arrangement timeline,
and validated local prompt candidates suitable for pasting into Suno, with a
deterministic Reliable mode. It describes musical and production
characteristics; it is not intended to reproduce the recording's identity,
exact melody, lyrics, or full chord sequence.

The primary workflow is local and does not require a Suno account, an external
AI API, telemetry, or an internet connection after dependencies and container
images have been installed.

> Audio-analysis results are estimates. Tempo, key, section labels, instruments,
> vocal presence, and production descriptors can be wrong, especially for short,
> quiet, noisy, polyphonic, rubato, or experimental material. Review and edit the
> findings before using a generated prompt.

TrackPrompt Studio is an independent project and is not affiliated with,
endorsed by, or sponsored by Suno.

## UI at a glance

The browser experience has five working areas:

1. **Upload:** drag or choose a file, confirm that you may analyze it, choose Fast
   or Deep, and review the configured limits and network status.
2. **Progress:** follow backend-reported stages, cancel an active job, and recover
   from structured validation or analysis errors.
3. **Results:** inspect confidence-labelled analysis, play the waveform, seek to
   detected sections, edit guarded section labels/bounds, and edit, disable, or
   restore findings.
4. **Prompt:** choose intent and length, adjust overrides and exclusions, inspect
   phrase rationale, copy an editable prompt, or export JSON/Markdown.
5. **Blender Visualizer:** choose an FPS and curve-detail budget, include or
   exclude beats/onsets/curves, and download a minimized Blender-ready cue sheet.
   This button does not launch Blender or expose the source audio path.

**Accept** persists an explicit review decision on a fact. It does not change the
detected value or analyzer confidence, but it lets an otherwise low-confidence
fact pass the prompt evidence gate; **Disable for prompt** still takes precedence.
**Unaccept**, **Edit**, **Disable for prompt**, **Use in prompt**, and **Restore
detected** also persist analysis changes. Editing or restoring a fact clears its
accepted marker unless that same API update explicitly re-accepts it. Every
analysis PATCH, including Accept/Unaccept, invalidates the
server's generated-prompt snapshot, so GET/export omits that prompt until it is
regenerated. Text typed directly in the prompt editor remains browser-local and
is not silently written into server exports.

Generated Reliable, Creative, and Experimental candidate packages are stored in
the private job directory. Choosing **Use this prompt** persists only that
already-generated candidate as the selected primary prompt, so job reload and
JSON/Markdown exports agree with the visible selection. Freeform edits in the
prompt textarea remain browser-local and require no hidden server write.
Reliable is deterministic. Creative and Experimental send only bounded reviewed
evidence to the private local Ollama service and request one or three distinct
candidates. Validation allows one complete-set model repair, then a bounded
deterministic safety repair may insert only missing exact reviewed evidence;
every repaired candidate still passes the normal privacy, contradiction,
diversity, and length validators. Remaining failures produce a declared
Reliable fallback. Genre use has separate acceptance-required modes and a
`Layered detected evidence` mode that can describe production and vocal
influences separately with ambiguity retained. Raw lyrics and rejected,
disabled, or otherwise ineligible detected genres remain outside sampled prompt
evidence. Persisted `factsUsed` entries contain a safe path, aggregate value, and
role; Creative/Experimental selection retains the arrangement blueprint and
rationale instead of blanking them.

Deep genre analysis keeps the listener-facing full mix separate from a private,
temporary accompaniment view made only from drums, bass, and other stems. Vocal
delivery is classified acoustically without requiring transcript confidence.
Results therefore report primary/secondary production genre, vocal delivery,
vocal genre influence, section-level influence, and an overall layered blend.
The accompaniment view and Demucs stems are deleted and never exported.

Timeline section cards separately support neutral label and numeric-boundary
edits, use/exclusion of the corrected label in the arrangement blueprint, and
restoration of the detected label/start/end. Excluding a correction removes its
semantic-label influence while preserving that section's timing under the
generic label `section`. Editable labels are restricted to neutral arrangement
terms such as intro, verse, chorus, bridge, build, outro, or section A. Bounds
must stay finite, ordered, non-overlapping, and within the adjacent sections and
track. Section fields do not use the fact-acceptance flag; their edits still
invalidate the stored prompt snapshot.

## Privacy summary

- The browser talks only to the local FastAPI service.
- Uploaded and derived audio stays in the configured data directory. If the
  optional Deep adapter is explicitly enabled, its temporary stems stay there
  too and are deleted immediately after feature extraction.
- Normal analysis makes no outbound requests and includes no analytics.
- Internal paths use UUID job IDs rather than source filenames.
- The prompt composer excludes source filenames, private media tags, raw lyrics,
  exact melody data, and the complete chord sequence.
- Likely lyric hallucinations remain reviewable only in the private transcript;
  they cannot drive themes or section activity. Generated abstract themes require
  explicit user approval before prompt use.
- Delete removes one job's upload, derivatives, stored analysis, and live state.
  Every job expires `JOB_TTL_MINUTES` after creation.
- Docker storage is persistent until its named volume is explicitly removed.

See [docs/privacy.md](docs/privacy.md) for the storage and deletion model.

## Blender Visualizer MVP

Completed analyses can export the versioned `TrackPromptVisualCueSheet 1.1.0`.
It combines deterministic frame timing, beats, onsets, sections, transitions,
and bounded normalized continuous controls without exporting filenames, raw
lyrics, server paths, full waveforms, stems, or prompt internals. Fast mode
provides six full-mix curves; successful Deep mode adds shared-normalized drum,
bass, vocal, and other curves before temporary stems are deleted.

The repository includes a reusable Blender Python importer and one deterministic
procedural `abstract-geometry` preset. It builds `TP_AUDIO_BUS` F-curves,
timeline markers, nine predictable collections, an Eevee preview scene, a
contract-checked build manifest, representative stills, and a short preview clip
whose duration and mux status are verified with local ffprobe. Blender receives
the original local audio as a separate explicit input; no audio is copied into
the cue export. The documented smoke path uses only deterministic signals from
`tools/generate_test_audio.py`.

See:

- [Cue-sheet schema and DSP methods](docs/blender-visual-cue-sheet.md)
- [Blender build and preview guide](docs/blender-visualizer-mvp.md)
- [Codex/Blender-MCP workflow](docs/codex-blender-mcp-preview.md)

On Windows, `run-trackprompt-to-blender.ps1` is the canonical whole-system
entrypoint. It uploads the permitted file, saves and polls the job, exports the
analysis/cues, builds Blender, and validates the bounded preview in one recorded
run. Common forms are:

The Deep/genre/lyrics examples assume `setup-full-gpu.ps1` has already
provisioned the reviewed local model volumes. `-BuildStack` rebuilds current
source images; it does not install or redownload model caches.

```powershell
# Rebuild current source, then run.
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\absolute\track.wav" `
  -ConfirmPermission -ConfirmLyricsConsent -BuildStack

# Reuse cached images; a healthy-but-stale backend is repaired at most once.
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\absolute\track.wav" `
  -ConfirmPermission -ConfirmLyricsConsent

# Build the validated scene but skip preview media.
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\absolute\track.wav" `
  -ConfirmPermission -ConfirmLyricsConsent -SkipPreview

# Run without lyrics or transcript consent.
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\run-trackprompt-to-blender.ps1 `
  -AudioPath "C:\absolute\track.wav" `
  -ConfirmPermission -EnableLyrics:$false
```

Add `-AutoRebuildStaleBackend:$false` when automatic image building is not
allowed; the runner then stops with source/live diagnostics if routes are stale.

Artifacts live under `test-output\system-runs\<timestamp>\`; each folder has a
`run-manifest.json`, job/export evidence, cue sheet, `.blend`, build manifest,
and preview manifest/media unless preview was skipped. The newest preserved job
ID is also in `test-output\last-trackprompt-job-id.txt` and can be inspected with:

```powershell
$jobId = (Get-Content test-output\last-trackprompt-job-id.txt -Raw).Trim()
Invoke-RestMethod "http://127.0.0.1:8000/api/analyses/$jobId" | ConvertTo-Json -Depth 100
```

The older `build-trackprompt-visualizer.ps1` job-ID handoff is retained only for
targeted recovery. Do not use it as the routine workflow because it separates
the Blender artifacts from the upload/poll provenance. The Blender guide also
contains the complete generated-audio smoke commands for a copyright-free run.

## Prerequisites

### Docker path

- Docker Desktop on Windows/macOS, or Docker Engine on Linux
- Docker Compose v2 (`docker compose version`)
- Enough free space for base images, dependencies, and uploaded working data

Windows users can run the Docker path directly in PowerShell. WSL 2 integration
also works, but keep the repository in the WSL filesystem for better file-system
performance when developing inside WSL.

### Direct development path

- Python 3.11 through 3.13
- Node.js 20 or newer and npm
- FFmpeg and ffprobe available on `PATH`

Confirm the native tools before starting:

```text
python --version
node --version
npm --version
ffmpeg -version
ffprobe -version
```

On Windows, `py -3.11` may be used instead of `python`. On macOS, Homebrew can
install `python@3.11`, `node`, and `ffmpeg`. On Debian/Ubuntu and WSL, install
Python, its venv module, Node/npm, and FFmpeg with the system package manager.

## Start with Docker

From the repository root:

```bash
docker compose up --build
```

Open <http://localhost:5173>. The API is also bound to loopback at
<http://localhost:8000/api/health>; API documentation is available at
<http://localhost:8000/docs>.

The first build needs network access to download container images and language
packages. That is installation traffic, not analysis traffic. Runtime uploads
and analysis remain local.

To reduce backend dependency drift, `backend/Dockerfile` pins its Python 3.12
base image by digest and installs the exact Linux dependency set in
`backend/requirements.lock.txt`. The adjacent `requirements.txt` remains the
human-maintained compatibility policy; review and refresh the lock whenever that
policy changes.

Stop with `Ctrl+C`, then remove the stopped containers while preserving data:

```bash
docker compose down
```

Optional configuration can be copied before startup:

```bash
cp .env.example .env
```

PowerShell equivalent:

```powershell
Copy-Item .env.example .env
```

Docker Compose reads root `.env` automatically. It always stores runtime data at
`/data` in the `trackprompt-data` named volume; the host path values in
`.env.example` are for direct startup.

## Start directly

Create one environment and install dependencies.

### macOS, Linux, or WSL

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
cd backend
python -m pip install -e ".[dev]"
cd ../frontend
npm ci
cd ..
```

Start the API in terminal 1, keeping data at the repository root:

```bash
source backend/.venv/bin/activate
export TRACKPROMPT_DATA_DIR="$PWD/.trackprompt-data"
export MODEL_CACHE_DIR="$TRACKPROMPT_DATA_DIR/models"
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the UI in terminal 2:

```bash
cd frontend
npm run dev
```

### Windows PowerShell

```powershell
py -3.11 -m venv backend\.venv
.\backend\.venv\Scripts\Activate.ps1
Set-Location backend
python -m pip install -e ".[dev]"
Set-Location ..\frontend
npm.cmd ci
Set-Location ..
```

Start the API in terminal 1:

```powershell
.\backend\.venv\Scripts\Activate.ps1
$env:TRACKPROMPT_DATA_DIR = Join-Path (Get-Location) ".trackprompt-data"
$env:MODEL_CACHE_DIR = Join-Path $env:TRACKPROMPT_DATA_DIR "models"
Set-Location backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the UI in terminal 2:

```powershell
Set-Location frontend
npm.cmd run dev
```

The frontend development server proxies `/api` to `http://127.0.0.1:8000`.
Root convenience commands are available through `make help` and
`.\tasks.ps1 help`. If Windows execution policy blocks the latter after you have
reviewed the repository script, use the process-scoped form:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tasks.ps1 help
```

## Supported inputs

The accepted families are WAV, FLAC, MP3, M4A/AAC, and OGG. The backend does not
trust the extension or browser MIME type: it inspects the actual container and
audio stream with ffprobe, then decodes with FFmpeg. A codec must also be present
in the installed FFmpeg build. Browser playback support can be narrower than
backend decoding support.

After a bounded upload is stored, the route performs a bounded ffprobe preflight
before accepting it. Unreadable or unsupported content returns a safe structured
422 after its private job directory/metadata are removed; no job ID is exposed.
If cleanup itself cannot complete, the API returns a structured 503 with a
retry-delete path instead of claiming the 422 cleanup succeeded.
Accepted media returns the queued job with HTTP 202. The background
**Validating** stage deliberately performs a second, fresh, cancellable ffprobe
so the visible stage represents real validation and guards against a changed or
unreadable stored source before decoding.

Defaults are 200 MiB per upload and 1,200 seconds (20 minutes) per file. Empty,
malformed, unsupported, over-limit, and effectively silent inputs receive a safe
error or an insufficient-signal result rather than fabricated musical claims.
The production nginx proxy independently caps request bodies at 202 MiB by
default, leaving modest multipart overhead while protecting the proxy before the
FastAPI stream limit runs. If `MAX_UPLOAD_MB` changes for Compose, review
`NGINX_UPLOAD_LIMIT` too; the backend limit remains authoritative for the audio
file itself.

## Analysis modes

### Fast

Fast mode is the offline CPU baseline. It uses signal processing rather than
large downloaded model weights for media inspection, waveform peaks, signal
quality, rhythm/tempo, approximate key and harmony, structure, timbre, stereo
and production measures, and deterministic prompt composition.

### Deep

Deep is a capability-gated, local Demucs four-stem adapter with CPU support and
optional CUDA selection when a compatible PyTorch build and container/runtime
make a GPU genuinely available. It is
disabled by default, and this repository bundles neither the Demucs package nor
model weights. When explicitly ready, it separates temporary vocals, drums,
bass, and other stems, derives coarse global and section-aligned relative-energy
instrumentation and vocal-presence evidence, and deletes the stems immediately.
If the adapter is not ready
or fails, the requested job retains the Fast result and reports the fallback; it
never invents stem or model output.

Deep becomes ready only when all of these conditions are true:

1. `ENABLE_DEMUCS=true` is set before the backend starts;
2. the optional `demucs>=4.0.1,<5` package is installed in that backend
   environment; and
3. the selected model has a complete `MODEL_CACHE_DIR/demucs-models.json`
   manifest, and every other regular file anywhere under `MODEL_CACHE_DIR`
   exactly matches its listed SHA-256.

Before opting in, review the exact checkpoint source, license, training-data
terms, and intended-use restrictions. TrackPrompt Studio does not approve a
checkpoint. From `backend/`, a direct-development environment may install the
optional code and its substantial transitive dependencies with:

```bash
python -m pip install -e ".[dev,deep]"
```

That command installs code only; it does not install or approve weights. Place
separately obtained, reviewed, compatible files in `MODEL_CACHE_DIR`, then create
`MODEL_CACHE_DIR/demucs-models.json` with relative paths and the actual SHA-256 of
every file in that local repository. For example:

```json
{
  "models": {
    "htdemucs": {
      "files": {
        "htdemucs.th": "<64-character SHA-256 digest>"
      }
    }
  }
}
```

The manifest must be a regular file directly at the cache root, no larger than
1,000,000 bytes, and its model key must equal `DEMUCS_MODEL_NAME`. Each entry
uses a relative path without `..` and an exact 64-hex-character SHA-256. Every
other regular file under the cache must appear in that selected model's `files`
map—including checkpoint, configuration, and nested files. Any missing,
unmanifested, changing-during-verification, or hash-mismatched file keeps Deep
unavailable. Hash results are cached by file path, size, modification time, and
change time, then recomputed when that signature changes.
The manifest is an integrity allowlist, not license approval; independently
review every listed checkpoint and its training-data terms.

Set the two Deep variables, restart the backend, and confirm readiness at
`/api/capabilities`. Analysis invokes Demucs with its local-repository option and
offline environment flags, so startup and analysis do not fetch a checkpoint.
The capability API conservatively discloses up to about 5 GB of disk impact. See
[docs/model-licenses.md](docs/model-licenses.md) before enabling it.

The stock backend Docker image intentionally installs only Fast-mode
requirements. Compose passes the Deep variables for configuration parity, but
setting `ENABLE_DEMUCS=true` in that image is not enough: Deep still falls back
unless you deliberately build a reviewed custom backend image with the optional
package and pre-populate `/data/models` with a complete reviewed repository plus
`demucs-models.json`, with no extra files. The provided Docker build never
installs that large dependency or downloads weights silently.

## Full GPU profile

The reviewed full-GPU profile adds CUDA PyTorch, Demucs, the local CLAP genre
tagger, the local faster-whisper lyrics adapter, and the private Ollama prompt
writer. Model installation is an explicit setup action; track analysis remains
offline and never initiates a model download. Read
[docs/model-licenses.md](docs/model-licenses.md) before accepting the model
terms.

On Windows PowerShell 5.1, the canonical first setup command is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\setup-full-gpu.ps1 `
  -AcceptAllReviewedModelTerms
```

When the images and model volumes are already installed, the normal recovery or
relaunch command reuses them without rebuilding or reinstalling:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\setup-full-gpu.ps1 `
  -AcceptAllReviewedModelTerms `
  -SkipBuild `
  -SkipModelInstall
```

The installer validates Docker, NVIDIA, Compose, dependency integrity, fresh-
process imports, CUDA/CTranslate2, Demucs, genre, lyrics, prompt-writer, and
combined capabilities. It starts the prompt writer, backend, and frontend in
that order and waits for each service. `-ForceDownload` is opt-in and cannot be
combined with `-SkipModelInstall`; normal recovery does not redownload model
files. `-NoStart` runs provisioning and diagnostics without leaving newly
started application services running, and `-NoBrowser` suppresses the browser.

Run the import diagnostic directly in an isolated backend container with:

```powershell
docker compose `
  -f compose.yaml `
  -f compose.full-gpu.yaml `
  run --rm --no-deps `
  backend `
  python -m app.diagnostics.imports
```

Verify an already-running full stack, including tiny local inference and the
synthetic Deep/genre/lyrics flow plus persisted Reliable, Creative, and
Experimental candidates, with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\verify-full-gpu.ps1
```

`docker compose -f compose.yaml -f compose.full-gpu.yaml down` removes
containers and networks but preserves the `trackprompt-data` and
`trackprompt-ollama` volumes. Do not add `--volumes` during setup, recovery, or
ordinary shutdown. The complete-deletion command later in this README is the
only documented flow that intentionally removes those caches and private data.

The full-GPU dependency set deliberately pins NumPy `2.4.6`: the reviewed
`librosa==0.11.0` path installs Numba with a `<2.5` NumPy constraint. Pinning the
compatible version in the base lock prevents optional installation from silently
replacing a different locked NumPy. Image build and verification both run
`python -m pip check` plus the direct-pin diagnostic.

## Configuration

All limits are validated as positive integers. Paths are filesystem paths or
executable names, never shell command fragments.

| Variable | Default | Purpose |
| --- | ---: | --- |
| `FRONTEND_PORT` | `5173` | Compose host port for the UI. |
| `BACKEND_PORT` | `8000` | Compose host port for direct API access. |
| `TRACKPROMPT_DATA_DIR` | `.trackprompt-data` | Direct-start upload, derivative, and SQLite root. Compose overrides this with `/data`. |
| `MODEL_CACHE_DIR` | `<data-dir>/models` | Optional local model cache containing `demucs-models.json` when Deep is configured. Compose overrides this with `/data/models`. |
| `ENABLE_DEMUCS` | `false` | Explicitly opt in to the local Demucs adapter; package and reviewed cached weights are also required. |
| `DEMUCS_MODEL_NAME` | `htdemucs` | Safe local model identifier and matching key in `demucs-models.json`. |
| `DEMUCS_DEVICE` | `auto` | `auto`, `cpu`, or `cuda`; `auto` selects CUDA only when build and runtime checks pass, and CPU remains the safe fallback. |
| `GPU_TASK_WORKERS` | `1` | Shared concurrency bound for Demucs, lyrics, genre, and sampled local-prompt GPU work. One preserves the 12 GB VRAM-safe serialized policy. |
| `ENABLE_GENRE_TAGGER` / `GENRE_DEVICE` | `false` / `auto` | Explicitly enable the checksum-manifested offline CLAP adapter and select `auto`, `cpu`, or `cuda`. Model ID/revision remain pinned by `GENRE_MODEL_ID` / `GENRE_MODEL_REVISION`. |
| `ENABLE_LYRICS_ADAPTER` / `LYRICS_DEVICE` | `false` / `auto` | Explicitly enable the checksum-manifested faster-whisper adapter and select its device. `LYRICS_COMPUTE_TYPE` defaults to `float16`; CPU fallback remains opt-in. |
| `ENABLE_LOCAL_PROMPT_WRITER` | `false` | Explicitly enable Creative/Experimental requests to the internal Ollama service after its configured model/digest is present. |
| `LOCAL_LLM_ENDPOINT` / `LOCAL_LLM_TIMEOUT_SECONDS` | internal Ollama URL / `90` | Private writer endpoint and bounded generation timeout. `LOCAL_LLM_KEEP_LOADED=false` releases the model after each task. |
| `PROMPT_WRITER_DEVICE` | `cuda` | Truthful device label for the reviewed local writer capability; full-GPU verification also checks returned model provenance. |
| `MAX_UPLOAD_MB` | `200` | Maximum streamed upload size in MiB. |
| `NGINX_UPLOAD_LIMIT` | `202m` | Production proxy body cap in nginx size syntax; includes multipart headroom and applies only to Compose/nginx. |
| `MAX_DURATION_SECONDS` | `1200` | Maximum probed audio duration. |
| `JOB_TTL_MINUTES` | `60` | Fixed lifetime from job creation before automatic removal. |
| `ANALYSIS_WORKERS` | `1` | Maximum concurrent analysis workers. One is the safe default for peak decoded/STFT memory. |
| `MAX_PENDING_JOBS` | `2` | Maximum admitted running plus waiting jobs. Direct startup defaults to twice the worker count when unset; Compose passes `2` unless overridden. |
| `FFMPEG_PATH` | `ffmpeg` | FFmpeg executable name or path. |
| `FFPROBE_PATH` | `ffprobe` | ffprobe executable name or path. |
| `SUBPROCESS_TIMEOUT_SECONDS` | `120` | Bound for individual media subprocesses. |
| `ANALYSIS_TIMEOUT_SECONDS` | `600` | Hard bound for the isolated DSP, optional-Deep, and result-serialization worker. Upload/ffprobe validation, FFmpeg decode, and parent-side prompt composition are outside this timer. |
| `CORS_ORIGINS` | localhost and `127.0.0.1` on `5173`/`4173` | Direct-start browser-origin allowlist for mutating API requests and CORS. Compose derives only the `localhost` and `127.0.0.1` origins at `FRONTEND_PORT`. |
| `ALLOWED_HOSTS` | loopback hosts and `testserver` | Direct-start `Host` base allowlist checked on every API request; configured CORS origin hostnames are also accepted. Compose intentionally fixes both sets to loopback. |
| `LOG_LEVEL` | `INFO` | Backend log level. Logs must remain metadata-safe. |
| `VITE_API_BASE_URL` | `/api` | Browser API base. Keep this for the Vite/nginx proxies. |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Vite development proxy target. |

The backend does not load root `.env` itself. For direct startup, export the
variables in the launching shell (or use a trusted environment manager). Vite
settings likewise need to be present in the frontend process environment.

## Verification commands

Generate the deterministic synthetic fixtures from the repository root before
running backend tests:

```bash
python tools/generate_test_audio.py --output-dir test-fixtures
```

On Windows with the documented environment, the equivalent explicit interpreter
is:

```powershell
.\backend\.venv\Scripts\python.exe tools\generate_test_audio.py --output-dir test-fixtures
```

These mathematical test signals are local and ignored by Git; do not substitute
recorded or copyrighted music. `make fixtures` and
`.\tasks.ps1 fixtures -Python backend\.venv\Scripts\python.exe` wrap the same
generator.

For a concise private diagnostic of one local file, run:

```text
python scripts/diagnose_analysis.py <audio-file>
python scripts/diagnose_analysis.py <audio-file> --mode deep --json
```

The command reports decoded range, silence threshold, BPM/grid evidence, key
candidates, sections, Deep readiness/evidence, invariant warnings, prompt text,
and omitted prompt facts. It does not print embedded metadata and removes its
temporary decoded audio and stems.

Then run each independent line from the repository root. These are the required
commands; a command should only be reported as passing after it has completed in
the current environment.

```bash
cd backend && python -m pytest
cd backend && python -m ruff check .
cd backend && python -m mypy app
cd frontend && npm test -- --run
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && npm run build
docker compose config
docker compose -f compose.yaml -f compose.full-gpu.yaml config
```

When Playwright/browser support is installed:

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
```

The E2E command generates a synthetic fixture when needed and starts both local
servers. Backend development dependencies and FFmpeg must already be available.
On Linux, Playwright may additionally require its documented system browser
dependencies. The one-time Chromium runtime is roughly 300 MB. Set
`E2E_BASE_URL` to test an already-running local/Compose UI instead of launching
the two development servers.

Windows PowerShell uses the explicit backend interpreter and `.cmd` shims:

```powershell
Set-Location frontend
npx.cmd playwright install chromium
Set-Location ..
.\tasks.ps1 e2e -Python backend\.venv\Scripts\python.exe
```

Or use `make check` on a Make-capable shell / WSL and
`.\tasks.ps1 check -Python backend\.venv\Scripts\python.exe` on Windows
PowerShell. If local execution policy blocks the script, the equivalent
process-scoped invocation is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tasks.ps1 check -Python backend\.venv\Scripts\python.exe
```

### Verification record (2026-07-18)

This is a machine-specific snapshot, not a substitute for rerunning the commands
above. It was produced on Windows with the repository virtual environment,
installed FFmpeg/ffprobe 8.1.1, Docker Compose, an NVIDIA GeForce RTX 3060, and
the reviewed local model cache. The portable command below uses placeholders;
the run supplied the actual absolute executable paths through the two environment
variables. No private transcript, source identity, or generated real-track
prompt text was printed or added to the repository.

| Working directory | Command or check | Outcome |
| --- | --- | --- |
| `backend/` | `$env:FFMPEG_PATH='<absolute-path-to-ffmpeg>'; $env:FFPROBE_PATH='<absolute-path-to-ffprobe>'; .\.venv\Scripts\python.exe -m pytest -q --basetemp <isolated-temp-directory>` | 200 tests passed in 38.88 s. The only warning was Starlette TestClient's non-failing `httpx` deprecation. |
| `backend/` | `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed. |
| `backend/` | `.\.venv\Scripts\python.exe -m mypy app` | Success with no issues in 42 source files. |
| `backend/` | `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found. |
| `frontend/` | `npm.cmd test -- --run` | 26 tests passed. |
| `frontend/` | `npm.cmd run lint` | Passed with zero warnings allowed. |
| `frontend/` | `npm.cmd run typecheck` | Both application and Node/Vite TypeScript projects passed. |
| `frontend/` | `npm.cmd run build` | Production build passed: 1,590 modules; CSS 37.58 kB (8.33 kB gzip); JavaScript 326.25 kB (95.93 kB gzip). |
| Repository root | `docker compose config --quiet` and `docker compose -f compose.yaml -f compose.full-gpu.yaml config --quiet` | Both the local Fast baseline and full-GPU override rendered successfully. |
| Repository root | `docker compose -f compose.yaml -f compose.full-gpu.yaml build backend frontend` | Both canonical images built successfully; the containerized frontend production build also processed 1,590 modules. |
| Repository root | `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-full-gpu.ps1 -AcceptAllReviewedModelTerms -SkipBuild -SkipModelInstall -NoBrowser` | Provisioning validation passed and the canonical services became healthy. |
| Repository root | canonical backend mount inspection | The baked backend had exactly one `/data` volume mount and no `/app/app` source bind. |
| Repository root | `powershell -NoProfile -ExecutionPolicy Bypass -File .\verify-full-gpu.ps1` | The baked-image verifier passed CUDA/Torch and CTranslate2 checks, Demucs GPU inference with temporary-stem cleanup, CLAP hierarchy/aggregation diagnostics, faster-whisper GPU/privacy checks, local Qwen writer diagnostics, and the complete synthetic Deep workflow. Reliable strict and blend, Creative, and Experimental generation, selection, reload, JSON/Markdown export, privacy scanning, and deletion all passed. |
| Repository root | permitted real-track quality verifier | A 262.031-second local track completed in effective Deep mode with CUDA Demucs, six genre windows, honest low-confidence genre ambiguity, private lyrics review and section mapping, and all four exercised prompt configurations: Reliable strict, Reliable blend, Creative, and Experimental. Every selected candidate persisted identically through reload and both exports, no mode fell back, safety repairs were declared where exact reviewed evidence was initially omitted, and deletion was confirmed. Output remained sanitized. |
| `frontend/` | `npm.cmd run test:e2e` | One Chromium scenario passed in 10.2 s: upload, analysis, section correction, prompt composition, copy, and complete deletion. |

## Calibrated local and NVIDIA Brev rendering

Run `WZHK-Media-Launcher.cmd` for calibrated profile creation, local
preflight/dry-run/render controls, safe stop-after-current-chunk, and the
provider-neutral cloud workflow. Generated profiles, render packages, image
sequences, logs, and private media remain local and ignored by Git.

The cloud path targets full NVIDIA Brev GPU VMs for Blender 5.2 headless
rendering; it does not use NVIDIA NIM inference containers. Brev readiness is
offline and provisioning fails closed until the installed CLI schema has been
inspected, a bounded one-worker benchmark plan is hash/budget bound, and the
operator supplies both confirmations. Private source audio is excluded from
cloud packages and muxed locally after video-only output is verified.

See [render calibration](docs/render-calibration.md),
[render profiles](docs/render-profiles.md),
[local performance mode](docs/local-performance-mode.md),
[cloud rendering](docs/cloud-rendering.md), and
[NVIDIA Brev rendering](docs/nvidia-brev-rendering.md) for the exact workflows
and safety gates.

## Completely delete local data

The UI's **Delete analysis** action is the preferred per-job deletion path. It
removes the uploaded file, decoded/derived files, stored result, and live job
state for that UUID.

For Docker, stop the app and irreversibly remove the project volume:

```bash
docker compose down --volumes
```

For direct startup with the documented root data directory, stop both servers,
verify that the shell is at the repository root, then remove exactly that folder:

```bash
rm -rf .trackprompt-data
```

PowerShell:

```powershell
Remove-Item -LiteralPath .trackprompt-data -Recurse -Force
```

If `TRACKPROMPT_DATA_DIR` or `MODEL_CACHE_DIR` was changed, remove those explicit
locations instead. Deleting containers without `--volumes` does not delete the
Docker named volume. Browser copies, exported reports, and prompts already copied
to another application are outside the backend's control and must be deleted
separately.

## Troubleshooting

- **Health reports FFmpeg unavailable:** ensure both `ffmpeg` and `ffprobe` are on
  `PATH`, or set `FFMPEG_PATH` and `FFPROBE_PATH` to executable files. Restart the
  backend after changing them.
- **The UI cannot reach the API:** check <http://localhost:8000/api/health>, then
  verify the frontend proxy setting, `CORS_ORIGINS`, and any structured
  `host_not_allowed`/`origin_not_allowed` response. Change `ALLOWED_HOSTS` only
  for an intentional trusted direct-start hostname; Compose remains loopback-
  only. Do not point the browser at the Docker-internal hostname `backend`.
- **Port already in use:** set `FRONTEND_PORT` or `BACKEND_PORT` in root `.env`
  for Compose. For direct startup, pass another uvicorn/Vite port and update the
  corresponding proxy/origin.
- **Upload returns 422 despite its extension:** the bounded preflight validates
  actual content, not the filename. Confirm the file contains one supported
  audio stream and that the installed FFmpeg build supports its codec. An
  accepted job repeats that probe during the cancellable **Validating** stage.
- **Compose rejects an otherwise valid large upload:** nginx applies
  `NGINX_UPLOAD_LIMIT` before FastAPI applies `MAX_UPLOAD_MB`. Keep the ingress
  value slightly above the intended backend file limit to allow multipart
  framing, without making it unbounded.
- **Deep falls back:** this is expected in the base installation. Check
  `/api/capabilities` for the exact reason. Readiness requires the explicit
  enable flag, the optional package, and compatible reviewed weights whose
  SHA-256 values match `demucs-models.json`. The stock Docker image intentionally
  omits the package.
- **The full-GPU backend does not become healthy:** rerun the canonical setup
  command with `-SkipBuild -SkipModelInstall`. It prints `compose ps --all`,
  backend logs, container state, and `python -m app.diagnostics.imports` on
  failure. The same diagnostic can be run manually with the command in the Full
  GPU profile section. Do not delete volumes as a startup-repair step.
- **A model diagnostic is unavailable after a rebuild:** inspect
  `/api/capabilities` and run `verify-full-gpu.ps1`. Image rebuilds do not remove
  either named model volume. Use `-ForceDownload` only after an integrity or
  compatibility failure has been established.
- **Analysis queue is full:** the server returns a retryable capacity error once
  `MAX_PENDING_JOBS` running/waiting jobs are admitted. Wait for a job to finish
  or raise the limit only after reviewing memory capacity.
- **Docker data appears after rebuilding:** image rebuilds do not remove the
  named volume. Use the complete-deletion command above only when that is desired.
- **PowerShell blocks a script shim:** the Windows runner uses `npm.cmd`/`npx.cmd`.
  If local policy also blocks `tasks.ps1`, invoke it explicitly with
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tasks.ps1 help`.

## Known limitations

- Fast results are estimates and heuristic descriptors are not calibrated model
  probabilities.
- Half/double-time tempo ambiguity, polyphonic key ambiguity, and neutral section
  labels are expected for difficult material.
- Dense mixes do not support reliable note-for-note melody extraction; weak
  melody claims are omitted from prompts.
- Fast instrumentation and vocal findings are coarse. The optional Deep adapter
  adds only four broad stem categories and relative-energy evidence, not reliable
  specific-instrument recognition.
- The base Compose profile remains CPU/Fast-only. The separate full-GPU profile
  requires a compatible NVIDIA runtime and explicitly provisioned reviewed
  models; genre and lyrics remain estimates, and the local prompt writer can
  still fall back to Reliable deterministic composition after validation or
  runtime failure.
- The application generates prompts for manual copy/paste; it does not connect to
  or automate Suno.

More detail is available in [docs/architecture.md](docs/architecture.md),
[docs/analysis-methods.md](docs/analysis-methods.md), and
[docs/model-licenses.md](docs/model-licenses.md).
