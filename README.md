# TrackPrompt Studio

TrackPrompt Studio is a local-first web application that turns a permitted audio
file into a transparent music-analysis report, an editable arrangement timeline,
and deterministic prompts suitable for pasting into Suno. It describes musical
and production characteristics; it is not intended to reproduce the recording's
identity, exact melody, lyrics, or full chord sequence.

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

The browser experience has four working areas:

1. **Upload:** drag or choose a file, confirm that you may analyze it, choose Fast
   or Deep, and review the configured limits and network status.
2. **Progress:** follow backend-reported stages, cancel an active job, and recover
   from structured validation or analysis errors.
3. **Results:** inspect confidence-labelled analysis, play the waveform, seek to
   detected sections, edit guarded section labels/bounds, and edit, disable, or
   restore findings.
4. **Prompt:** choose intent and length, adjust overrides and exclusions, inspect
   phrase rationale, copy an editable prompt, or export JSON/Markdown.

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
- The prompt composer excludes source filenames, private media tags, lyrics,
  exact melody data, and the complete chord sequence.
- Delete removes one job's upload, derivatives, stored analysis, and live state.
  Every job expires `JOB_TTL_MINUTES` after creation.
- Docker storage is persistent until its named volume is explicitly removed.

See [docs/privacy.md](docs/privacy.md) for the storage and deletion model.

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

### Verification record (2026-07-15)

This is a machine-specific snapshot, not a substitute for rerunning the commands
above. The environment was Windows build 26200 with PowerShell 5.1, Python
3.12.13 in `backend/.venv`, Node.js 24.16.0, npm 11.13.0, and FFmpeg/ffprobe
8.1.1, with Docker Compose 5.2.0. For the backend integration run,
`FFMPEG_PATH` and `FFPROBE_PATH` pointed to the installed real 8.1.1
executables. The final Docker lifecycle check also verified complete teardown
of each synthetic job through both the API and its private job directory. The
final Deep profile was intentionally left healthy on the loopback-only ports.

| Working directory | Exact command | Outcome |
| --- | --- | --- |
| Repository root | `.\backend\.venv\Scripts\python.exe tools\generate_test_audio.py --output-dir test-fixtures` | Generated 34 ignored, synthetic-only fixtures. |
| `backend/` | `$env:FFMPEG_PATH='C:\Users\theon\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe'; $env:FFPROBE_PATH='C:\Users\theon\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffprobe.exe'; .\.venv\Scripts\python.exe -m pytest --basetemp .pytest_final_05 -q` | 114 passed in 13.32 s; one non-failing Starlette TestClient deprecation warning. |
| `backend/` | `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed. |
| `backend/` | `.\.venv\Scripts\python.exe -m mypy app` | Success with no issues in 21 source files. |
| `frontend/` | `npm.cmd test -- --run` | 15 tests passed in 12.03 s. |
| `frontend/` | `npm.cmd run lint` | Passed with zero warnings allowed. |
| `frontend/` | `npm.cmd run typecheck` | Both application and Node/Vite TypeScript projects passed. |
| `frontend/` | `npm.cmd run build` | Production build passed in 3.02 s: 1,588 modules; CSS 35.78 kB (7.90 kB gzip); JavaScript 289.98 kB (87.18 kB gzip). |
| Repository root | `docker compose config --quiet` and `docker compose -f compose.yaml -f compose.deep.yaml config --quiet` | Both the true Fast default and Deep override models rendered successfully. The sandboxed CLI also emitted a non-failing access warning for this machine's user-level Docker config file. |
| Repository root | `docker compose -f compose.yaml -f compose.deep.yaml up --build -d` | Final Deep images built and both services became healthy on loopback at `127.0.0.1:8000` and `127.0.0.1:5173`. Capabilities reported Torch 2.13.0, CUDA build support true, CUDA runtime false, selected device CPU, and no fallback. |
| Repository root | local multipart upload/poll/delete against `/api/analyses` | The 16-second synthetic 133 BPM fixture completed in 27.6 s with requested/effective mode `deep`, Demucs four-stem evidence, 133.3 BPM, 35 beats, 35 onsets, one section, and no invariant warnings. No stem files remained before deletion; DELETE returned 204, the subsequent GET returned 404, and the UUID job directory was absent. |
| `frontend/` | `$env:E2E_BASE_URL='http://127.0.0.1:5173'; npm.cmd run test:e2e` | One Chromium scenario passed in 10.4 s (9.4 s test): upload, Fast analysis, section correction, prompt composition, copy, and complete delete against the final rebuilt stack. |
| Repository root | `$env:FFMPEG_PATH=...; $env:FFPROBE_PATH=...; .\backend\.venv\Scripts\python.exe scripts\diagnose_analysis.py test-fixtures\133bpm_click.wav --json` | Real decode path reported 133.3 BPM, 35 beats/onsets, one stable 16-second neutral section, no invariant warnings, and an ambiguity-safe prompt. |

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
- GPU-aware Demucs selection is available, but the stock Compose profile does
  not require an NVIDIA runtime and the setup script intentionally installs a
  CPU PyTorch build. Lyrical analysis and external LLM polishing are not
  implemented.
- The application generates prompts for manual copy/paste; it does not connect to
  or automate Suno.

More detail is available in [docs/architecture.md](docs/architecture.md),
[docs/analysis-methods.md](docs/analysis-methods.md), and
[docs/model-licenses.md](docs/model-licenses.md).
