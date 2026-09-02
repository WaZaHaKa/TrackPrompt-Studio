# Andromeda V2 production foundation

This directory promotes the immutable R13.1 proof into a new, owner-attested
creative baseline without modifying or replacing any historical proof file.
The acceptance is creative scope only. It does not authorize a final render.
Codex records the owner-supplied attestation; Codex is not identified as the
human approver. The acceptance explicitly preserves technical QA, fresh scene
and profile checks, cloud-provisioning controls, and the final production gate.

The locked look profile is aspect-neutral. Horizontal and vertical outputs use
independently authored composition profiles; vertical is optional and disabled
by default. A crop of the horizontal output is never an acceptable vertical
composition. Every authored shot declares its camera rig and lens, three depth
layers, dominant shape, secondary action, lighting identity, cut intent,
bounded smoothed audio response, render complexity, and separate horizontal
and vertical framing overrides.

The R13.1 profile locks Blender 5.2 Eevee, 64 temporal and 32 volumetric
samples, temporal antialiasing/reprojection, AgX Medium High Contrast, DITHERED
transparency, motion blur off, at most two transparent layers, one localized
gate membrane, and no compositor denoising. It also locks independent
protagonist motion, authored camera lag/foreground parallax, and the prohibition
on raw-audio-driven major travel.

`production-authorization.json` and `technical-authorization-v2.json` remain
deliberately operator-gated. They are immutable technical evidence, not a place
to record the operator's production decision. Production may start only after
the full source-audio animatic, representative human visual QA, V2 calibration,
deterministic-effect and disk checks, the exact enabled-matrix 24-hour SLA, and
a separate local operator authorization all pass.

## Current visual release hold

The current seven horizontal calibration frames establish measured render time;
they do **not** establish visual equivalence to the preserved R13.1 contact
sheets. The 2026-07-23 human visual audit found the V2 calibration output
sparse and blockout-like: Awakening still contains floating or isolated
elements, and Arrival does not yet show an unmistakable destination.

Treat the current package and technical authorization as preserved historical
technical evidence, not as proof that the finish-line sprint is complete or
that the final scene meets the locked R13.1 artistic target. Do not create or
use an operator-start artifact for this release until a corrected, bounded
final-quality visual proof is reviewed. Any source, scene, profile, or package
change made to correct the visual gap requires fresh exact-identity evidence,
calibration, package binding, and technical authorization. Do not rewrite the
existing immutable artifacts to make them appear current.

`release-hold.json` records this additive, exact-release hold. The operator
authorization CLI checks it before inspect, create, or validate and fails
closed when its release/package/calibration/technical-authorization bindings
match. A missing, invalid, or same-identity-mismatched hold record also fails
closed; deleting or editing the record cannot authorize production.

`package-manifest.json` hash-binds this directory to the versioned StoryPlan and
ShotPlan templates. Private source audio and cue files remain local and are
bound by digest rather than committed.

## Final V2 operator path

The tracked historical compatibility default is
`render-profiles/trip-to-andromeda/andromeda-v2-horizontal-1080p-final.json`.
A corrected fresh release must pass its generated profile and scene paths
explicitly with `-RenderProfilePath` and `-ScenePath`; it must never silently
reuse that default. `-HorizontalRenderProfilePath` remains a compatibility
alias, while generated release commands use the lower-level contract name
`-RenderProfilePath`. The wrapper reuses the canonical resumable renderer.
Inspection and preflight are non-starting operations and do not require an
operator artifact:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 -Action Inspect

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 -Action Preflight
```

After the current visual release hold is resolved with new exact-identity
evidence, creating the separate operator artifact is an explicit owner/operator
action.
The command displays the exact release hash, matrix, variants, and a long
confirmation phrase. It writes only after that phrase is typed exactly, never
edits the committed technical authorization, never overwrites an existing
authorization, and never starts a render:

```powershell
$freshRelease = ".\test-output\<fresh-proof>\release\horizontal-only"
$freshCalibration = Join-Path $freshRelease "v2-calibration.json"
$freshPackageManifest = Join-Path $freshRelease "package-manifest-v2.json"
$freshTechnicalAuthorization = Join-Path (
  $freshRelease
) "technical-authorization-v2.json"
$freshHorizontalProfile = (
  ".\test-output\<fresh-proof>\release\profiles\" +
  "andromeda-v2-horizontal-1080p-final-<release-tag>.json"
)
$freshHorizontalScene = `
  ".\test-output\<fresh-proof>\andromeda-v2-master-horizontal.blend"

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\new-operator-authorization.ps1 `
  -CalibrationPath $freshCalibration `
  -PackageManifestPath $freshPackageManifest `
  -TechnicalAuthorizationPath $freshTechnicalAuthorization
