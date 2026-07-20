# Provider-neutral cloud rendering

The `cloud_render` Python package contains provider-neutral manifests,
scheduling and lease primitives, cost ranking, filesystem/S3-compatible storage
adapters, return validation, media plans, a Brev adapter, and an offline mock
worker. PowerShell owns the Windows operator interface and explicit
confirmations. Importing either layer is offline and has no provider side
effects.

## Current capability status

| Capability | Current status | Boundary |
| --- | --- | --- |
| Readiness and manifest validation | Offline tested | No provider process or network |
| Legacy remote-package to cloud-manifest bridge | Offline local mutation | Writes a new manifest; does not upload |
| SQLite jobs, leases, retries, conflict quarantine | Offline tested primitives | Local database only |
| Filesystem and S3-compatible storage adapters | Mock/unit tested | No production bucket is configured by Mission Control |
| Worker | Offline mock plus bounded Blender subprocess runtime | Production entrypoint is fake-runner tested; no Brev VM/Blender execution has been performed |
| GPU tournament | Offline ranking of supplied measurements | No live Brev measurements exist yet |
| Brev | Adapter and fail-closed readiness only | CLI/environment/live benchmark unverified |
| Hybrid | Static disjoint assignment plus shared-queue/conflict primitives | The production local renderer does not yet claim SQLite leases |
| Cloud video encode and returned-master mux | Argument plans only | The existing Mission Control encoder executes from a verified local frame sequence |
| Worker termination | Tested controller primitive | No active fleet reconciler; manual teardown is required |

Accordingly, the live Brev benchmark is **PREPARED BUT NOT EXECUTED**. These
commands exercise local contracts only; none provisions a VM.

## Command conventions

Run the examples from the repository root in Windows PowerShell. The documented
interpreter is the repository backend virtual environment:

```powershell
$CloudPython = ".\backend\.venv\Scripts\python.exe"
```

Every CLI command prints one JSON envelope. Exit `0` means the requested local
operation succeeded. Readiness and validation are read-only. Commands labelled
`OFFLINE LOCAL MUTATION` write only the paths named by the operator.

## Readiness — READ-ONLY

```powershell
& $CloudPython -m cloud_render.cli readiness
```

Expected safety fields include `provisioningEnabled: false`,
`networkContacted: false`, and `providerProcessInvoked: false`.

`-ValidateOnly` remains read-only and does not invoke Brev:

```powershell
.\WZHK-Media-Launcher.cmd -ValidateOnly
```

## Adapt and validate a sanitized package

`CREATE SANITIZED PACKAGE` currently creates the established
`trackprompt-remote-render-package`. The provider-neutral worker consumes a
different, sealed cloud manifest. The explicit bridge preserves this boundary.

Set the actual package directory and desired manifest output:

```powershell
$RemotePackage = Read-Host "Sanitized remote package directory"
$CloudManifest = Read-Host "Cloud package manifest output JSON"
```

Create the bridge manifest — **OFFLINE LOCAL MUTATION**:

```powershell
& $CloudPython -m cloud_render.cli prepare-manifest `
  --remote-package "$RemotePackage" `
  --output "$CloudManifest"
```

Validate it — **READ-ONLY**:

```powershell
& $CloudPython -m cloud_render.cli validate-manifest `
  --path "$CloudManifest"
```

This structural validation does not prove visual equivalence. The package is
not live-cloud-ready until bounded approved-versus-sanitized comparison frames
have been reviewed and accepted.

## Create and inspect a bounded local scheduler job

Choose new local state paths:

```powershell
$SchedulerDb = ".\.trackprompt-data\cloud-render\offline-demo.db"
$StorageRoot = ".\.trackprompt-data\cloud-render\offline-demo-storage"
$JobId = "trip-to-andromeda-offline-demo"
```

Initialize the job — **OFFLINE LOCAL MUTATION**:

```powershell
& $CloudPython -m cloud_render.cli scheduler-init `
  --database "$SchedulerDb" `
  --job-id "$JobId" `
  --package-manifest "$CloudManifest" `
  --frames-per-chunk 1 `
  --max-attempts 3
```

Read its scheduler state — **OFFLINE LOCAL METADATA MUTATION / STATUS
SNAPSHOT**. Opening an existing SQLite scheduler may create or update SQLite
journal/WAL metadata next to the database; it does not contact a provider or
change the render job contract:

```powershell
& $CloudPython -m cloud_render.cli scheduler-status `
  --database "$SchedulerDb" `
  --job-id "$JobId"
```

Run one explicit mock-worker claim — **OFFLINE LOCAL MUTATION**:

```powershell
& $CloudPython -m cloud_render.cli mock-worker `
  --package-manifest "$CloudManifest" `
  --database "$SchedulerDb" `
  --storage-root "$StorageRoot" `
  --job-id "$JobId" `
  --worker-id "mock-worker-01"
