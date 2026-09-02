# Andromeda V2 release finalization

`tools/andromeda_release_finalization.py` prepares hash-bound render profiles
and assembles a fresh horizontal-only release bundle. It does not invoke
Blender, render frames, encode media, create an operator authorization, or
cross the production-start boundary.

Finalization is evidence assembly, not evidence generation. A report that says
an operation passed is insufficient unless its schema identifies the exact
scene, profile, revision, hardware, worker, and source artifacts required for
that operation.

## Safety boundary

Both commands require a new output directory. `--overwrite` remains accepted
only for command-line compatibility and is rejected. A final release is staged
as one sibling directory and promoted with one atomic directory rename. If a
write or post-publication validation fails, the staging directory and any
newly published directory are removed. Existing bundles are never modified.

Every generated technical authorization records:

```json
{
  "operatorStartGate": {
    "status": "not-authorized",
    "explicitFullRenderStartAuthorized": false
  },
  "productionStartAllowed": false,
  "finalRenderStarted": false,
  "codexHumanArtisticApproval": false
}
```

## 1. Prepare scene-bound profiles

Both scene build receipts must contain the exact SHA-256 of the supplied
builder:

```json
{
  "schemaVersion": "1.0.0",
  "builderId": "andromeda-v2-master-scene-builder-v2",
  "builderSourceSha256": "<exact builder-source SHA-256>",
  "outputBlend": "<absolute path emitted by the builder>",
  "productionAuthorized": false,
  "renderStarted": false
}
```

The profile-preparation request binds one builder, two immutable base
profiles, two independently authored scenes, and their build receipts:

```json
{
  "schemaVersion": "1.0.0",
  "kind": "trackprompt-andromeda-v2-profile-preparation-request",
  "releaseTag": "r14-reviewed-20260723",
  "builderSource": {
    "role": "builder-source",
    "path": "blender/trackprompt_visualizer/andromeda_story_v2.py",
    "sha256": "<builder SHA-256>"
  },
  "horizontal": {
    "baseProfile": {
      "role": "horizontal-base-render-profile",
      "path": "render-profiles/trip-to-andromeda/andromeda-v2-horizontal-1080p-final.json",
      "sha256": "<base-profile SHA-256>"
    },
    "scene": {
      "role": "horizontal-scene",
      "path": "<fresh horizontal .blend>",
      "sha256": "<scene SHA-256>"
    },
    "buildReceipt": {
      "role": "horizontal-scene-build-receipt",
      "path": "<fresh horizontal build receipt>",
      "sha256": "<receipt SHA-256>"
    }
  },
  "vertical": {
    "baseProfile": {
      "role": "vertical-base-render-profile",
      "path": "render-profiles/trip-to-andromeda/andromeda-v2-vertical-1080x1920-final-optional.json",
      "sha256": "<base-profile SHA-256>"
    },
    "scene": {
      "role": "vertical-scene",
      "path": "<fresh vertical .blend>",
      "sha256": "<scene SHA-256>"
    },
    "buildReceipt": {
      "role": "vertical-scene-build-receipt",
      "path": "<fresh vertical build receipt>",
      "sha256": "<receipt SHA-256>"
    }
  }
}
```

Run:

```powershell
.\backend\.venv\Scripts\python.exe `
  .\tools\andromeda_release_finalization.py prepare-profiles `
  --repository-root . `
  --request .\test-output\<proof>\profile-preparation-request.json `
  --output-directory .\test-output\<proof>\release\profiles
```

The generated horizontal profile is pending operator approval. The generated
vertical profile remains disabled pending its own final-resolution
calibration, aggregate dual-matrix SLA, package, and operator authorization.

## 2. Collect strict evidence

All evidence paths are normalized repository-relative POSIX paths. Every file
has an exact lowercase SHA-256. Nested frame, log, throughput, media, and media
QA receipts also contain an exact byte count.

JSON roles must use a `.json` suffix and parse as the role-specific model.
Renaming generic text to `.json`, using a different suffix, or supplying a
generic `technicalPass` object does not satisfy the contract.

### Final-resolution calibration

`final-resolution-calibration-evidence` is the only calibration input.
Caller-supplied percentile and projection scalars are not accepted in the
finalization request.

The calibration evidence must:

