[CmdletBinding()]
param(
    [ValidateSet("Inspect", "Preflight", "StartOrResume")]
    [string]$Action = "Inspect",
    [string]$ScenePath = "",
    [string]$VerticalScenePath = "",
    [Alias("HorizontalRenderProfilePath")]
    [string]$RenderProfilePath = "",
    [string]$VerticalRenderProfilePath = "",
    [string]$OutputDirectory = "",
    [string]$VerticalOutputDirectory = "",
    [string]$AuthorizationToken = "",
    [string]$VerticalAuthorizationToken = "",
    [string]$SourceAudioPath = "",
    [string]$SourceCuePath = "",
    [string]$CalibrationPath = "",
    [string]$PackageManifestPath = "",
    [string]$TechnicalAuthorizationPath = "",
    [string]$OperatorAuthorizationPath = "",
    [switch]$EnableVertical,
    [string]$BlenderExecutable = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    [string]$PythonExecutable = ""
)

# TrackPrompt bootstrap: explicitly load Microsoft.PowerShell.Utility.
$script:TrackPromptUtilityManifest = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1'

if (-not (Test-Path -LiteralPath $script:TrackPromptUtilityManifest -PathType Leaf)) {
    throw "Required PowerShell utility module was not found: $script:TrackPromptUtilityManifest"
}