```

The resulting file is local under `.operator-authorizations/` and is ignored by
Git. Keep it with the private operator records. Pass its exact path to the
starting command:

```powershell
# Run only after the owner/operator intentionally created the exact artifact.
$sourceAudioPath = "<private source-audio path>"
$sourceCuePath = "<private visual-cues path>"
$freshMatrixId = (
  Get-Content -Raw -LiteralPath $freshCalibration | ConvertFrom-Json
).identity.outputMatrix.matrixId
$operatorAuthorizationPath = Join-Path `
  ".\production\andromeda-v2\.operator-authorizations" `
  "$freshMatrixId.operator-start-authorization.json"
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action StartOrResume `
  -ScenePath $freshHorizontalScene `
  -RenderProfilePath $freshHorizontalProfile `
  -CalibrationPath $freshCalibration `
  -PackageManifestPath $freshPackageManifest `
  -TechnicalAuthorizationPath $freshTechnicalAuthorization `
  -SourceAudioPath $sourceAudioPath `
  -SourceCuePath $sourceCuePath `
  -OperatorAuthorizationPath $operatorAuthorizationPath `
  -AuthorizationToken "<exact scene/profile token>"
```

`StartOrResume` validates and skips already published frames, so the same
command safely resumes an interrupted job. It also hashes the private audio and
visual-cues files locally and refuses any identity drift; their paths are never
committed. Before the first variant can start, it validates the separate
operator artifact, the complete package manifest and every artifact it binds,
calibration and technical-authorization hashes, exact release identity and
enabled matrix, scene/profile identities, output roots, both private-source
hashes, scene/profile tokens, Blender version, and storage for every enabled
variant.

## Optional vertical workflow

The independently authored vertical profile and scene can be inspected or
preflighted now without enabling production or creating authorization:

```powershell
$freshVerticalProfile = (
  ".\test-output\<fresh-proof>\release\profiles\" +
  "andromeda-v2-vertical-1080x1920-final-<release-tag>.json"
)
$freshVerticalScene = `
  ".\test-output\<fresh-proof>\andromeda-v2-master-vertical.blend"

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action Inspect `
  -EnableVertical `
  -ScenePath $freshHorizontalScene `
  -VerticalScenePath $freshVerticalScene `
  -RenderProfilePath $freshHorizontalProfile `
  -VerticalRenderProfilePath $freshVerticalProfile

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action Preflight `
  -EnableVertical `
  -ScenePath $freshHorizontalScene `
  -VerticalScenePath $freshVerticalScene `
  -RenderProfilePath $freshHorizontalProfile `
  -VerticalRenderProfilePath $freshVerticalProfile
```

The current committed V2 package, calibration, and technical authorization are
horizontal-only and cannot authorize vertical. A vertical production decision
requires a separately authored horizontal-plus-vertical package manifest,
calibration, and technical-authorization files. They must bind both exact
scene/profile identities, contain completed calibration for both variants,
include the aggregate matrix SLA and disk forecast, and remain technically
ready but operator-gated. After those documents exist, create a new exact
dual-matrix operator artifact:

```powershell
$dualCalibration = "<exact dual-matrix calibration path>"
$dualPackageManifest = "<exact dual-matrix package-manifest path>"
$dualTechnicalAuthorization = "<exact dual-matrix technical-authorization path>"

powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\new-operator-authorization.ps1 `
  -EnableVertical `
  -CalibrationPath $dualCalibration `
  -PackageManifestPath $dualPackageManifest `
  -TechnicalAuthorizationPath $dualTechnicalAuthorization
```

Only then can the same wrapper start or resume both isolated output sequences:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\production\andromeda-v2\invoke-production.ps1 `
  -Action StartOrResume `
  -EnableVertical `
  -ScenePath $freshHorizontalScene `
  -VerticalScenePath $freshVerticalScene `
  -RenderProfilePath $freshHorizontalProfile `
  -VerticalRenderProfilePath $freshVerticalProfile `
  -CalibrationPath $dualCalibration `
  -PackageManifestPath $dualPackageManifest `
  -TechnicalAuthorizationPath $dualTechnicalAuthorization `
  -OperatorAuthorizationPath "<exact dual-matrix operator artifact path>" `
  -SourceAudioPath $sourceAudioPath `
  -SourceCuePath $sourceCuePath `
  -AuthorizationToken "<exact horizontal scene/profile token>" `
  -VerticalAuthorizationToken "<exact vertical scene/profile token>"
```

Any stale release hash, changed package artifact, changed technical
authorization, changed calibration, wrong enabled matrix, scene/profile drift,
wrong output root, or missing token fails before the horizontal render is
allowed to start.