- bind the horizontal scene, generated profile, builder, worker, hardware
  report, Git HEAD, and recomputed source-tree digest;
- identify exactly 13,029 frames at 1920x1080, 30 fps;
- contain at least ten unique warm-renderer measurements with timezone-bound
  start and completion timestamps, measured duration, forecast weight, exact
  final-resolution PNG, and renderer log;
- cover all seven acts; all required expensive effect classes; and the start,
  middle, and end of expensive shots;
- use weights summing to 1.0;
- contain the eight ordered evidence-based stages:
  `scene-package-preparation`, `cache-bake`, `image-sequence-render`,
  `frame-validation`, `encoding`, `final-qa`, `publication`, and
  `contingency`;
- derive the image-sequence P50 and P90 using nearest-rank percentiles of the
  measured frame times multiplied by exactly 13,029;
- derive the weighted projection from measured time, sample weight, and
  exactly 13,029; and
- keep the sum of all stage P90 forecasts at or below 86,400 seconds.

Finalization recomputes all three values. A matching caller echo is not
trusted.

### Hardware, storage, and worker

The hardware report records the operating-system build, CPU, RAM, every GPU
and VRAM amount, driver, Blender 5.2.0 LTS, FFmpeg, FFprobe, executable
digests, AC-power state, sleep-risk acknowledgement, and measured target-disk
throughput evidence. Its scene, profile, and worker must match the
calibration. Available disk must include the declared safety multiplier of at
least 1.25.

The worker report contains exactly one horizontal worker requirement and a
local match whose GPU model appears in the hardware report and whose VRAM
satisfies the requirement.

### Dashboard and visual/media evidence

The dashboard proof includes an exact final-resolution completed PNG,
timezone-bound `completedAt`, `publicationStartedAt`, and `publishedAt`
timestamps, and `publicationLatencySeconds`. The latency is recomputed from
frame completion through dashboard publication, must match the timestamps
within one millisecond, and must not exceed 2.0 seconds. The same proof
contains a persisted P50/P90 ETA snapshot, revision bindings, and passing
endpoint, restart, event-channel, stop-after-chunk, retry, and variant-stream
checks:

```json
{
  "completedFrame": {
    "outputVariantId": "horizontal-16x9-1080p",
    "frame": 652,
    "completedAt": "2026-07-23T15:00:00+03:00",
    "publicationStartedAt": "2026-07-23T15:00:00.100+03:00",
    "publishedAt": "2026-07-23T15:00:00.750+03:00",
    "publicationLatencySeconds": 0.75,
    "image": {
      "path": "<exact latest-frame PNG>",
      "sha256": "<exact SHA-256>",
      "sizeBytes": 123456,
      "format": "PNG",
      "width": 1920,
      "height": 1080
    }
  }
}
```

The full-song animatic is explicitly LOW resolution, not a final-resolution
proof. Its report binds a hash/size-checked 480x270 16:9 MP4 receipt, all
13,029 video frames, the 434.3-second song clock, H.264/yuv420p video,
AAC/44.1-kHz stereo audio, sync tolerance, scene, profile, and source audio.
The animatic receipt independently binds the same media, story, and shot
plans. Final-resolution requirements apply to calibration images and
transition samples rather than this full-length review artifact.
The media-QA document declares
`outputVariantId: "horizontal-16x9-1080p"` and
`resolutionClass: "LOW"` explicitly.

Motion and exposure reports must cover every act and exact review frames.
Transition evidence contains exactly these three continuous final-resolution
samples and a separate hash-bound media-QA document for each:

1. gates to rupture;
2. rupture to transformation;
3. transformation to arrival.

Vertical evidence binds an independently authored 1080x1920 MP4, vertical
scene, disabled vertical profile, camera, composition profile, safe-zone and
mobile checks, and an explicit `horizontalCropUsed: false`.

### Release hold and human closure

The request must supersede the identity in the tracked
`production/andromeda-v2/release-hold.json`; a copied or caller-selected old
identity is not sufficient.

Two distinct human records are required:

- `human-visual-qa-approval` approves the exact corrected scene, profiles,
  animatic, transition evidence, motion/exposure evidence, and vertical proof,
  with no blocking findings.
