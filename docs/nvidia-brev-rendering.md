# NVIDIA Brev rendering

The intended Blender environment is an NVIDIA Brev **full GPU VM**. NVIDIA NIM
is an inference-container product and is not used as a Blender renderer. No
Launchable schema or CLI flag is assumed until the locally installed official
Brev CLI documents it.

## Current status

**PREPARED BUT NOT EXECUTED.** The provider adapter and fail-closed local
readiness paths exist, but this machine has no recorded, verified Brev CLI
capability inspection, VM environment validation, or live billable benchmark.
There is no measured winning GPU or active fleet. A bounded production Blender
worker entrypoint exists and is fake-runner tested, but its Brev VM environment,
real Blender/GPU probes, and frozen-scene output remain unverified.

Graphics-oriented candidates should be considered first: L40S, L40, RTX 6000
Ada, RTX PRO 6000 variants, RTX 4090/5090 where offered, and A40. H100 and H200
are optional measured comparisons. H200 is not automatically best for EEVEE;
selection requires validated cost per frame and visual equivalence.

## Existing package-specific visual evidence

The current validated remote package at
`render-packages\trip-to-andromeda\225ee7124b62\8dac222acc7d\package` has
bounded original-versus-sanitized evidence. Its package ID is
`pkg-1447cb6531a8-31dea3f11155`, its package SHA-256 is
`00E66F1F7748C789864DF22F842BF7B82A843E55C7A50003BE6C0D27D6FC2D1E`,
and FFmpeg decoding produced the same pixel SHA-256 for the approved and
sanitized comparison frames:

```text
5662cf0406bed23d28feedde066dfa5fef9e5baf4b4a88960cd869af4f662f35
```

The PNG container hashes differed, so the decoded-pixel comparison is the
relevant visual-equivalence evidence. This evidence is bound only to that exact
package and source/profile identities. The generic exporter performs a
sanitized smoke render but does not automatically render and compare both
scenes. Any regenerated or different package requires new bounded A/B evidence.

## Brev prerequisites and verification checklist

Complete these prerequisites outside Mission Control before any provider
discovery or billable operation:

1. Install the current official NVIDIA Brev CLI by following the current
   official NVIDIA/Brev documentation.
2. Authenticate the CLI in the operator's intended Brev account/context.
3. Record the CLI version and date of inspection.
4. Inspect root help and the exact help for discovery, create, list, stop, and
   delete. Do not infer unsupported commands or Launchable fields.
5. Confirm the selected offer is a full Linux GPU VM, not NIM.
6. Prepare a supported Ubuntu image with host NVIDIA driver/runtime, Blender
   5.2, EGL/OpenGL libraries, FFmpeg/ffprobe, Python, checksum tools, the worker
   package, storage client, supervisor, and shutdown helper.
7. Verify headless Blender, intended GPU visibility, absence of software
   rasterization, EEVEE/compositor/Fog Glow output, bounded frame rendering,
   upload, heartbeat, cancellation, and shutdown.

This repository deliberately does not print an installation or live-create
command without a verified current local CLI schema.

After the CLI is installed, the following inspection is **READ-ONLY LOCAL CLI
INSPECTION**. It may start the local `brev` executable for version/help only;
it does not request offers or provision anything:

```powershell
$CloudPython = ".\backend\.venv\Scripts\python.exe"
$Brev = Get-Command brev -ErrorAction Stop
& $CloudPython -m cloud_render.cli brev-readiness `
  --executable "$($Brev.Source)"