Import-Module `
    -Name $script:TrackPromptUtilityManifest `
    -Force `
    -ErrorAction Stop


Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$renderScript = Join-Path $repositoryRoot "render-trackprompt-final.ps1"
$operatorAuthorizationTool = Join-Path (
    $repositoryRoot
) "tools\andromeda_operator_authorization.py"
$defaultHorizontalProfilePath = Join-Path (
    $repositoryRoot
) "render-profiles\trip-to-andromeda\andromeda-v2-horizontal-1080p-final.json"
$defaultVerticalProfilePath = Join-Path (
    $repositoryRoot
) "render-profiles\trip-to-andromeda\andromeda-v2-vertical-1080x1920-final-optional.json"
$creativeAcceptancePath = Join-Path $PSScriptRoot "creative-acceptance.json"
$encodingProfilesPath = Join-Path $PSScriptRoot "encoding-profiles.json"
$defaultCalibrationPath = Join-Path $PSScriptRoot "v2-calibration.json"
$defaultPackageManifestPath = Join-Path $PSScriptRoot "package-manifest-v2.json"
$defaultTechnicalAuthorizationPath = Join-Path $PSScriptRoot "technical-authorization-v2.json"
$calibrationWasSupplied = -not [string]::IsNullOrWhiteSpace($CalibrationPath)
$packageManifestWasSupplied = -not [string]::IsNullOrWhiteSpace($PackageManifestPath)
$technicalAuthorizationWasSupplied = (
    -not [string]::IsNullOrWhiteSpace($TechnicalAuthorizationPath)
)

function Resolve-RequiredFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $candidate = Resolve-RepositoryPath -Path $Path
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "$Label is unavailable: $candidate"
    }
    return (Resolve-Path -LiteralPath $candidate).Path
}

function Resolve-RepositoryPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    return [IO.Path]::GetFullPath((Join-Path $repositoryRoot $Path))
}

function Resolve-Python {
    if (-not [string]::IsNullOrWhiteSpace($PythonExecutable)) {
        return Resolve-RequiredFile -Path $PythonExecutable -Label "Python executable"
    }
    $repositoryPython = Join-Path $repositoryRoot "backend\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $repositoryPython -PathType Leaf) {
        return (Resolve-Path -LiteralPath $repositoryPython).Path
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Python was not found. Supply -PythonExecutable explicitly."
    }
    return $command.Source
}

function Test-ExactFileBinding {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256
    )

    $resolved = Resolve-RequiredFile -Path $FilePath -Label $Label
    $actualSha256 = (
        Get-FileHash -LiteralPath $resolved -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "The local $Label file does not match the exact authorized identity."
    }
}

function Invoke-AuthorizationTool {
    param(
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $captured = @(
            & $script:ResolvedPython $script:OperatorAuthorizationTool @ArgumentList 2>&1
        )
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $lines = @($captured | ForEach-Object { [string]$_ })
    $jsonLine = @(
        $lines |
            Where-Object { $_.TrimStart().StartsWith("{") } |
            Select-Object -Last 1
    )
    $payload = $null
    if ($jsonLine.Count -eq 1) {
        try {
            $payload = $jsonLine[0] | ConvertFrom-Json
        }
        catch {
            $payload = $null
        }
    }
    if ($exitCode -ne 0) {
        $reason = if ($null -ne $payload -and $null -ne $payload.error) {
            "$($payload.error.code): $($payload.error.message)"
        }
        else {
            ($lines -join [Environment]::NewLine)
        }
        throw "$Description failed with exit code $exitCode. $reason"
    }
    if ($null -eq $payload) {
        throw "$Description did not return structured JSON."
    }
    return $payload
}

function Get-RenderProcessArguments {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Target,
        [Parameter(Mandatory = $true)]
        [ValidateSet("Inspect", "Preflight", "StartOrResume")]
        [string]$Mode
    )

    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $script:RenderScript,
        "-ApprovedScenePath", [string]$Target.ScenePath,
        "-RenderProfilePath", [string]$Target.ProfilePath,
        "-OutputDirectory", [string]$Target.OutputDirectory,
        "-MissionControlJobId", [string]$Target.MissionControlJobId,
        "-BlenderExecutable", $BlenderExecutable
    )
    if (-not [string]::IsNullOrWhiteSpace($script:ResolvedPython)) {
        $arguments += @("-PythonExecutable", $script:ResolvedPython)
    }
    if ($Mode -eq "Inspect") {
        $arguments += "-DryRun"
    }
    elseif ($Mode -eq "Preflight") {
        $arguments += "-Preflight"
    }
    else {
        $arguments += @("-AuthorizationToken", [string]$Target.AuthorizationToken)
    }
    if (
        $Mode -ne "StartOrResume" -and
        -not [string]::IsNullOrWhiteSpace([string]$Target.AuthorizationToken)
    ) {
        $arguments += @("-AuthorizationToken", [string]$Target.AuthorizationToken)
    }
    return $arguments
}

function Invoke-RenderTargetJson {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Target,
        [Parameter(Mandatory = $true)]
        [ValidateSet("Inspect", "Preflight")]
        [string]$Mode
    )

    $arguments = Get-RenderProcessArguments -Target $Target -Mode $Mode
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $captured = @(& $script:PowerShellExecutable @arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $lines = @($captured | ForEach-Object { [string]$_ })
    if ($exitCode -ne 0) {
        throw (
            "{0} {1} failed with exit code {2}. {3}" -f `
                [string]$Target.VariantId,
                $Mode,
                $exitCode,
                ($lines -join [Environment]::NewLine)
        )
    }
    try {
        return (($lines -join [Environment]::NewLine) | ConvertFrom-Json)
    }
    catch {
        throw (
            "{0} {1} did not return structured JSON." -f `
                [string]$Target.VariantId,
                $Mode
        )
    }
}

function Invoke-RenderTargetStart {
    param([Parameter(Mandatory = $true)][pscustomobject]$Target)

    $arguments = Get-RenderProcessArguments -Target $Target -Mode "StartOrResume"
    & $script:PowerShellExecutable @arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw (
            "{0} StartOrResume failed with exit code {1}." -f `
                [string]$Target.VariantId,
                $exitCode
        )
    }
}

function Get-AuthorizedOutputRoot {
    param([Parameter(Mandatory = $true)][string]$OutputPattern)

    $nativePattern = $OutputPattern.Replace("/", [IO.Path]::DirectorySeparatorChar)
    $absolutePattern = Join-Path $repositoryRoot $nativePattern
    $framesDirectory = Split-Path -Parent $absolutePattern
    return [IO.Path]::GetFullPath((Split-Path -Parent $framesDirectory))
}

$script:RenderScript = Resolve-RequiredFile `
    -Path $renderScript `
    -Label "Canonical resumable renderer"
$script:OperatorAuthorizationTool = Resolve-RequiredFile `
    -Path $operatorAuthorizationTool `
    -Label "Operator-authorization validator"
$script:ResolvedPython = Resolve-Python
$powerShellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
if ($null -eq $powerShellCommand) {
    throw "Windows PowerShell was not found."
}
$script:PowerShellExecutable = $powerShellCommand.Source

if ([string]::IsNullOrWhiteSpace($RenderProfilePath)) {
    $RenderProfilePath = $defaultHorizontalProfilePath
}
$resolvedHorizontalProfile = Resolve-RequiredFile `
    -Path $RenderProfilePath `
    -Label "Horizontal render profile"
