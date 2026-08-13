# Privacy and data lifecycle

## Local-first boundary

TrackPrompt Studio's normal data path is browser to local FastAPI service and
back. The application contains no telemetry, analytics beacon, hosted classifier,
external language-model call, or silent model downloader. Analysis and prompt
composition do not require outbound network requests.

Package installation and Docker image builds can contact package registries.
Those setup operations are distinct from analyzing a track. The browser can also
make ordinary requests initiated by browser extensions or developer tooling;
those are not part of TrackPrompt Studio.

The UI and `/api/capabilities` expose `networkFeaturesEnabled`; it is `false` in
this release.

The optional operator-authorized GCP video path is a distinct exception to the normal-analysis network boundary. Planning, audio analysis, StoryPlan/ShotPlan compilation, timeline work, exports, and assembly remain local. Only sanitized visual prompts, exact video-generation parameters, and a private GCS output prefix are sent after the local operator reviews an immutable plan and enters its exact maximum-spend phrase. The song, lyrics, stems, source filename, local paths, and bearer credentials are never included. `generateAudio` is always false; the original local master is muxed only by FFmpeg after verified clips return.

The local API validates the `Host` of every `/api/` request against
`ALLOWED_HOSTS` plus configured origin hosts. It rejects browser requests marked
cross-site and requires a configured `CORS_ORIGINS` match when a mutating request
supplies `Origin`. These checks supplement the default loopback-only listeners;
they are not a reason to expose the service to a LAN or the internet.

## What is stored

The configured `TRACKPROMPT_DATA_DIR` contains:

- `trackprompt.sqlite3` (plus normal SQLite WAL/SHM sidecars while open):
  job/lifecycle metadata, including a sanitized display name and request flags,
  but no audio or derived analysis;
- `.cancellations/<uuid>.cancel`: transient cancellation markers without audio;
- `jobs/<uuid>/`: `source.bin`, editable and detected analysis JSON, the latest
  prompt/preferences JSON, private `lyrics.json`/`detected-lyrics.json`, a
  text-free lyrics summary, and temporary decoded/derived files;
- `models/` by default: reserved for optional explicitly managed model files and
  their `demucs-models.json` checksum manifest.

Source names are sanitized for display but are not used as disk paths. Job
directories and API identifiers are UUIDs. This release does not request,
display, or persist embedded artist/title/album tags; they are excluded from
classification and prompt generation by construction.

Docker mounts `/data` from the project-scoped `trackprompt-data` named volume.
Direct startup defaults to the repository-root `.trackprompt-data`; the
documented launch commands set the same location explicitly.

On POSIX, the backend enforces owner-only mode `0700` on private data, jobs,
model-cache, cancellation, and stem directories, and `0600` on the SQLite
database/sidecars, uploaded source, and job JSON. Startup or writes fail closed if
those modes cannot be enforced. Windows mode bits do not implement ACL security,
so Windows relies on the inherited ACL of `TRACKPROMPT_DATA_DIR`; choose a
user-private location and retain its restrictive ACL.

## Processing and derived information

FFprobe inspects the real media container and stream. FFmpeg produces analysis
signals locally. Temporary audio and waveform data remain inside the job
directory. If the optional Deep adapter is explicitly enabled and ready, it
creates vocals, drums, bass, and other stems there, derives coarse descriptors,
including section-aligned relative-energy measurements, and deletes those stems
immediately after feature extraction or adapter failure.
Cancellation, failed-job cleanup, explicit deletion, and TTL cleanup also cover
the private stem directory. The API never exposes stem downloads.

`scripts/diagnose_analysis.py` follows the same local path in a process-managed
temporary directory. It prints derived signal/music evidence, not the source
filename or embedded tags, and removes its decoded audio and any stems when the
command exits.

Core DSP, optional Deep work, and result serialization execute in an isolated
Python worker with a hard `ANALYSIS_TIMEOUT_SECONDS` limit (600 seconds by
default). A timeout or cancellation kills that worker before the normal private-
payload cleanup path runs. Upload/ffprobe validation, FFmpeg decode, and parent-
side prompt composition use their own boundaries and are not silently counted
against this worker timer.