```

Do not continue if the returned capability report cannot prove the required
official commands and flags.

## Exact non-billable benchmark-preparation workflow

From the repository root:

```powershell
.\WZHK-Media-Launcher.cmd
```

Then select:

```text
NVIDIA BREV CLOUD RENDER
-> OFFLINE CLOUD READINESS
-> INSPECT INSTALLED BREV CLI (only if installed)
-> CREATE SANITIZED PACKAGE
-> prepare and validate the provider-neutral cloud manifest offline
-> verify package-specific visual evidence, or complete a new bounded A/B for a different package
-> CLOUD BENCHMARK TOURNAMENT
-> select the exact saved profile
-> select the sealed cloud manifest
-> cross-check its source-profile hash against the selected saved profile
-> derive the cloud scene/profile/package identities from that manifest
-> enter the maximum budget
-> review the candidate list and ranges 7065-7094 / 8091-8120
-> type the exact AUTHORIZE BREV BENCHMARK token
-> review the complete offline plan
-> [Y] LOCK CLOUD PLAN
-> PREPARED BUT NOT EXECUTED
-> EXIT WITHOUT PROVISIONING FLEET
```

The current readiness build stops after the offline plan lock. It does not ask
for `PROVISION BILLABLE GPU WORKERS`, records no reusable live authorization,
contacts no provider, and does not provision a worker. A future live-capable
command must ask for fresh exact authorization and a separate final billable
confirmation. No runnable live provisioning command is documented here.

The current preparation flow does not query offers, select measured candidates,
run a tournament, review actual cost per frame, or select a winner. Use the
offline `tournament-rank` command in
[`cloud-rendering.md`](cloud-rendering.md) only after separately obtained,
validated measurements exist. No runnable live provisioning command is
documented here.

Four or eight workers remain forbidden until an explicitly authorized,
exactly-one-worker bounded benchmark completes, uploads valid results, reports
measured cost per validated frame, and is confirmed stopped.

## Future production workflow — design target, not currently runnable

```text
SELECT APPROVED SCENE AND EXACT SAVED PROFILE
-> CREATE AND VISUALLY APPROVE SANITIZED CLOUD PACKAGE
-> DISCOVER CURRENT OFFERS THROUGH VERIFIED CLI
-> BENCHMARK ONE INSTANCE PER SELECTED CANDIDATE
-> REVIEW COST PER VALIDATED FRAME
-> SELECT WINNER
-> CHOOSE WORKERS / DEADLINE / BUDGET / STORAGE
-> REVIEW EXPECTED AND CONSERVATIVE COST/TIME
-> FRESH PLAN LOCK AND FINAL BILLABLE CONFIRMATION
-> PROVISION BOUNDED FLEET
-> RUN VM AND WORKER HEALTH CHECKS
-> DYNAMIC CHUNK LEASES AND VALIDATED UPLOADS
-> ACTIVE COST / HEARTBEAT / IDLE MONITORING
-> STOP COMPLETED OR IDLE WORKERS
-> VERIFY COMPLETE CLOUD SEQUENCE
-> ENCODE VIDEO-ONLY MASTER
-> DOWNLOAD MASTER, QA FRAMES, LOGS, AND MANIFESTS
-> MUX PRIVATE AUDIO LOCALLY
-> FINAL FFPROBE AND VISUAL QA
-> EXPLICITLY DELETE CLOUD STORAGE AND INSTANCES
```

The active fleet reconciler, provider-to-worker launch wiring, cloud encode
executor, artifact download, and local returned-master mux are not yet wired.
The bounded worker entrypoint alone does not make the list above runnable; it
remains a target workflow, not a readiness claim.

## Manual teardown for an already known instance

Automatic fleet reconciliation is not active. If a separately authorized
external operation created an instance, teardown is manual. The following is a
**NETWORK / DESTRUCTIVE PROVIDER COMMAND**. Run it only for a verified instance
reference after preserving required outputs:

```powershell
$CloudPython = ".\backend\.venv\Scripts\python.exe"
$Brev = Get-Command brev -ErrorAction Stop
$KnownInstance = Read-Host "Exact Brev instance reference to stop and delete"

& $CloudPython -m cloud_render.cli brev-teardown `
  --executable "$($Brev.Source)" `
  --instance-ref "$KnownInstance" `
  --confirm-stop "STOP BREV WORKER" `
  --confirm-delete "DELETE BREV WORKER"
```

This command does not delete object-storage frames. Keep returned/cloud frames
until local verification and mux QA pass, then delete storage through a
separately confirmed provider-specific procedure.