if ([string]::IsNullOrWhiteSpace($ScenePath)) {
    $ScenePath = Join-Path $repositoryRoot (
        "test-output\andromeda-v2-finish-line-20260723\" +
        "andromeda-v2-master-horizontal-release-v2.blend"
    )
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $repositoryRoot "final-output\andromeda-v2-horizontal"
}
$targets = @(
    [pscustomobject]@{
        VariantId = "horizontal-16x9-1080p"
        ScenePath = Resolve-RequiredFile -Path $ScenePath -Label "Horizontal scene"
        ProfilePath = $resolvedHorizontalProfile
        OutputDirectory = Resolve-RepositoryPath -Path $OutputDirectory
        AuthorizationToken = $AuthorizationToken
        MissionControlJobId = "andromeda-v2-horizontal-final"
    }
)

if ($EnableVertical) {
    if ([string]::IsNullOrWhiteSpace($VerticalRenderProfilePath)) {
        $VerticalRenderProfilePath = $defaultVerticalProfilePath
    }
    $resolvedVerticalProfile = Resolve-RequiredFile `
        -Path $VerticalRenderProfilePath `
        -Label "Vertical render profile"
    if ([string]::IsNullOrWhiteSpace($VerticalScenePath)) {
        $VerticalScenePath = Join-Path $repositoryRoot (
            "test-output\andromeda-v2-finish-line-20260723\" +
            "andromeda-v2-master-vertical-release-v2.blend"
        )
    }
    if ([string]::IsNullOrWhiteSpace($VerticalOutputDirectory)) {
        $VerticalOutputDirectory = Join-Path (
            $repositoryRoot
        ) "final-output\andromeda-v2-vertical"
    }
    $targets += [pscustomobject]@{
        VariantId = "vertical-9x16-1080p"
        ScenePath = Resolve-RequiredFile -Path $VerticalScenePath -Label "Vertical scene"
        ProfilePath = $resolvedVerticalProfile
        OutputDirectory = Resolve-RepositoryPath -Path $VerticalOutputDirectory
        AuthorizationToken = $VerticalAuthorizationToken
        MissionControlJobId = "andromeda-v2-vertical-final"
    }
}

if ($Action -eq "Inspect" -or $Action -eq "Preflight") {
    $reports = @()
    foreach ($target in $targets) {
        $plan = Invoke-RenderTargetJson -Target $target -Mode $Action
        $reports += [pscustomobject]@{
            variantId = [string]$target.VariantId
            scenePath = [string]$target.ScenePath
            renderProfilePath = [string]$target.ProfilePath
            outputDirectory = [string]$target.OutputDirectory
            plan = $plan
        }
    }
    [pscustomobject]@{
        schemaVersion = "1.0.0"
        kind = "andromeda-v2-production-wrapper-inspection"
        action = $Action
        enableVertical = [bool]$EnableVertical
        enabledVariantIds = @($targets | ForEach-Object { [string]$_.VariantId })
        productionStartAttempted = $false
        targets = $reports
    } | ConvertTo-Json -Depth 40
    exit 0
}

if ($EnableVertical -and (
    -not $calibrationWasSupplied -or
    -not $packageManifestWasSupplied -or
    -not $technicalAuthorizationWasSupplied
)) {
    throw (
        "Vertical StartOrResume requires explicit -CalibrationPath, " +
        "-PackageManifestPath, and -TechnicalAuthorizationPath values for the " +
        "separately authored horizontal-plus-vertical release. The committed " +
        "horizontal-only files cannot authorize vertical."
    )
}
if (-not $calibrationWasSupplied) {
    $CalibrationPath = $defaultCalibrationPath
}
if (-not $packageManifestWasSupplied) {
    $PackageManifestPath = $defaultPackageManifestPath
}
if (-not $technicalAuthorizationWasSupplied) {
    $TechnicalAuthorizationPath = $defaultTechnicalAuthorizationPath
}
if ([string]::IsNullOrWhiteSpace($OperatorAuthorizationPath)) {
    throw (
        "StartOrResume requires -OperatorAuthorizationPath pointing to the " +
        "separate local artifact created by new-operator-authorization.ps1."
    )
}
if ([string]::IsNullOrWhiteSpace($SourceAudioPath)) {
    throw "StartOrResume requires -SourceAudioPath for exact private-source validation."
}
if ([string]::IsNullOrWhiteSpace($SourceCuePath)) {
    throw "StartOrResume requires -SourceCuePath for exact private-source validation."
}
if ([string]::IsNullOrWhiteSpace($AuthorizationToken)) {
    throw "StartOrResume requires the exact horizontal scene/profile -AuthorizationToken."
}
if (
    $EnableVertical -and
    [string]::IsNullOrWhiteSpace($VerticalAuthorizationToken)
) {
    throw (
        "Vertical StartOrResume requires the exact vertical scene/profile " +
        "-VerticalAuthorizationToken."
    )
}