After bounded local storage, the upload route runs a bounded ffprobe preflight
before acceptance. Malformed or unsupported media returns a safe structured 422;
successful cleanup removes both the private job directory and metadata before a
job ID is exposed. If that cleanup cannot complete, the API instead returns a
structured `cleanup_pending` response with the job ID and retry-delete path,
rather than falsely claiming deletion. Preflight-approved media returns HTTP 202.
The visible background **Validating** stage then runs a second, fresh, cancellable
ffprobe so changed/unreadable stored media cannot proceed to decode.

Deep separation requires every regular file anywhere under `MODEL_CACHE_DIR`,
other than the root `demucs-models.json` itself, to be listed by relative path for
the selected model and verified against its SHA-256. One unlisted repository,
configuration, or checkpoint file disables the adapter. Successful hashes are
cached by path, size, modification time, and change time and recomputed when that
signature changes; files must also remain stable during verification. The
manifest is an integrity allowlist, not proof of a checkpoint's license or
provenance. Deep runs with common model-client offline flags, and the application
does not download weights. Model files and their manifest are installation-level
data rather than per-job derivatives, so they persist until the configured model
cache or Docker volume is deliberately removed.

During the upload request, the local ASGI multipart parser may use a bounded
process-managed temporary spool before chunks are copied to `source.bin`. The
upload object is always closed at the request boundary, which releases that
temporary file. The production nginx proxy disables request buffering so it does
not create a second proxy-level body file. It also rejects bodies above
`NGINX_UPLOAD_LIMIT` (202 MiB by default) and applies a 10-minute client-body
inactivity timeout. FastAPI independently enforces `MAX_UPLOAD_MB` on the streamed
file, and the ASGI boundary caps the full upload request before multipart parsing
at that file limit plus 1 MiB of framing allowance. Other API `POST`/`PATCH`
bodies are limited to 256 KiB. Byte counting still applies when a request omits
`Content-Length`, so changing one limit does not silently disable the others.

The completed track can be streamed back to the local waveform player through a
job-scoped `/audio` route with HTTP byte ranges and a generic response filename.
This is local playback of the retained private source, not an upload or a public
stem/download service. Direct Vite and uvicorn commands, as well as published
Compose ports, bind to `127.0.0.1` by default; that loopback bind is part of this
privacy boundary.

The visualizer adds one private, versioned `visual-features.json` artifact to a
completed job directory. It contains bounded normalized numbers and method
metadata only—never audio/stem samples, a source name/path, media tags, lyrics,
transcript, prompts, or model paths. Deep stem curves are calculated before the
temporary stems are deleted. Explicit deletion and TTL expiry remove the
artifact with the entire UUID job directory; cancellation and processing failure
remove an incomplete artifact with other partial derivatives.

The public Blender cue sheet is compiled locally without rereading audio,
loading a model, using a GPU, or contacting a network. Its privacy validator
rejects private field names and absolute filesystem paths. The browser download
uses only the job UUID in its name. The user supplies an audio file separately
to Blender, and Blender writes only to caller-approved output locations.

The private singing transcript keeps accepted, uncertain, likely-hallucinated,
and non-lexical detections so users can review or delete the actual local model
artifact. Standard job responses and JSON/Markdown analysis exports contain only
aggregate lyric status, usable-segment counts, section IDs, and approved abstract
themes; they never merge segment text. Rejected/non-lexical text is not supplied
to the local theme writer. Generated themes remain disabled as prompt evidence
until the user explicitly approves them. The explicit transcript export endpoint
is the only standard route that returns the raw private text as a download.
User-approved abstract themes can use ordinary open-vocabulary concepts, but
the approval path rejects prompt-injection wording, URLs/handles, private paths,
imitation requests, and four-word-or-longer fragments copied from the private
transcript before a theme can enter prompt evidence.

Generated prompts exclude:

- source filenames and internal paths;
- embedded artist, album, and track-title metadata;
- detected or supplied raw lyrics;
- note-for-note melody data; and
- the exact complete chord sequence by default.

User-provided overrides and exclusions are treated as data. They do not become
backend instructions or shell fragments.

