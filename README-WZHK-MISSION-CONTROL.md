# WZHK Media — TrackPrompt Mission Control

A modular, keypad-driven PowerShell wrapper around the existing
`render-trackprompt-final.ps1`.

## Install

Extract this package into the root of:

```text
C:\Users\theon\GitHub\TrackPrompt-Studio
```

The resulting paths should include:

```text
WZHK-Media-Launcher.cmd
wzhk-media-control-center.ps1
tools\watch-trackprompt-final-render.ps1
tools\test-wzhk-mission-control.ps1
tools\wzhk-launcher\WZHK.UI.psm1
tools\wzhk-launcher\WZHK.Discovery.psm1
tools\wzhk-launcher\WZHK.Profiles.psm1
tools\wzhk-launcher\WZHK.ProfileBuilder.psm1
tools\wzhk-launcher\WZHK.Execution.psm1
tools\wzhk-launcher\WZHK.Calibration.psm1
tools\wzhk-launcher\WZHK.Performance.psm1
tools\wzhk-launcher\WZHK.Outsource.psm1
tools\wzhk-launcher\WZHK.Cloud.psm1
tools\wzhk-launcher\WZHK.Brev.psm1
cloud_render\cli.py
render-profiles\README.md
```

The package does not replace `render-trackprompt-final.ps1`. It wraps it.

## Start with one command

From the repository root:

```powershell
.\WZHK-Media-Launcher.cmd
```

From anywhere:

```text
C:\Users\theon\GitHub\TrackPrompt-Studio\WZHK-Media-Launcher.cmd
```

You may pin the CMD file to Start or create a desktop shortcut.

## Noninteractive validation

To validate parser compatibility, module imports, required files, and read-only
package/profile discovery without opening a browser or starting Blender, run:

```powershell
.\WZHK-Media-Launcher.cmd -ValidateOnly
```

The automated Windows PowerShell 5.1 regression suite also exercises synthetic
profile, hash, authorization-token, output-progress, and CMD-forwarding cases:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\test-wzhk-mission-control.ps1
```

## Controls

- Numpad `1`–`9`: select a menu item.
- Arrow keys: move selection.
- Page Up/Page Down or Left/Right: move between pages when a menu has more
  than nine actions.
- Enter: confirm the highlighted item.
- Escape: return.
- First confirmation: `Y` locks the selected mode; `N` returns to fix it.
- Final confirmation: `Y` executes; `N` cancels.

Pressing a digit only moves the highlight. Mission Control never executes that
item until Enter confirms it.

## Reusable render profiles

Choose `CREATE NEW RENDER PROFILE` to start from FULL HD FAST, 1440P BALANCED,
4K BALANCED, 4K HIGH, 4K ULTRA, or CUSTOM. The 13 keyboard-driven stages expose
identity, frozen scene, resolved resolution, timeline, quality, supported
Blender 5.2 EEVEE settings, image sequence, color management, chunk/resume
safety, output policy, encoding, dashboard preferences, and final review.

Generated profiles are valid schema 1.1.0 JSON stored beneath
`render-profiles\<project>\`. Each save is atomic and has a sibling
`.summary.txt`. Generated profiles and authorization records stay local and are
ignored by Git because they contain absolute local scene paths and hashes.
Final-media encoding stays disabled until the profile binds an exact approved
audio SHA-256 and the full approved timeline clock. Pixel aspect and the safe
published-frame subdirectory are included in resume compatibility.

`LOAD / EDIT SAVED PROFILE` supports inspect, edit, duplicate, rename, compare,
summary export, preflight, dry-run, render, authorization, and explicitly
confirmed deletion. Editing or renaming changes the exact saved-file SHA-256,
so an older authorization record or output manifest no longer matches. Parseable
invalid profiles remain selectable only in this manager for validation-error
inspection, repair through the builder, or two-confirmation deletion; render,
preflight, dry-run, compare, and authorization selectors keep them disabled.
Saved-profile workflows use the scene identity stored in the profile and do not
require choosing a preparation package at startup. Preparation selection is
requested lazily when creating a new profile.

The authorization request records only the token hash and exact scene/profile
hash preview. Mission Control creates the local authorization record only after
two explicit Y confirmations. There is no wildcard authorization.

Saved profiles also have command-line entry points:

```powershell
.\WZHK-Media-Launcher.cmd -ListProfiles

.\WZHK-Media-Launcher.cmd -ValidateProfile `
  -ProfilePath ".\render-profiles\trip-to-andromeda\4k-balanced.json"

.\WZHK-Media-Launcher.cmd -RenderProfile `
  -ProfilePath ".\render-profiles\trip-to-andromeda\4k-balanced.json"