```

The mock worker writes synthetic local output. It does not start Blender,
contact Brev, or establish that a production VM worker works.

## Production worker entrypoint — IMPLEMENTED, NOT ENVIRONMENT-VALIDATED

The worker has a non-mock Blender subprocess runtime with argument-array
execution, bounded output and timeout, cancellation/tree cleanup, package and
manifest identity checks, exact Blender-version validation, NVIDIA and Blender
GPU probes, frame-header validation, leases, heartbeats, and verified
filesystem publication. Its fake-runner tests pass, but it has not been run on
a Brev VM or against the frozen production scene. Mission Control does not
provision or start it.

On an already prepared and separately authorized Linux full GPU VM, set the
actual installed paths and run the entrypoint from the repository root:

```bash
CLOUD_PYTHON="./backend/.venv/bin/python"
CLOUD_MANIFEST="/srv/trackprompt/cloud-package.manifest.json"
SCHEDULER_DB="/srv/trackprompt/cloud-scheduler.sqlite3"
STORAGE_ROOT="/srv/trackprompt/cloud-worker-storage"
REMOTE_PACKAGE="/srv/trackprompt/package"

"$CLOUD_PYTHON" -m cloud_render.worker.render_worker \
  --package-manifest "$CLOUD_MANIFEST" \
  --database "$SCHEDULER_DB" \
  --storage-root "$STORAGE_ROOT" \
  --job-id "exact-job-id" \
  --worker-id "brev-worker-01" \
  --remote-package "$REMOTE_PACKAGE" \
  --blender "/opt/blender/blender" \
  --nvidia-smi "nvidia-smi" \
  --render-timeout-seconds 21600 \
  --run-until-idle
```

This command performs rendering and local scheduler/storage mutation. Do not
run it as an offline-readiness check. It does not provision a VM, upload the
package, supervise a fleet, or replace the separate live authorization,
provider verification, and teardown requirements.

## Rank supplied tournament results — READ-ONLY

Prepare a local JSON document whose `benchmarks` array contains measured offer,
price, timing, visual, and technical fields, then run:

```powershell
$TournamentInput = Read-Host "Tournament benchmark JSON"
& $CloudPython -m cloud_render.cli tournament-rank `
  --input "$TournamentInput"
```

Ranking supplied data is not a live benchmark. Until separately authorized
measurements exist, no winning Brev GPU has been selected.

## Video-only encode plan — READ-ONLY, PLAN ONLY

This returns an FFmpeg argument array; it does not run FFmpeg. For a bounded
three-frame example, the verified frame set may be supplied inline:

```powershell
& $CloudPython -m cloud_render.cli encode-plan `
  --ffmpeg "ffmpeg" `
  --frame-pattern "C:\render-return\frames\frame_%06d.png" `
  --frame-start 1 `
  --frame-end 3 `
  --verified-frames "1,2,3" `
  --fps 30 `
  --output "C:\render-return\video-only-demo.mp4"
```

For a production-sized sequence, put the verified frame numbers in a local
JSON array or comma/newline-delimited text file and pass the file instead of a
very long command-line value:

```powershell
$VerifiedFramesFile = Read-Host "Verified frame-number file"
& $CloudPython -m cloud_render.cli encode-plan `
  --ffmpeg "ffmpeg" `
  --frame-pattern "C:\render-return\frames\frame_%06d.png" `
  --frame-start 1 `
  --frame-end 13029 `
  --verified-frames-file "$VerifiedFramesFile" `
  --fps 30 `
  --output "C:\render-return\video-only-master.mp4"
```

## Local private-audio mux plan — READ-ONLY, PLAN ONLY

This also returns arguments only:

```powershell
& $CloudPython -m cloud_render.cli mux-plan `
  --ffmpeg "ffmpeg" `
  --video-only "C:\render-return\video-only-master.mp4" `
  --private-audio "C:\private-audio\source.wav" `
  --output "C:\render-return\final-delivery.mp4"
```

The currently executable Mission Control path is instead:

```text
WZHK-Media-Launcher.cmd
-> ENCODE / MUX FINAL VIDEO
-> select exact saved profile
-> select complete verified local frame-sequence directory
-> select private local audio
-> PREFLIGHT ONLY
-> START LOCAL ENCODE / MUX (only after both confirmations)
```

## Import a returned chunk — OFFLINE LOCAL MUTATION

The importer first copies returned data into quarantine, validates identity and
frames, and atomically publishes only missing valid frames. Pass the exact
sealed cloud package manifest so identity, package range, resolution, and
image-header requirements come from one validated contract rather than being
retyped. When explicit chunk bounds are omitted, the CLI validates the sealed
returned manifest, derives its positive, nonduplicate, contiguous chunk range,
and requires that range to fall inside the package:

```powershell
$ReturnedDirectory = Read-Host "Downloaded return root preserving manifest objectKey paths"
$QuarantineRoot = Read-Host "Local quarantine root"
$OutputFrames = Read-Host "Canonical local output-frames directory"
$ReturnManifest = Read-Host "Returned chunk manifest JSON"
$CloudManifest = Read-Host "Sealed cloud package manifest JSON"

& $CloudPython -m cloud_render.cli import-return `
  --returned "$ReturnedDirectory" `
  --quarantine-root "$QuarantineRoot" `
  --output-frames "$OutputFrames" `
  --manifest "$ReturnManifest" `
  --package-manifest "$CloudManifest"
```

Conflicting valid local/cloud frames remain quarantined with local preference;
the importer never silently overwrites the local frame.

## Hybrid boundary

Mission Control can generate a static non-overlapping local/remote assignment
manifest. Separately, the scheduler models `LOCAL` and `CLOUD` workers and
quarantines conflicting publication. The production local renderer is not yet
wired to claim scheduler leases, so do not describe or operate this as a live
shared-queue hybrid render.

Automated tests use mock providers, workers, and filesystem/object-storage
fixtures. They never provision billable resources.