Creative and Experimental prompt writing is local to the internal-only Ollama
service. It receives bounded derived evidence, never audio or the private
transcript. Generated candidate packages are private job artifacts; selecting an
existing candidate persists that generated text, while freeform editor changes
remain in the browser unless the user copies or exports them separately.
The sampled-writer contract permits reviewed selected genre labels, an eligible
layered production/vocal blend when `detected_layered` is selected, or a
sanitized explicit target genre. Known taxonomy/detected labels outside the
active mode's set and named-reference terms such as
`artist`, `clone`, `copy`, `imitate`, `in the style of`, and `sounds like` are
forbidden even in negative wording. Prompt diagnostics report bounded reason
codes, counts, and booleans only; they never report generated candidate text or
private transcript text. Candidate and approved-theme validation scans the
timestamp-ordered transcript across decoder boundaries, not just one segment at
time. Candidate titles and creative-direction metadata pass the same private
transcript, source-identity, path, and instruction-language screens as prompt
text; requested transformations must be exact members of the server allowlist.
The sampled writer's provenance metadata is advisory. The server records a
structured fact only when its approved aggregate value is actually expressed in
the validated prompt. Values come from an explicit path resolver that has no raw
transcript, filename, source-path, melody, or complete-chord-sequence route. After
the single model repair, a bounded deterministic repair may insert only those
reviewed literals and must still pass the standard privacy, contradiction,
diversity, and length checks. If the private transcript artifact is missing,
prompt and export boundaries clear transcript-derived themes and invalidate the
stale generated package before returning a durable result.

## Logging

Operational logs may contain timestamps, job UUIDs, safe lifecycle stages,
analyzer names/versions, durations, status codes, and sanitized error codes. They
must not contain raw audio, lyrics, embedded tags, full filesystem paths, or stack
traces returned to the browser. Container-engine logs remain subject to the local
Docker installation's retention settings.

An otherwise uncaught API exception is converted to a structured
`internal_error` with a generic browser message. The global boundary logs the
exception class only; it does not return the exception text, traceback, paths, or
private request data.

## Deletion

### One analysis

`DELETE /api/analyses/{job_id}` is idempotent. Deletion removes the job directory,
its upload, result artifacts and derivatives, its SQLite lifecycle row, and
associated in-memory event state. The UI's **Delete analysis** control uses this
route. Cancellation is also idempotent and removes the upload, partial results,
decoded audio, and intermediates while retaining a safe cancelled lifecycle row
until explicit deletion or TTL expiry. Processing failures apply the same private
payload cleanup. A completed job retains its source privately until deletion or
expiry.

### Automatic expiration

At creation, every job receives a fixed expiration of `JOB_TTL_MINUTES` (60 by
default). The deadline is not silently extended by polling or edits. Periodic
cleanup removes uploads and derived files, lifecycle metadata, result artifacts,
and live state after that deadline. A process that is forcibly terminated cannot
run cleanup until it is started again; use manual complete deletion when needed.

### Entire installation

For Docker, after verifying that the current directory is this repository:

```bash
docker compose down --volumes
```

This is an intentional complete-deletion command. Ordinary full-GPU shutdown,
setup, and recovery use `docker compose ... down` or `setup-full-gpu.ps1` without
`--volumes`, preserving both the private data/model volume and the Ollama model
volume. Neither canonical full-GPU script deletes a named volume.

For the documented direct-start configuration, stop the servers and remove the
literal repository-local directory:

```bash
rm -rf .trackprompt-data
```

```powershell
Remove-Item -LiteralPath .trackprompt-data -Recurse -Force
```

If storage variables point elsewhere, remove those explicit directories too.
Exports saved by the browser, downloaded reports, clipboard contents, browser
caches, and text pasted into Suno or another service are separate copies and are
not deleted by the backend.

## Optional future external polishing

An external prompt-polishing adapter is not implemented or enabled. If one is
added later, it must be opt-in per use, show the provider and data sent, and ask
for explicit consent. It should send only a bounded structured set of derived
features—not audio, source metadata, raw lyrics, exact melody, or credentials.
The response must validate against `PromptPackage`, secrets must stay on the
backend, and deterministic local composition must remain the fallback.