- `human-review-closure` is timestamped after that approval and binds the
  tracked hold ID and file hash, held identity, approval hash, corrected scene
  and profile, calibration evidence, transition report, and vertical proof.

Neither record authorizes an operator start. Codex cannot stand in for the
human reviewer.

### Verification matrix

Every `VerificationCheck` has a typed `checkId`. The verification report must
contain each ID exactly once in this order:

```text
backend-pytest
backend-ruff
backend-mypy
blender-tooling-tests
mission-control-generic-fixture-tests
frontend-unit-tests
frontend-lint
frontend-typecheck
frontend-build
frontend-e2e
dependency-import-diagnostics
powershell-parser-harness-launcher
compose-base-config
compose-gpu-config
proof-regeneration
git-diff-check
```

Each item records the exact command, status, runtime, and evidence. A skipped
item requires a nonempty `skipReason`, and that reason must appear verbatim in
`knownLimitations`; silent or generic skips are rejected. The
`git-diff-check` item may not be skipped and must have status `passed`.

## 3. Bind the exact Git state

Finalization obtains Git facts directly from the repository:

- current branch from `git rev-parse --abbrev-ref HEAD`;
- implementation commit from `git rev-parse HEAD^{commit}`;
- starting-commit ancestry from `git merge-base --is-ancestor`;
- exact ordered commits from the starting commit through HEAD;
- source-tree SHA-256 from the raw bytes of
  `git ls-tree -r --full-tree <HEAD>`;
- source-tree entry count from that same output; and
- porcelain status for every release-owned source path.

Request and source-revision values must equal those results. Any tracked,
modified, deleted, or untracked release-owned path rejects finalization.

Remote state is informative rather than a local release blocker:
`pushed`, `not-pushed`, or `not-configured`, with tracking ref and remote
commit when available.

## 4. Finalization request

The top level contains no `calibration` object:

```json
{
  "schemaVersion": "1.0.0",
  "kind": "trackprompt-andromeda-v2-release-finalization-request",
  "releaseTag": "r14-reviewed-20260723",
  "recordedAt": "<timezone-bound finalization time>",
  "branch": "feat/andromeda-story-v2",
  "startingCommitSha": "<verified ancestor>",
  "implementationCommitSha": "<verified HEAD>",
  "commitList": ["<exact ordered ancestry through HEAD>"],
  "sourceTreeSha256": "<SHA-256 of raw git ls-tree bytes>",
  "sourceTreeEntryCount": 500,
  "supersedesReleaseIdentitySha256": "<identity in tracked release hold>",
  "horizontalOutputPattern": "final-output/andromeda-v2-r14-horizontal/frames/frame_######.png",
  "sourceBindings": [
    {
      "role": "source-audio",
      "sha256": "6adf4f3e75f1f775226571ace56883b6e72ad11775bde6c94adc1b95112e5cd5",
      "sizeBytes": 76608080,
      "privateLocalArtifact": true,
      "committed": false
    },
    {
      "role": "source-cue",
      "sha256": "b58ba759feb44aa869391ade40e72b8450d0e2917e40255ccf60af2e0205c1b2",
      "sizeBytes": 1276886,
      "privateLocalArtifact": true,
      "committed": false
    }
  ],
  "artifacts": [
    {
      "role": "<one exact required role>",
      "path": "<repository-relative evidence path>",
      "sha256": "<exact SHA-256>"
    }
  ]
}
```

The artifact set must equal this list; omissions and additions are rejected:

```text
animatic-media-qa-report
animatic-receipt
builder-source
dependency-health-report
deterministic-effects-and-disk-report
encoding-profiles
exposure-mobile-readability-report
final-look-profile
final-quality-transition-report
final-resolution-calibration-evidence
final-scene
final-scene-receipt
full-audio-animatic
gates-to-rupture-media
hardware-and-storage-report
horizontal-render-profile
horizontal-scene-build-receipt
human-review-closure
human-visual-qa-approval
live-dashboard-proof
motion-health-report
output-variants
owner-creative-acceptance
rupture-to-transformation-media
shot-plan
source-revision-report
story-plan
transformation-to-arrival-media
verification-report
vertical-bounded-proof-media
vertical-bounded-proof-media-qa
vertical-composition-proof
vertical-master-scene
vertical-render-profile
vertical-scene-build-receipt
worker-requirements
```