```

The render command remains interactive: it refuses redirected input and still
requires a valid exact authorization record plus both render confirmations.

## Workflow

Mission Control also exposes calibration, saved-profile generation,
provider-neutral cloud primitives, NVIDIA Brev readiness and bounded benchmark
preparation, exclusive performance mode, and identity-bound
stop-after-current-chunk controls. The top-level menu is paginated, so actions
ten and later remain reachable with arrows or Page Up/Page Down.

## Current capability matrix

| Capability | Status | Important boundary |
| --- | --- | --- |
| Local calibration, profiles, preflight, dry run, render, and local sequence encode | Implemented local workflows | Rendering and encoding still require their exact confirmations |
| Sanitized remote package | Offline local workflow | Produces the established remote-package schema; use the explicit offline bridge before cloud-manifest validation |
| Cloud manifest, scheduler, leases, retry/quarantine, storage, and cost ranking | Offline-tested primitives | No provider or billable side effect |
| Worker | Offline mock plus bounded Blender subprocess runtime | Production entrypoint is fake-runner tested; it has not been executed on a Brev VM or against the frozen production scene |
| NVIDIA Brev | Readiness and fail-closed adapter | No verified local CLI capability report, live benchmark, winning GPU, or production fleet |
| Benchmark tournament | Preparation and offline ranking only | `PREPARED BUT NOT EXECUTED`; supplied measurements are not live results |
| Hybrid | Static disjoint planning plus shared-queue/conflict primitives | The local production renderer does not yet claim the cloud scheduler queue |
| Cloud video encode / returned-master audio mux | Argument plans only | The executable Mission Control encoder currently starts from a verified local frame sequence |
| Worker termination | Tested controller primitive | No active reconciler; an already known live instance requires manual teardown |
| Thermal/memory protection | Snapshot and manual safe-stop controls | No continuous watchdog automatically writes a stop marker |

The exact copy-paste offline cloud commands, including package adaptation,
manifest validation, scheduler setup, mock work, tournament ranking, media
plans, and return import, are in
[`docs/cloud-rendering.md`](docs/cloud-rendering.md). No runnable live
provisioning command is documented.

Focused operator documentation:

- [`docs/render-calibration.md`](docs/render-calibration.md)
- [`docs/render-profiles.md`](docs/render-profiles.md)
- [`docs/local-performance-mode.md`](docs/local-performance-mode.md)
- [`docs/cloud-rendering.md`](docs/cloud-rendering.md)
- [`docs/nvidia-brev-rendering.md`](docs/nvidia-brev-rendering.md)
- [`docs/cloud-render-privacy.md`](docs/cloud-render-privacy.md)
- [`docs/cloud-render-recovery.md`](docs/cloud-render-recovery.md)
- [`docs/cloud-render-cost-model.md`](docs/cloud-render-cost-model.md)

1. **Mode Confirmation**
   - Select preflight, dry-run/resume plan, production render, visual watcher,
     output viewer, or another preparation package.
   - Select the authorized render profile and output/resume target.
   - Review scene, hashes, counts, resolution, frame contract, progress,
     authorization state, and watcher state inside one framed screen.
   - Confirm with `Y`, or press `N` to fix the mode.
   - Complete a second final Y/N confirmation.

2. **The Frame**
   - Runs the existing renderer in a child PowerShell process.
   - Streams all process output inside the WZHK console frame.
   - Production mode automatically starts the browser progress dashboard.
   - The dashboard shows the newest frame, rendered and atomically published
     progress, movie position, active chunk, rolling speed, ETA, and Blender log.

3. **Done**
   - A short neon/90s ASCII celebration appears after successful completion.
   - Failures produce a framed error and preserve the wrapper log.

## Safety

The wrapper does not bypass or weaken the existing production renderer. The
existing exact authorization token, scene/profile hash validation, one-GPU
mutex, storage checks, atomic chunk publication, and resume behavior remain in
control. If a profile explicitly enables invalid-frame replacement, production
does not overwrite the file: after authorization, mutex acquisition, and a
fresh storage check, it moves invalid canonical frames into a recoverable
checkpoint quarantine and renders the now-missing frames. Valid frames,
ambiguous names, and dry-run/preflight state are never mutated.

The wrapper never runs:

```text
git reset --hard
git clean -fd
docker compose down --volumes
```

It does not delete partial or completed outputs.

## NVIDIA Brev safety boundary

The cloud readiness path is offline. `-ValidateOnly` checks files, PowerShell
5.1 parsing, module imports, and local profile/output discovery; it does not
invoke Brev or contact a provider. `INSPECT INSTALLED BREV CLI` is a separate
operator action limited to local version/help inspection.

Mission Control prepares the bounded benchmark token:

```text
AUTHORIZE BREV BENCHMARK: <PACKAGE_SHA12> | <PROFILE_SHA12> | MAX $<BUDGET>
```

The token is case-sensitive and plan-specific. The current offline preparation
path accepts the exact token, displays the offline plan, and asks only for
`[Y] LOCK CLOUD PLAN`. It then reports `PREPARED BUT NOT EXECUTED` and exits
without discovery, provisioning, upload, render, or network action. It never
asks the operator to rehearse a billable confirmation and records no reusable
live authorization.

A separately authorized future live command must ask again with the exact live
plan and must additionally require `[Y] PROVISION BILLABLE GPU WORKERS`. No
runnable live provisioning command is documented by this readiness build.

Brev full GPU VMs are the intended environment. NVIDIA NIM inference containers
are not used for Blender. The installed Brev CLI, its official command schema,
the VM image, production Blender worker environment, and automatic fleet
controller remain unverified on this machine. Manual teardown is required for any instance
created outside this preparation-only workflow; see
[`docs/nvidia-brev-rendering.md`](docs/nvidia-brev-rendering.md).

## 4K

A genuine 4K mode appears only when the selected final-render preparation
package contains a valid profile that resolves to `3840×2160` and a matching
scene/profile authorization token. The wrapper does not rename or mutate a
1440p profile to pretend it is 4K.
