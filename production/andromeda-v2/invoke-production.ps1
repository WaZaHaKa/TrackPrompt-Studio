[CmdletBinding()]
param(
    [ValidateSet("Inspect", "Preflight", "StartOrResume")]
    [string]$Action = "Inspect",
    [string]$ScenePath = "",
    [string]$OutputDirectory = "",
    [string]$AuthorizationToken = "",
    [switch]$EnableVertical,
    [string]$BlenderExecutable = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    [string]$PythonExecutable = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$renderScript = Join-Path $repositoryRoot "render-trackprompt-final.ps1"
$profilePath = Join-Path $repositoryRoot "render-profiles\trip-to-andromeda\andromeda-v2-horizontal-1080p-final.json"
$authorizationPath = Join-Path $PSScriptRoot "technical-authorization-v2.json"

if ($EnableVertical) {
    throw (
        "Vertical is an authored but disabled output variant. Enabling it requires a new " +
        "vertical calibration, aggregate SLA forecast, exact output matrix, and operator authorization."
    )
}

if ([string]::IsNullOrWhiteSpace($ScenePath)) {
    $ScenePath = Join-Path $repositoryRoot (
        "test-output\andromeda-v2-finish-line-20260723\" +
        "andromeda-v2-master-horizontal-release-v2.blend"
    )
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $repositoryRoot "final-output\andromeda-v2-horizontal"
}

foreach ($requiredFile in @($renderScript, $profilePath, $ScenePath)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required production file is unavailable: $requiredFile"
    }
}

$resolvedScene = (Resolve-Path -LiteralPath $ScenePath).Path
$resolvedProfile = (Resolve-Path -LiteralPath $profilePath).Path
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
$sceneSha256 = (Get-FileHash -LiteralPath $resolvedScene -Algorithm SHA256).Hash.ToLowerInvariant()
$profileSha256 = (Get-FileHash -LiteralPath $resolvedProfile -Algorithm SHA256).Hash.ToLowerInvariant()

$renderArguments = @{
    ApprovedScenePath = $resolvedScene
    RenderProfilePath = $resolvedProfile
    OutputDirectory = $resolvedOutput
    BlenderExecutable = $BlenderExecutable
    MissionControlJobId = "andromeda-v2-horizontal-final"
}
if (-not [string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $renderArguments.PythonExecutable = $PythonExecutable
}

if ($Action -eq "Inspect") {
    $renderArguments.DryRun = $true
    & $renderScript @renderArguments
    exit $LASTEXITCODE
}
if ($Action -eq "Preflight") {
    $renderArguments.Preflight = $true
    & $renderScript @renderArguments
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $authorizationPath -PathType Leaf)) {
    throw "The exact V2 technical authorization is unavailable: $authorizationPath"
}
$authorization = Get-Content -LiteralPath $authorizationPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (
    [string]$authorization.status -ne "technically-ready" -or
    -not [bool]$authorization.technicalReady -or
    -not [bool]$authorization.productionStartAllowed -or
    [string]$authorization.operatorStartGate.status -ne "authorized" -or
    -not [bool]$authorization.operatorStartGate.explicitFullRenderStartAuthorized
) {
    throw (
        "Production start is blocked. Technical readiness and an exact identity-bound " +
        "operator start authorization must both be present."
    )
}

$enabledVariantIds = @($authorization.identity.outputMatrix.enabledVariantIds)
if (
    $enabledVariantIds.Count -ne 1 -or
    [string]$enabledVariantIds[0] -ne "horizontal-16x9-1080p"
) {
    throw "The authorization does not bind the horizontal-only output matrix."
}
$horizontal = @(
    $authorization.identity.outputMatrix.variants |
        Where-Object { [string]$_.id -eq "horizontal-16x9-1080p" }
)
if ($horizontal.Count -ne 1) {
    throw "The authorization must contain exactly one horizontal output-variant identity."
}
if (
    [string]$horizontal[0].sceneSha256 -ne $sceneSha256 -or
    [string]$horizontal[0].renderProfileSha256 -ne $profileSha256
) {
    throw "The local scene or render profile does not match the exact authorized identity."
}
if (
    [string]$authorization.releaseIdentitySha256 -ne
    [string]$authorization.operatorStartGate.authorizedReleaseIdentitySha256
) {
    throw "The operator start gate does not bind the current release identity."
}
if ([string]::IsNullOrWhiteSpace($AuthorizationToken)) {
    throw "StartOrResume requires the exact scene/profile -AuthorizationToken."
}

$renderArguments.AuthorizationToken = $AuthorizationToken
& $renderScript @renderArguments
exit $LASTEXITCODE
