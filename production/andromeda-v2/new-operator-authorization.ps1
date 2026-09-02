[CmdletBinding()]
param(
    [string]$CalibrationPath = "",
    [string]$PackageManifestPath = "",
    [string]$TechnicalAuthorizationPath = "",
    [string]$OutputPath = "",
    [switch]$EnableVertical,
    [string]$PythonExecutable = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$toolPath = Join-Path $repositoryRoot "tools\andromeda_operator_authorization.py"
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

function Invoke-AuthorizationTool {
    param(
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $captured = @(& $script:ResolvedPython $script:ToolPath @ArgumentList 2>&1)
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

if ($EnableVertical -and (
    -not $calibrationWasSupplied -or
    -not $packageManifestWasSupplied -or
    -not $technicalAuthorizationWasSupplied
)) {
    throw (
        "Vertical authorization requires explicit -CalibrationPath, " +
        "-PackageManifestPath, and -TechnicalAuthorizationPath values for a " +
        "separately authored, horizontal-plus-vertical release. The committed " +
        "horizontal-only package is not modified or promoted."
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

$script:ResolvedPython = Resolve-Python
$script:ToolPath = Resolve-RequiredFile `
    -Path $toolPath `
    -Label "Andromeda operator-authorization tool"
$resolvedCalibration = Resolve-RequiredFile `
    -Path $CalibrationPath `
    -Label "Final calibration"
$resolvedPackageManifest = Resolve-RequiredFile `
    -Path $PackageManifestPath `
    -Label "Final package manifest"
$resolvedTechnicalAuthorization = Resolve-RequiredFile `
    -Path $TechnicalAuthorizationPath `
    -Label "Technical authorization"

$inspectArguments = @(
    "inspect",
    "--repository-root", $repositoryRoot,
    "--calibration", $resolvedCalibration,
    "--package-manifest", $resolvedPackageManifest,
    "--technical-authorization", $resolvedTechnicalAuthorization
)
if ($EnableVertical) {
    $inspectArguments += "--enable-vertical"
}
$context = Invoke-AuthorizationTool `
    -ArgumentList $inspectArguments `
    -Description "Operator-authorization inspection"

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $authorizationDirectory = Join-Path $PSScriptRoot ".operator-authorizations"
    $OutputPath = Join-Path $authorizationDirectory (
        "{0}.operator-start-authorization.json" -f [string]$context.outputMatrixId
    )
}
$resolvedOutput = Resolve-RepositoryPath -Path $OutputPath

Write-Host "No render will be started by this command." -ForegroundColor Yellow
Write-Host (
    "Release identity: {0}" -f [string]$context.releaseIdentitySha256
) -ForegroundColor Cyan
Write-Host (
    "Enabled matrix: {0} [{1}]" -f `
        [string]$context.outputMatrixId,
        (@($context.enabledVariantIds) -join ", ")
) -ForegroundColor Cyan
Write-Host "Type the following phrase exactly to create the separate local artifact:"
Write-Host ([string]$context.requiredTypedConfirmation) -ForegroundColor Yellow
$typedConfirmation = Read-Host "Exact confirmation"

$createArguments = @(
    "create",
    "--repository-root", $repositoryRoot,
    "--calibration", $resolvedCalibration,
    "--package-manifest", $resolvedPackageManifest,
    "--technical-authorization", $resolvedTechnicalAuthorization,
    "--output", $resolvedOutput,
    "--typed-confirmation", $typedConfirmation
)
if ($EnableVertical) {
    $createArguments += "--enable-vertical"
}
$result = Invoke-AuthorizationTool `
    -ArgumentList $createArguments `
    -Description "Operator-authorization creation"

Write-Host (
    "Created a separate local operator-start authorization. No render was started: {0}" -f `
        $resolvedOutput
) -ForegroundColor Green
$result | ConvertTo-Json -Depth 20