$resolvedCalibration = Resolve-RequiredFile `
    -Path $CalibrationPath `
    -Label "Final calibration"
$resolvedPackageManifest = Resolve-RequiredFile `
    -Path $PackageManifestPath `
    -Label "Final package manifest"
$resolvedTechnicalAuthorization = Resolve-RequiredFile `
    -Path $TechnicalAuthorizationPath `
    -Label "Technical authorization"
$resolvedOperatorAuthorization = Resolve-RequiredFile `
    -Path $OperatorAuthorizationPath `
    -Label "Separate operator-start authorization"

$authorizationArguments = @(
    "validate",
    "--repository-root", $repositoryRoot,
    "--calibration", $resolvedCalibration,
    "--package-manifest", $resolvedPackageManifest,
    "--technical-authorization", $resolvedTechnicalAuthorization,
    "--operator-authorization", $resolvedOperatorAuthorization
)
if ($EnableVertical) {
    $authorizationArguments += "--enable-vertical"
}
$authorizedRelease = Invoke-AuthorizationTool `
    -ArgumentList $authorizationArguments `
    -Description "Exact operator-start authorization validation"

foreach ($target in $targets) {
    $variant = @(
        $authorizedRelease.variants |
            Where-Object { [string]$_.id -eq [string]$target.VariantId }
    )
    if ($variant.Count -ne 1) {
        throw (
            "The operator-authorized matrix does not contain exactly one {0} identity." -f `
                [string]$target.VariantId
        )
    }
    Test-ExactFileBinding `
        -Label ("{0} scene" -f [string]$target.VariantId) `
        -FilePath ([string]$target.ScenePath) `
        -ExpectedSha256 ([string]$variant[0].sceneSha256)
    Test-ExactFileBinding `
        -Label ("{0} render profile" -f [string]$target.VariantId) `
        -FilePath ([string]$target.ProfilePath) `
        -ExpectedSha256 ([string]$variant[0].renderProfileSha256)
    $authorizedOutputRoot = Get-AuthorizedOutputRoot `
        -OutputPattern ([string]$variant[0].outputPattern)
    if (
        $authorizedOutputRoot.TrimEnd("\") -ine
        ([string]$target.OutputDirectory).TrimEnd("\")
    ) {
        throw (
            "The {0} output directory does not match the exact authorized output pattern." -f `
                [string]$target.VariantId
        )
    }
}

Test-ExactFileBinding `
    -Label "source-audio" `
    -FilePath $SourceAudioPath `
    -ExpectedSha256 ([string]$authorizedRelease.sourceAudioSha256)
Test-ExactFileBinding `
    -Label "source-cue" `
    -FilePath $SourceCuePath `
    -ExpectedSha256 ([string]$authorizedRelease.sourceCueSha256)
Test-ExactFileBinding `
    -Label "owner creative-acceptance" `
    -FilePath $creativeAcceptancePath `
    -ExpectedSha256 ([string]$authorizedRelease.ownerCreativeAcceptanceSha256)
Test-ExactFileBinding `
    -Label "encoding-profiles" `
    -FilePath $encodingProfilesPath `
    -ExpectedSha256 ([string]$authorizedRelease.encodingProfilesSha256)

# Validate every selected scene/profile token, Blender version, output path, and
# storage requirement before allowing the first enabled variant to start.
$preflightReports = @()
foreach ($target in $targets) {
    $preflightReports += Invoke-RenderTargetJson -Target $target -Mode "Preflight"
}
Write-Host (
    "Validated separate operator authorization {0} for release {1} and matrix {2}." -f `
        [string]$authorizedRelease.authorizationId,
        [string]$authorizedRelease.releaseIdentitySha256,
        [string]$authorizedRelease.outputMatrixId
) -ForegroundColor Green
Write-Host (
    "All {0} enabled variant preflights passed before any render start." -f `
        $targets.Count
) -ForegroundColor Green

foreach ($target in $targets) {
    Invoke-RenderTargetStart -Target $target
}