Run:

```powershell
.\backend\.venv\Scripts\python.exe `
  .\tools\andromeda_release_finalization.py finalize `
  --repository-root . `
  --request .\test-output\<proof>\release-finalization-request.json `
  --output-directory .\test-output\<proof>\release\bundle
```

## 5. Generated bundle and report

The new directory contains:

```text
v2-calibration.json
technical-authorization-v2.json
package-manifest-v2.json
evidence/release-report.json
```

The command result includes the exact SHA-256 of all four outputs. The package
manifest hash-binds the calibration, technical authorization, report, and
every input artifact. Release report schema `4.1.0` includes:

- tracked hold and distinct human closure;
- verified branch, commit ancestry, source tree, dirty-state result, and
  remote-push status;
- owner acceptance, seven-act story, 35-shot plan, and final look;
- scene, builder, build receipts, exact horizontal and disabled-vertical
  profile settings;
- full animatic and all media QA;
- hardware, worker, VRAM, disk, and dashboard evidence;
- the exact typed §14/AGENTS verification matrix, including surfaced skipped
  checks and a mandatory passed Git-diff check;
- every calibration measurement, recomputed P50/P90/weighted projections, all
  eight stage forecasts, aggregate SLA, and limitations;
- explicit `enabledVariantForecasts` with render, encoding, QA, and total P50
  and P90 seconds for each enabled variant, plus a separately labeled
  `aggregateForecast` over the exact enabled variant IDs;
- generated-document paths and external hash-delivery fields; and
- operator-authorization, horizontal start/resume, and fail-closed
  horizontal-plus-vertical command templates.

For the horizontal-only matrix, the aggregate forecast must exactly equal the
single enabled horizontal variant forecast. Render values bind the measured
13,029-frame image-sequence projection, encoding and QA bind their detailed
stage evidence, and total binds the sum of all eight ordered production
stages:

```json
{
  "enabledVariantForecasts": [
    {
      "outputVariantId": "horizontal-16x9-1080p",
      "frameCount": 13029,
      "render": {"p50Seconds": 8468.85, "p90Seconds": 11726.1},
      "encoding": {"p50Seconds": 180.0, "p90Seconds": 240.0},
      "qa": {"p50Seconds": 120.0, "p90Seconds": 180.0},
      "total": {"p50Seconds": 9278.85, "p90Seconds": 13076.1}
    }
  ],
  "aggregateForecast": {
    "enabledVariantIds": ["horizontal-16x9-1080p"],
    "render": {"p50Seconds": 8468.85, "p90Seconds": 11726.1},
    "encoding": {"p50Seconds": 180.0, "p90Seconds": 240.0},
    "qa": {"p50Seconds": 120.0, "p90Seconds": 180.0},
    "total": {"p50Seconds": 9278.85, "p90Seconds": 13076.1}
  }
}
```

The operator-authorization output filename is derived from the fresh
`outputMatrixId`. The horizontal command uses `-RenderProfilePath`. The dual
template includes explicit horizontal and vertical scenes, profiles, output
roots, source paths, operator authorization, and both authorization tokens,
including `-VerticalRenderProfilePath` and `-VerticalAuthorizationToken`.
Placeholders must be replaced by the operator; omission fails closed.

No command in the report is authorization by itself. Production remains
blocked until a human operator creates and supplies a separate exact
authorization after reviewing the complete bundle.

## Verification

Focused checks:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest `
  tests\test_andromeda_release_finalization.py -q `
  --basetemp ..\.pytest-release-finalization
.\.venv\Scripts\python.exe -m ruff check `
  app\cinematic\release_finalization.py `
  tests\test_andromeda_release_finalization.py
.\.venv\Scripts\python.exe -m mypy app\cinematic\release_finalization.py
```

The focused suite uses a real temporary Git repository and covers successful
assembly, direct Git-tree recomputation, dirty release-owned paths, missing
builder hashes, non-JSON suffix bypass, placeholder media, calibration
tampering, measured latest-frame publication latency and its 2.0-second
threshold, per-variant and aggregate forecast labels and tamper rejection,
exact verification-matrix coverage and honest skip handling, the LOW 480x270
full animatic contract, complete command arguments, output hashes, and
failure-atomic publication.
