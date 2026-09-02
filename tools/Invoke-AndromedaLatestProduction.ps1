#requires -Version 5.1
<#
.SYNOPSIS
Discovers the newest coherent Andromeda V2 production release, opens Mission
Control, performs the canonical inspection/preflight, and—after the repository's
exact operator gates are satisfied—starts or resumes the render. In
StartAndEncode mode it then hands encoding to Mission Control and waits for the
verified delivery/master outputs.

.DESCRIPTION
This script is a fail-closed operator helper. It does not bypass release holds,
technical authorization, scene/profile tokens, the separate operator-start
artifact, output-matrix rules, storage checks, the GPU mutex, or frame/encode
validation. "Newest" is used only to order complete release bundles; all
identity checks remain owned by the repository's canonical production wrapper.

The script deliberately leaves the final Encode action inside Mission Control.
The documented workflow requires an explicit UI confirmation for
"Encode delivery + master" and no stable public auto-encode API is part of the
published operator contract. After that single click, this script watches the
managed outputs until delivery, master, and QA artifacts are present.

.EXAMPLE
.\tools\Invoke-AndromedaLatestProduction.ps1 -Mode Discover

.EXAMPLE
.\tools\Invoke-AndromedaLatestProduction.ps1 -Mode Preflight

.EXAMPLE
.\tools\Invoke-AndromedaLatestProduction.ps1 -Mode StartAndEncode

.EXAMPLE
.\tools\Invoke-AndromedaLatestProduction.ps1 -Mode StartAndEncode `
  -SourceAudioPath "C:\Media\DJ WaZaHaKa - Trip to Andromeda.wav" `
  -SourceCuePath "C:\Media\visual-cues.json"

.EXAMPLE
# Only works when the newest release is a separately authored, calibrated,
# technically authorized dual-output matrix.
.\tools\Invoke-AndromedaLatestProduction.ps1 -Mode StartAndEncode -EnableVertical
#>

[CmdletBinding()]
param(
    [ValidateSet('Discover', 'Preflight', 'Start', 'StartAndEncode')]
    [string]$Mode = 'Preflight',

    [string]$RepositoryRoot = 'C:\Users\theon\GitHub\TrackPrompt-Studio',

    # Optional exact bundle directory or package-manifest-v2.json. When omitted,
    # the newest complete bundle below test-output/production is selected.
    [string]$ReleaseBundlePath,

    # Horizontal 1920x1080 is the default required matrix. Vertical is optional
    # and succeeds only when the selected release itself enables and authorizes
    # the independently authored vertical variant.
    [switch]$EnableVertical,

    [string]$SourceAudioPath,
    [string]$SourceCuePath,

    # Additional roots used only to locate private source files by the SHA-256
    # and byte length recorded in the release report.
    [string[]]$SourceSearchRoot = @(),

    # Normally discovered from a non-placeholder command template or derived
    # from the exact scene/profile hashes. Explicit values remain subject to the
    # canonical wrapper's exact validation.
    [string]$AuthorizationToken,
    [string]$VerticalAuthorizationToken,

    # By default a complete but blocked newest release stops the run. This
    # switch permits testing older complete candidates, but each still has to
    # pass the current canonical Inspect and Preflight paths.
    [switch]$AllowOlderCompatibleRelease,

    [switch]$SkipDashboardLaunch,

    [ValidateRange(1, 72)]
    [int]$RenderWaitHours = 30,

    [ValidateRange(1, 24)]
    [int]$EncodeWaitHours = 8,

    [ValidateRange(1, 240)]
    [int]$EncodeStartWaitMinutes = 60,

    [ValidateRange(2, 120)]
    [int]$PollSeconds = 10
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
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:RunStartedAt = [DateTimeOffset]::Now
$script:RunStateDirectory = $null
$script:SelectedCandidate = $null

function Write-Stage {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host ("==> {0}" -f $Message) -ForegroundColor Cyan
}

function Write-Notice {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ("    {0}" -f $Message) -ForegroundColor Gray
}

function Write-WarningLine {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ("WARNING: {0}" -f $Message) -ForegroundColor Yellow
}

function Throw-OperatorError {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw $Message
}

function Resolve-ExistingFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Label = 'File'
    )
    if ([string]::IsNullOrWhiteSpace($Path)) {
        Throw-OperatorError "$Label path is empty."
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Throw-OperatorError "$Label does not exist: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Resolve-ExistingDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Label = 'Directory'
    )
    if ([string]::IsNullOrWhiteSpace($Path)) {
        Throw-OperatorError "$Label path is empty."
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        Throw-OperatorError "$Label does not exist: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Read-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Label = 'JSON file'
    )
    $resolved = Resolve-ExistingFile -Path $Path -Label $Label
    try {
        return (Get-Content -Raw -LiteralPath $resolved -Encoding UTF8 | ConvertFrom-Json)
    }
    catch {
        Throw-OperatorError "$Label is not valid JSON: $resolved`n$($_.Exception.Message)"
    }
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Get-JsonLeaves {
    param(
        [Parameter(Mandatory = $false)]$Value,
        [string]$Path = '$',
        [string]$Name = ''
    )

    if ($null -eq $Value) {
        return
    }

    if (($Value -is [string]) -or ($Value -is [ValueType])) {
        [pscustomobject]@{
            Path  = $Path
            Name  = $Name
            Value = $Value
        }
        return
    }

    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($key in $Value.Keys) {
            Get-JsonLeaves -Value $Value[$key] -Path ("{0}.{1}" -f $Path, $key) -Name ([string]$key)
        }
        return
    }

    if (($Value -is [System.Collections.IEnumerable]) -and -not ($Value -is [string])) {
        $index = 0
        foreach ($item in $Value) {
            Get-JsonLeaves -Value $item -Path ("{0}[{1}]" -f $Path, $index) -Name ([string]$index)
            $index++
        }
        return
    }

    foreach ($property in @($Value.PSObject.Properties)) {
        Get-JsonLeaves -Value $property.Value -Path ("{0}.{1}" -f $Path, $property.Name) -Name $property.Name
    }
}

function Get-RoleRecords {
    param(
        [Parameter(Mandatory = $false)]$Value,
        [string]$SourcePath,
        [string]$Path = '$'
    )

    if ($null -eq $Value) {
        return
    }

    if (($Value -is [string]) -or ($Value -is [ValueType])) {
        return
    }

    if (($Value -is [System.Collections.IEnumerable]) -and -not ($Value -is [string]) -and -not ($Value -is [pscustomobject])) {
        $index = 0
        foreach ($item in $Value) {
            Get-RoleRecords -Value $item -SourcePath $SourcePath -Path ("{0}[{1}]" -f $Path, $index)
            $index++
        }
        return
    }

    $properties = @($Value.PSObject.Properties)
    if ($properties.Count -gt 0) {
        $roleProperty = @($properties | Where-Object { $_.Name -ieq 'role' } | Select-Object -First 1)
        if ($roleProperty.Count -gt 0 -and -not [string]::IsNullOrWhiteSpace([string]$roleProperty[0].Value)) {
            $pathProperty = @($properties | Where-Object { $_.Name -ieq 'path' -or $_.Name -ieq 'file' } | Select-Object -First 1)
            $shaProperty = @($properties | Where-Object { $_.Name -ieq 'sha256' } | Select-Object -First 1)
            $sizeProperty = @($properties | Where-Object { $_.Name -ieq 'sizeBytes' } | Select-Object -First 1)

            [pscustomobject]@{
                Role       = ([string]$roleProperty[0].Value).ToLowerInvariant()
                Path       = $(if ($pathProperty.Count -gt 0) { [string]$pathProperty[0].Value } else { $null })
                Sha256     = $(if ($shaProperty.Count -gt 0) { ([string]$shaProperty[0].Value).ToLowerInvariant() } else { $null })
                SizeBytes  = $(if ($sizeProperty.Count -gt 0) { [int64]$sizeProperty[0].Value } else { $null })
                JsonPath   = $Path
                SourcePath = $SourcePath
            }
        }

        foreach ($property in $properties) {
            Get-RoleRecords -Value $property.Value -SourcePath $SourcePath -Path ("{0}.{1}" -f $Path, $property.Name)
        }
    }
}

function Get-FirstLeafValue {
    param(
        [Parameter(Mandatory = $true)]$Json,
        [Parameter(Mandatory = $true)][string[]]$Names,
        [string]$PathRegex
    )

    $nameSet = @{}
    foreach ($name in $Names) {
        $nameSet[$name.ToLowerInvariant()] = $true
    }

    foreach ($leaf in @(Get-JsonLeaves -Value $Json)) {
        $leafName = ([string]$leaf.Name).ToLowerInvariant()
        if (-not $nameSet.ContainsKey($leafName)) {
            continue
        }
        if (-not [string]::IsNullOrWhiteSpace($PathRegex) -and ([string]$leaf.Path) -notmatch $PathRegex) {
            continue
        }
        return $leaf.Value
    }
    return $null
}

function Get-ProofRootFromBundle {
    param([Parameter(Mandatory = $true)][string]$BundleDirectory)
    $cursor = Get-Item -LiteralPath $BundleDirectory
    while ($null -ne $cursor) {
        if ($cursor.Name -ieq 'release' -and $null -ne $cursor.Parent) {
            return $cursor.Parent.FullName
        }
        $cursor = $cursor.Parent
    }
    return (Split-Path -Parent $BundleDirectory)
}

function Resolve-ReferencedPath {
    param(
        [string]$PathValue,
        [Parameter(Mandatory = $true)][string[]]$BaseDirectories
    )

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $null
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($PathValue.Trim())
    $normalized = $expanded -replace '/', [IO.Path]::DirectorySeparatorChar

    if ([IO.Path]::IsPathRooted($normalized)) {
        if (Test-Path -LiteralPath $normalized -PathType Leaf) {
            return (Resolve-Path -LiteralPath $normalized).Path
        }
        return $null
    }

    foreach ($base in $BaseDirectories | Select-Object -Unique) {
        if ([string]::IsNullOrWhiteSpace($base)) {
            continue
        }
        $candidate = Join-Path $base $normalized
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Resolve-RoleArtifact {
    param(
        [Parameter(Mandatory = $true)]$RoleRecords,
        [Parameter(Mandatory = $true)][string[]]$RoleNames,
        [Parameter(Mandatory = $true)][string[]]$BaseDirectories
    )

    foreach ($roleName in $RoleNames) {
        foreach ($record in @($RoleRecords | Where-Object { $_.Role -ieq $roleName })) {
            $resolved = Resolve-ReferencedPath -PathValue $record.Path -BaseDirectories $BaseDirectories
            if ($null -eq $resolved) {
                continue
            }
            if (-not [string]::IsNullOrWhiteSpace([string]$record.Sha256)) {
                $actual = Get-Sha256 -Path $resolved
                if ($actual -ne ([string]$record.Sha256).ToLowerInvariant()) {
                    Throw-OperatorError "Role '$roleName' points to a file with the wrong SHA-256: $resolved"
                }
            }
            return $resolved
        }
    }
    return $null
}

function Get-EnabledVariantIds {
    param([Parameter(Mandatory = $true)]$Json)

    $ids = New-Object 'System.Collections.Generic.List[string]'

    function Visit-VariantNode {
        param($Node)
        if ($null -eq $Node) { return }
        if (($Node -is [string]) -or ($Node -is [ValueType])) { return }

        if (($Node -is [System.Collections.IEnumerable]) -and -not ($Node -is [string]) -and -not ($Node -is [pscustomobject])) {
            foreach ($item in $Node) { Visit-VariantNode -Node $item }
            return
        }

        $properties = @($Node.PSObject.Properties)
        $enabledProperty = @($properties | Where-Object { $_.Name -ieq 'enabled' } | Select-Object -First 1)
        $idProperty = @($properties | Where-Object { $_.Name -ieq 'outputVariantId' -or $_.Name -ieq 'variantId' } | Select-Object -First 1)
        $enabledValue = if ($enabledProperty.Count -gt 0) { $enabledProperty[0].Value } else { $null }
        if ($enabledProperty.Count -gt 0 -and $idProperty.Count -gt 0 -and ($enabledValue -is [bool]) -and $enabledValue) {
            $ids.Add([string]$idProperty[0].Value)
        }

        foreach ($property in $properties) {
            if ($property.Name -ieq 'enabledVariantIds' -and ($property.Value -is [System.Collections.IEnumerable])) {
                foreach ($id in $property.Value) {
                    if (-not [string]::IsNullOrWhiteSpace([string]$id)) {
                        $ids.Add([string]$id)
                    }
                }
            }
            Visit-VariantNode -Node $property.Value
        }
    }

    Visit-VariantNode -Node $Json
    return @($ids | Sort-Object -Unique)
}

function Get-ReleaseTimestamp {
    param(
        [Parameter(Mandatory = $true)]$ReportJson,
        [Parameter(Mandatory = $true)][IO.FileInfo]$PackageFile
    )
    foreach ($name in @('recordedAt', 'generatedAt', 'finalizedAt', 'createdAt')) {
        $raw = Get-FirstLeafValue -Json $ReportJson -Names @($name)
        if ($null -ne $raw) {
            $parsed = [DateTimeOffset]::MinValue
            if ([DateTimeOffset]::TryParse([string]$raw, [ref]$parsed)) {
                return $parsed
            }
        }
    }
    return [DateTimeOffset]($PackageFile.LastWriteTimeUtc)
}

function New-ReleaseCandidate {
    param(
        [Parameter(Mandatory = $true)][IO.FileInfo]$PackageFile,
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    $bundleDirectory = $PackageFile.Directory.FullName
    $calibrationPath = Join-Path $bundleDirectory 'v2-calibration.json'
    $technicalAuthorizationPath = Join-Path $bundleDirectory 'technical-authorization-v2.json'
    $releaseReportPath = Join-Path $bundleDirectory 'evidence\release-report.json'
    $errors = New-Object 'System.Collections.Generic.List[string]'

    foreach ($required in @($calibrationPath, $technicalAuthorizationPath, $releaseReportPath)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            $errors.Add("Missing required bundle file: $required")
        }
    }

    if ($errors.Count -gt 0) {
        return [pscustomobject]@{
            PackageManifestPath       = $PackageFile.FullName
            BundleDirectory          = $bundleDirectory
            Complete                 = $false
            Errors                   = @($errors)
            ReleaseTimestamp         = [DateTimeOffset]($PackageFile.LastWriteTimeUtc)
        }
    }

    try {
        $package = Read-JsonFile -Path $PackageFile.FullName -Label 'Package manifest'
        $calibration = Read-JsonFile -Path $calibrationPath -Label 'V2 calibration'
        $technicalAuthorization = Read-JsonFile -Path $technicalAuthorizationPath -Label 'Technical authorization'
        $releaseReport = Read-JsonFile -Path $releaseReportPath -Label 'Release report'
    }
    catch {
        $errors.Add($_.Exception.Message)
        return [pscustomobject]@{
            PackageManifestPath       = $PackageFile.FullName
            BundleDirectory          = $bundleDirectory
            Complete                 = $false
            Errors                   = @($errors)
            ReleaseTimestamp         = [DateTimeOffset]($PackageFile.LastWriteTimeUtc)
        }
    }

    $proofRoot = Get-ProofRootFromBundle -BundleDirectory $bundleDirectory
    $bases = @(
        $RepoRoot,
        $proofRoot,
        $bundleDirectory,
        (Split-Path -Parent $releaseReportPath),
        (Join-Path $proofRoot 'release'),
        (Join-Path $proofRoot 'release\profiles')
    )

    $roleRecords = @()
    $roleRecords += @(Get-RoleRecords -Value $releaseReport -SourcePath $releaseReportPath)
    $roleRecords += @(Get-RoleRecords -Value $package -SourcePath $PackageFile.FullName)
    $roleRecords += @(Get-RoleRecords -Value $calibration -SourcePath $calibrationPath)
    $roleRecords += @(Get-RoleRecords -Value $technicalAuthorization -SourcePath $technicalAuthorizationPath)

    $horizontalScene = Resolve-RoleArtifact -RoleRecords $roleRecords -RoleNames @(
        'final-scene', 'horizontal-scene', 'horizontal-master-scene'
    ) -BaseDirectories $bases
    $verticalScene = Resolve-RoleArtifact -RoleRecords $roleRecords -RoleNames @(
        'vertical-master-scene', 'vertical-scene'
    ) -BaseDirectories $bases
    $horizontalProfile = Resolve-RoleArtifact -RoleRecords $roleRecords -RoleNames @(
        'horizontal-render-profile', 'render-profile'
    ) -BaseDirectories $bases
    $verticalProfile = Resolve-RoleArtifact -RoleRecords $roleRecords -RoleNames @(
        'vertical-render-profile'
    ) -BaseDirectories $bases
    if ($null -eq $horizontalScene) { $errors.Add('Horizontal final scene was not resolved.') }
    if ($null -eq $horizontalProfile) { $errors.Add('Horizontal final render profile was not resolved.') }

    $technicalReady = Get-FirstLeafValue -Json $technicalAuthorization -Names @('technicalReady')
    if (($technicalReady -isnot [bool]) -or -not $technicalReady) {
        $errors.Add('Technical authorization does not explicitly report typed technicalReady=true.')
    }

    $humanApprovalPath = Resolve-RoleArtifact -RoleRecords $roleRecords -RoleNames @(
        'human-visual-qa-approval'
    ) -BaseDirectories $bases
    $humanClosurePath = Resolve-RoleArtifact -RoleRecords $roleRecords -RoleNames @(
        'human-review-closure'
    ) -BaseDirectories $bases
    if ($null -eq $humanApprovalPath) { $errors.Add('Release does not bind a resolvable, hash-valid human-visual-qa-approval artifact.') }
    if ($null -eq $humanClosurePath) { $errors.Add('Release does not bind a resolvable, hash-valid human-review-closure artifact.') }

    $enabledVariants = @(Get-EnabledVariantIds -Json $technicalAuthorization)
    if ($enabledVariants.Count -eq 0) {
        $enabledVariants = @(Get-EnabledVariantIds -Json $releaseReport)
    }
    if ($enabledVariants -notcontains 'horizontal-16x9-1080p') {
        $errors.Add('Required horizontal-16x9-1080p variant is not enabled by the selected release.')
    }

    $aggregateP90 = Get-FirstLeafValue -Json $releaseReport -Names @('p90Seconds') -PathRegex 'aggregateForecast.*total.*p90Seconds'
    if ($null -eq $aggregateP90) {
        $errors.Add('Release report does not contain the exact enabled-matrix aggregate total P90 forecast.')
    }
    elseif ([double]$aggregateP90 -gt 86400.0) {
        $errors.Add("Aggregate P90 exceeds 24 hours: $aggregateP90 seconds.")
    }

    $sourceAudioRecord = @($roleRecords | Where-Object { $_.Role -eq 'source-audio' } | Select-Object -First 1)
    $sourceCueRecord = @($roleRecords | Where-Object { $_.Role -eq 'source-cue' } | Select-Object -First 1)
    if ($sourceAudioRecord.Count -eq 0) {
        $errors.Add('Release report does not bind source-audio identity.')
    }
    elseif ([string]::IsNullOrWhiteSpace([string]$sourceAudioRecord[0].Sha256) -or $null -eq $sourceAudioRecord[0].SizeBytes) {
        $errors.Add('Source-audio binding must include exact SHA-256 and byte length.')
    }
    if ($sourceCueRecord.Count -eq 0) {
        $errors.Add('Release report does not bind source-cue identity.')
    }
    elseif ([string]::IsNullOrWhiteSpace([string]$sourceCueRecord[0].Sha256) -or $null -eq $sourceCueRecord[0].SizeBytes) {
        $errors.Add('Source-cue binding must include exact SHA-256 and byte length.')
    }

    $packageSha = Get-Sha256 -Path $PackageFile.FullName
    $calibrationSha = Get-Sha256 -Path $calibrationPath
    $technicalSha = Get-Sha256 -Path $technicalAuthorizationPath
    $releaseReportSha = Get-Sha256 -Path $releaseReportPath

    $holdPath = Join-Path $RepoRoot 'production\andromeda-v2\release-hold.json'
    $held = $false
    if (Test-Path -LiteralPath $holdPath -PathType Leaf) {
        $holdRaw = (Get-Content -Raw -LiteralPath $holdPath -Encoding UTF8).ToLowerInvariant()
        foreach ($hash in @($packageSha, $calibrationSha, $technicalSha)) {
            if ($holdRaw.Contains($hash)) {
                $held = $true
            }
        }
        $releaseIdentity = Get-FirstLeafValue -Json $technicalAuthorization -Names @('releaseIdentitySha256') -PathRegex '(?i)(?<!supersedes)releaseIdentitySha256$'
        if ($null -ne $releaseIdentity -and $holdRaw.Contains(([string]$releaseIdentity).ToLowerInvariant())) {
            $held = $true
        }
    }
    if ($held) {
        $errors.Add('The tracked release hold binds this exact release. It cannot be selected for production.')
    }

    return [pscustomobject]@{
        PackageManifestPath       = $PackageFile.FullName
        PackageManifestSha256     = $packageSha
        CalibrationPath           = $calibrationPath
        CalibrationSha256         = $calibrationSha
        TechnicalAuthorizationPath = $technicalAuthorizationPath
        TechnicalAuthorizationSha256 = $technicalSha
        ReleaseReportPath         = $releaseReportPath
        ReleaseReportSha256       = $releaseReportSha
        BundleDirectory           = $bundleDirectory
        ProofRoot                 = $proofRoot
        ReleaseTimestamp          = Get-ReleaseTimestamp -ReportJson $releaseReport -PackageFile $PackageFile
        HorizontalScenePath       = $horizontalScene
        VerticalScenePath         = $verticalScene
        HorizontalProfilePath     = $horizontalProfile
        VerticalProfilePath       = $verticalProfile
        EnabledVariantIds         = $enabledVariants
        AggregateP90Seconds       = $(if ($null -ne $aggregateP90) { [double]$aggregateP90 } else { $null })
        SourceAudioBinding        = $(if ($sourceAudioRecord.Count -gt 0) { $sourceAudioRecord[0] } else { $null })
        SourceCueBinding          = $(if ($sourceCueRecord.Count -gt 0) { $sourceCueRecord[0] } else { $null })
        HumanVisualQaApprovalPath = $humanApprovalPath
        HumanReviewClosurePath    = $humanClosurePath
        RoleRecords               = $roleRecords
        PackageJson               = $package
        CalibrationJson           = $calibration
        TechnicalAuthorizationJson = $technicalAuthorization
        ReleaseReportJson         = $releaseReport
        Held                      = $held
        Complete                  = ($errors.Count -eq 0)
        Errors                    = @($errors)
    }
}

function Find-ReleaseCandidates {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string]$ExplicitPath
    )

    $packageFiles = @()
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        $resolved = $ExplicitPath
        if (Test-Path -LiteralPath $resolved -PathType Container) {
            $resolved = Join-Path $resolved 'package-manifest-v2.json'
        }
        $resolved = Resolve-ExistingFile -Path $resolved -Label 'Explicit package manifest'
        $packageFiles = @((Get-Item -LiteralPath $resolved))
    }
    else {
        $roots = @(
            (Join-Path $RepoRoot 'test-output'),
            (Join-Path $RepoRoot 'production'),
            (Join-Path $RepoRoot 'release')
        ) | Where-Object { Test-Path -LiteralPath $_ -PathType Container }

        foreach ($root in $roots) {
            $packageFiles += @(Get-ChildItem -LiteralPath $root -File -Recurse -Filter 'package-manifest-v2.json' -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.FullName -notmatch '[\\/](\.pytest[^\\/]*|node_modules|\.git)[\\/]'
                })
        }
    }

    $unique = @($packageFiles | Sort-Object FullName -Unique)
    if ($unique.Count -eq 0) {
        Throw-OperatorError "No package-manifest-v2.json was found below the configured release roots."
    }

    $candidates = @()
    foreach ($packageFile in $unique) {
        $candidates += New-ReleaseCandidate -PackageFile $packageFile -RepoRoot $RepoRoot
    }
    return @($candidates | Sort-Object ReleaseTimestamp -Descending)
}

function Select-ReleaseCandidate {
    param(
        [Parameter(Mandatory = $true)]$Candidates,
        [switch]$AllowOlder
    )

    $complete = @($Candidates | Where-Object { $_.Complete })
    if ($complete.Count -eq 0) {
        $details = foreach ($candidate in $Candidates) {
            "- $($candidate.PackageManifestPath): $([string]::Join('; ', @($candidate.Errors)))"
        }
        Throw-OperatorError ("No coherent production bundle was found.`n{0}" -f ([string]::Join("`n", $details)))
    }

    $newestPackage = $Candidates[0]
    if (-not $newestPackage.Complete -and -not $AllowOlder) {
        Throw-OperatorError (
            "The newest bundle is incomplete or blocked, and older fallback is disabled.`n" +
            "$($newestPackage.PackageManifestPath)`n" +
            ([string]::Join("`n", @($newestPackage.Errors)))
        )
    }

    return $complete[0]
}

function Get-ExpectedSourceBinding {
    param(
        [Parameter(Mandatory = $true)]$Candidate,
        [ValidateSet('Audio', 'Cue')][string]$Kind
    )
    if ($Kind -eq 'Audio') { return $Candidate.SourceAudioBinding }
    return $Candidate.SourceCueBinding
}

function Test-SourceIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Binding,
        [string]$Label
    )
    $resolved = Resolve-ExistingFile -Path $Path -Label $Label
    if ($null -ne $Binding.SizeBytes -and (Get-Item -LiteralPath $resolved).Length -ne [int64]$Binding.SizeBytes) {
        Throw-OperatorError "$Label byte length does not match the release binding: $resolved"
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$Binding.Sha256)) {
        $actual = Get-Sha256 -Path $resolved
        if ($actual -ne ([string]$Binding.Sha256).ToLowerInvariant()) {
            Throw-OperatorError "$Label SHA-256 does not match the release binding: $resolved"
        }
    }
    return $resolved
}

function Find-PrivateSourceByIdentity {
    param(
        [Parameter(Mandatory = $true)]$Binding,
        [ValidateSet('Audio', 'Cue')][string]$Kind,
        [Parameter(Mandatory = $true)][string[]]$SearchRoots,
        [string[]]$PriorityPaths = @()
    )

    $candidateFiles = New-Object 'System.Collections.Generic.List[System.IO.FileInfo]'
    foreach ($priorityPath in $PriorityPaths) {
        if (-not [string]::IsNullOrWhiteSpace($priorityPath) -and (Test-Path -LiteralPath $priorityPath -PathType Leaf)) {
            $candidateFiles.Add((Get-Item -LiteralPath $priorityPath))
        }
    }

    $allowedExtensions = if ($Kind -eq 'Audio') {
        @('.wav', '.flac', '.aiff', '.aif', '.mp3', '.m4a')
    }
    else {
        @('.json')
    }
    $extensionSet = @{}
    foreach ($extension in $allowedExtensions) { $extensionSet[$extension] = $true }

    foreach ($root in $SearchRoots | Select-Object -Unique) {
        if ([string]::IsNullOrWhiteSpace($root) -or -not (Test-Path -LiteralPath $root -PathType Container)) {
            continue
        }

        $rootFiles = New-Object 'System.Collections.Generic.List[System.IO.FileInfo]'
        foreach ($extension in $allowedExtensions) {
            foreach ($file in @(Get-ChildItem `
                -LiteralPath $root `
                -File `
                -Recurse `
                -Filter ("*{0}" -f $extension) `
                -ErrorAction SilentlyContinue)) {
                $rootFiles.Add($file)
            }
        }

        foreach ($file in @($rootFiles | Sort-Object FullName -Unique)) {
            if ($file.FullName -match '[\\/](node_modules|\.git|deep-models|render-packages)[\\/]') {
                continue
            }
            if (-not $extensionSet.ContainsKey($file.Extension.ToLowerInvariant())) {
                continue
            }
            if ($Kind -eq 'Cue' -and $file.Name -notmatch '(?i)visual[-_ ]?cues|cue') {
                continue
            }
            if ($null -ne $Binding.SizeBytes -and $file.Length -ne [int64]$Binding.SizeBytes) {
                continue
            }
            $candidateFiles.Add($file)
        }
    }

    foreach ($file in @($candidateFiles | Sort-Object -Property `
        @{ Expression = 'LastWriteTimeUtc'; Descending = $true }, `
        @{ Expression = 'FullName'; Descending = $false } -Unique)) {
        if ($null -ne $Binding.SizeBytes -and $file.Length -ne [int64]$Binding.SizeBytes) {
            continue
        }
        if ([string]::IsNullOrWhiteSpace([string]$Binding.Sha256)) {
            continue
        }
        if ((Get-Sha256 -Path $file.FullName) -eq ([string]$Binding.Sha256).ToLowerInvariant()) {
            return $file.FullName
        }
    }
    return $null
}

function Resolve-PrivateSource {
    param(
        [Parameter(Mandatory = $true)]$Candidate,
        [ValidateSet('Audio', 'Cue')][string]$Kind,
        [string]$ExplicitPath,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string[]]$ExtraSearchRoots
    )

    $binding = Get-ExpectedSourceBinding -Candidate $Candidate -Kind $Kind
    if ($null -eq $binding) {
        Throw-OperatorError "The selected release has no $Kind source binding."
    }

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        return Test-SourceIdentity -Path $ExplicitPath -Binding $binding -Label "Private source $Kind"
    }

    $priority = @()
    if ($Kind -eq 'Audio') {
        $priority += 'C:\Users\theon\OneDrive\Desktop\Gratis Project\DJ WaZaHaKa - Trip to Andromeda.wav'
    }
    else {
        $priority += (Join-Path $Candidate.ProofRoot 'visual-cues.json')
        $priority += (Join-Path $Candidate.ProofRoot 'analysis\visual-cues.json')
    }

    $roots = @(
        $Candidate.ProofRoot,
        (Join-Path $RepoRoot 'test-output'),
        (Join-Path $RepoRoot '.trackprompt-data')
    ) + @($ExtraSearchRoots)

    Write-Notice "Searching for the private $Kind source by exact SHA-256 and size..."
    $found = Find-PrivateSourceByIdentity -Binding $binding -Kind $Kind -SearchRoots $roots -PriorityPaths $priority
    if ($null -ne $found) {
        return $found
    }

    $entered = Read-Host "Enter the exact private source $Kind path (expected SHA-256 $($binding.Sha256))"
    return Test-SourceIdentity -Path $entered -Binding $binding -Label "Private source $Kind"
}

function Get-MatrixId {
    param([Parameter(Mandatory = $true)]$CalibrationJson)
    try {
        $direct = $CalibrationJson.identity.outputMatrix.matrixId
        if (-not [string]::IsNullOrWhiteSpace([string]$direct)) { return [string]$direct }
    }
    catch { }
    $fallback = Get-FirstLeafValue -Json $CalibrationJson -Names @('matrixId', 'outputMatrixId')
    if ($null -eq $fallback -or [string]::IsNullOrWhiteSpace([string]$fallback)) {
        Throw-OperatorError 'Could not resolve output matrix ID from the calibration.'
    }
    return [string]$fallback
}

function Find-ExactAuthorizationToken {
    param(
        [Parameter(Mandatory = $true)]$JsonDocuments,
        [Parameter(Mandatory = $true)][string]$SceneSha,
        [Parameter(Mandatory = $true)][string]$ProfileSha
    )
    $scenePrefix = $SceneSha.Substring(0, 12).ToUpperInvariant()
    $profilePrefix = $ProfileSha.Substring(0, 12).ToUpperInvariant()
    foreach ($document in $JsonDocuments) {
        foreach ($leaf in @(Get-JsonLeaves -Value $document)) {
            if (-not ($leaf.Value -is [string])) { continue }
            $value = [string]$leaf.Value
            if (-not $value.StartsWith('AUTHORIZE FULL RENDER:', [StringComparison]::Ordinal)) { continue }
            if ($value.Contains('<') -or $value.Contains('>')) { continue }
            if ($value.ToUpperInvariant().Contains("SCENE $scenePrefix") -and $value.ToUpperInvariant().Contains("PROFILE $profilePrefix")) {
                return $value
            }
        }
    }
    return $null
}

function Get-ProfileRootValue {
    param(
        [Parameter(Mandatory = $true)]$ProfileJson,
        [Parameter(Mandatory = $true)][string[]]$Names
    )
    foreach ($name in $Names) {
        $property = @($ProfileJson.PSObject.Properties | Where-Object { $_.Name -ieq $name } | Select-Object -First 1)
        if ($property.Count -gt 0 -and $null -ne $property[0].Value) {
            return $property[0].Value
        }
    }
    return Get-FirstLeafValue -Json $ProfileJson -Names $Names
}

function New-DerivedAuthorizationToken {
    param(
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [Parameter(Mandatory = $true)][string]$ProfilePath
    )
    $profile = Read-JsonFile -Path $ProfilePath -Label 'Render profile'
    $profileId = Get-ProfileRootValue -ProfileJson $profile -Names @('profileId')
    $project = Get-ProfileRootValue -ProfileJson $profile -Names @('project')
    $preset = Get-ProfileRootValue -ProfileJson $profile -Names @('preset')

    if ([string]::IsNullOrWhiteSpace([string]$profileId)) {
        Throw-OperatorError "Cannot derive authorization token because profileId is missing: $ProfilePath"
    }
    if ([string]::IsNullOrWhiteSpace([string]$project)) { $project = 'trip-to-andromeda' }
    if ([string]::IsNullOrWhiteSpace([string]$preset)) { $preset = 'space-journey' }

    $projectLabel = (([string]$project) -replace '[-_]+', ' ').ToUpperInvariant()
    $presetLabel = (([string]$preset) -replace '_', '-').ToUpperInvariant()
    $sceneSha = Get-Sha256 -Path $ScenePath
    $profileSha = Get-Sha256 -Path $ProfilePath

    return ('AUTHORIZE FULL RENDER: {0} | {1} | {2} | SCENE {3} | PROFILE {4}' -f `
        $projectLabel,
        $presetLabel,
        [string]$profileId,
        $sceneSha.Substring(0, 12).ToUpperInvariant(),
        $profileSha.Substring(0, 12).ToUpperInvariant())
}

function Resolve-AuthorizationToken {
    param(
        [string]$ExplicitToken,
        [Parameter(Mandatory = $true)]$Candidate,
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [Parameter(Mandatory = $true)][string]$ProfilePath
    )
    if (-not [string]::IsNullOrWhiteSpace($ExplicitToken)) {
        return $ExplicitToken
    }

    $sceneSha = Get-Sha256 -Path $ScenePath
    $profileSha = Get-Sha256 -Path $ProfilePath
    $fromEvidence = Find-ExactAuthorizationToken -JsonDocuments @(
        $Candidate.ReleaseReportJson,
        $Candidate.TechnicalAuthorizationJson,
        $Candidate.PackageJson,
        $Candidate.CalibrationJson
    ) -SceneSha $sceneSha -ProfileSha $profileSha

    if ($null -ne $fromEvidence) {
        return $fromEvidence
    }
    return New-DerivedAuthorizationToken -ScenePath $ScenePath -ProfilePath $ProfilePath
}

function Invoke-LauncherValidation {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    $launcher = Resolve-ExistingFile -Path (Join-Path $RepoRoot 'WZHK-Media-Launcher.cmd') -Label 'Mission Control launcher'
    Push-Location $RepoRoot
    try {
        & $launcher -ValidateOnly
        if ($LASTEXITCODE -ne 0) {
            Throw-OperatorError "Mission Control launcher validation failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

function Start-MissionControlDashboard {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    if ($SkipDashboardLaunch) { return }
    $launcher = Resolve-ExistingFile -Path (Join-Path $RepoRoot 'WZHK-Media-Launcher.cmd') -Label 'Mission Control launcher'
    Start-Process -FilePath $launcher -WorkingDirectory $RepoRoot | Out-Null
    Start-Sleep -Seconds 3
    Write-Notice 'Mission Control was launched/reopened. Keep the browser open to watch real completed frames, safe frames, stage progress, and P50/P90 ETA.'
}

function Invoke-PathHarnessIfAvailable {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)]$Candidate
    )
    $harness = Join-Path $RepoRoot 'tools\test-andromeda-production-paths.ps1'
    if (-not (Test-Path -LiteralPath $harness -PathType Leaf)) { return }
    if ($null -eq $Candidate.VerticalScenePath -or $null -eq $Candidate.VerticalProfilePath) { return }

    Write-Stage 'Validating exact scene/profile path forwarding'
    & $harness `
        -RenderProfilePath $Candidate.HorizontalProfilePath `
        -VerticalRenderProfilePath $Candidate.VerticalProfilePath `
        -HorizontalScenePath $Candidate.HorizontalScenePath `
        -VerticalScenePath $Candidate.VerticalScenePath
    $harnessSucceeded = $?
    if (-not $harnessSucceeded) {
        Throw-OperatorError 'Andromeda production path harness failed.'
    }
}

function Invoke-ProductionWrapper {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][ValidateSet('Inspect', 'Preflight', 'StartOrResume')][string]$Action,
        [Parameter(Mandatory = $true)]$Candidate,
        [string]$PrivateAudio,
        [string]$PrivateCue,
        [string]$OperatorAuthorizationPath,
        [string]$HorizontalToken,
        [string]$VerticalToken
    )

    $wrapper = Resolve-ExistingFile -Path (Join-Path $RepoRoot 'production\andromeda-v2\invoke-production.ps1') -Label 'Andromeda production wrapper'
    $parameters = @{
        Action            = $Action
        ScenePath         = $Candidate.HorizontalScenePath
        RenderProfilePath = $Candidate.HorizontalProfilePath
    }

    if ($EnableVertical) {
        $parameters.EnableVertical = $true
        $parameters.VerticalScenePath = $Candidate.VerticalScenePath
        $parameters.VerticalRenderProfilePath = $Candidate.VerticalProfilePath
    }

    if ($Action -eq 'StartOrResume') {
        $parameters.CalibrationPath = $Candidate.CalibrationPath
        $parameters.PackageManifestPath = $Candidate.PackageManifestPath
        $parameters.TechnicalAuthorizationPath = $Candidate.TechnicalAuthorizationPath
        $parameters.SourceAudioPath = $PrivateAudio
        $parameters.SourceCuePath = $PrivateCue
        $parameters.OperatorAuthorizationPath = $OperatorAuthorizationPath
        $parameters.AuthorizationToken = $HorizontalToken
        if ($EnableVertical) {
            $parameters.VerticalAuthorizationToken = $VerticalToken
        }
    }

    Push-Location $RepoRoot
    try {
        & $wrapper @parameters
        $wrapperSucceeded = $?
        if (-not $wrapperSucceeded) {
            Throw-OperatorError "Production action '$Action' failed."
        }
    }
    finally {
        Pop-Location
    }
}

function Ensure-OperatorAuthorization {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)]$Candidate
    )

    $matrixId = Get-MatrixId -CalibrationJson $Candidate.CalibrationJson
    $authorizationDirectory = Join-Path $RepoRoot 'production\andromeda-v2\.operator-authorizations'
    $expectedPath = Join-Path $authorizationDirectory ("{0}.operator-start-authorization.json" -f $matrixId)

    if (Test-Path -LiteralPath $expectedPath -PathType Leaf) {
        Write-Notice "Found existing operator-start artifact: $expectedPath"
        return (Resolve-Path -LiteralPath $expectedPath).Path
    }

    Write-Stage 'Creating the separate operator-start authorization'
    Write-WarningLine 'This invokes the repository helper and requires the exact human confirmation phrase. It will fail closed while a matching release hold is open.'
    $helper = Resolve-ExistingFile -Path (Join-Path $RepoRoot 'production\andromeda-v2\new-operator-authorization.ps1') -Label 'Operator-authorization helper'
    $parameters = @{
        CalibrationPath = $Candidate.CalibrationPath
        PackageManifestPath = $Candidate.PackageManifestPath
        TechnicalAuthorizationPath = $Candidate.TechnicalAuthorizationPath
    }
    if ($EnableVertical) { $parameters.EnableVertical = $true }

    Push-Location $RepoRoot
    try {
        & $helper @parameters
        $helperSucceeded = $?
        if (-not $helperSucceeded) {
            Throw-OperatorError 'Operator-authorization helper failed.'
        }
    }
    finally {
        Pop-Location
    }

    if (Test-Path -LiteralPath $expectedPath -PathType Leaf) {
        return (Resolve-Path -LiteralPath $expectedPath).Path
    }

    $newest = @()
    if (Test-Path -LiteralPath $authorizationDirectory -PathType Container) {
        $newest = @(Get-ChildItem -LiteralPath $authorizationDirectory -File -Filter '*.operator-start-authorization.json' |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1)
    }
    if ($newest.Count -eq 0) {
        Throw-OperatorError 'The helper returned successfully but no operator-start authorization artifact was found.'
    }
    return $newest[0].FullName
}

function Get-ProfileFrameContract {
    param([Parameter(Mandatory = $true)][string]$ProfilePath)
    $profile = Read-JsonFile -Path $ProfilePath -Label 'Render profile'

    $frameStart = $null
    $frameEnd = $null
    $framesSubdirectory = 'frames'
    try { $frameStart = [int]$profile.frameStart } catch { }
    try { $frameEnd = [int]$profile.frameEnd } catch { }
    if ($null -eq $frameStart) { try { $frameStart = [int]$profile.timeline.frameStart } catch { } }
    if ($null -eq $frameEnd) { try { $frameEnd = [int]$profile.timeline.frameEnd } catch { } }
    try {
        if (-not [string]::IsNullOrWhiteSpace([string]$profile.output.framesSubdirectory)) {
            $framesSubdirectory = [string]$profile.output.framesSubdirectory
        }
    }
    catch { }

    if ($null -eq $frameStart) { $frameStart = 1 }
    if ($null -eq $frameEnd) { $frameEnd = 13029 }

    return [pscustomobject]@{
        FrameStart = $frameStart
        FrameEnd = $frameEnd
        FrameCount = ($frameEnd - $frameStart + 1)
        FramesSubdirectory = $framesSubdirectory
    }
}

function Get-OutputRootFromReport {
    param(
        [Parameter(Mandatory = $true)]$Candidate,
        [ValidateSet('Horizontal', 'Vertical')][string]$Variant,
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    $name = if ($Variant -eq 'Horizontal') { 'horizontalOutputPattern' } else { 'verticalOutputPattern' }
    $pattern = Get-FirstLeafValue -Json $Candidate.ReleaseReportJson -Names @($name)
    if ($null -eq $pattern -or [string]::IsNullOrWhiteSpace([string]$pattern)) {
        return $null
    }
    if ([string]$pattern -match '[{}<>]') { return $null }

    $normalized = ([string]$pattern) -replace '/', [IO.Path]::DirectorySeparatorChar
    $fullPattern = if ([IO.Path]::IsPathRooted($normalized)) { $normalized } else { Join-Path $RepoRoot $normalized }
    $framesDirectory = Split-Path -Parent $fullPattern
    if ([string]::IsNullOrWhiteSpace($framesDirectory)) { return $null }
    return (Split-Path -Parent $framesDirectory)
}

function Find-MatchingManagedOutput {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$SceneSha,
        [Parameter(Mandatory = $true)][string]$ProfileSha,
        [DateTimeOffset]$NotBefore
    )

    $finalOutput = Join-Path $RepoRoot 'final-output'
    if (-not (Test-Path -LiteralPath $finalOutput -PathType Container)) { return $null }

    foreach ($directory in @(Get-ChildItem -LiteralPath $finalOutput -Directory | Sort-Object LastWriteTimeUtc -Descending)) {
        if ([DateTimeOffset]($directory.LastWriteTimeUtc) -lt $NotBefore.AddDays(-2)) { continue }
        $manifestDirectory = Join-Path $directory.FullName 'manifests'
        if (-not (Test-Path -LiteralPath $manifestDirectory -PathType Container)) { continue }
        foreach ($manifest in @(Get-ChildItem -LiteralPath $manifestDirectory -File -Filter '*.json' -ErrorAction SilentlyContinue)) {
            $raw = (Get-Content -Raw -LiteralPath $manifest.FullName -Encoding UTF8).ToLowerInvariant()
            if ($raw.Contains($SceneSha.ToLowerInvariant()) -and $raw.Contains($ProfileSha.ToLowerInvariant())) {
                return $directory.FullName
            }
        }
    }
    return $null
}

function Resolve-ManagedOutputRoot {
    param(
        [Parameter(Mandatory = $true)]$Candidate,
        [ValidateSet('Horizontal', 'Vertical')][string]$Variant,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [Parameter(Mandatory = $true)][string]$ProfilePath
    )

    $fromReport = Get-OutputRootFromReport -Candidate $Candidate -Variant $Variant -RepoRoot $RepoRoot
    if ($null -ne $fromReport -and (Test-Path -LiteralPath $fromReport -PathType Container)) {
        return (Resolve-Path -LiteralPath $fromReport).Path
    }

    return Find-MatchingManagedOutput -RepoRoot $RepoRoot -SceneSha (Get-Sha256 -Path $ScenePath) -ProfileSha (Get-Sha256 -Path $ProfilePath) -NotBefore $script:RunStartedAt
}

function Get-PublishedFrameCount {
    param(
        [Parameter(Mandatory = $true)][string]$OutputRoot,
        [Parameter(Mandatory = $true)]$FrameContract
    )
    $framesDirectory = Join-Path $OutputRoot $FrameContract.FramesSubdirectory
    if (-not (Test-Path -LiteralPath $framesDirectory -PathType Container)) { return 0 }
    return @(Get-ChildItem -LiteralPath $framesDirectory -File -Filter 'frame_*.png' -ErrorAction SilentlyContinue).Count
}

function Wait-ForRenderCompletion {
    param(
        [Parameter(Mandatory = $true)]$Candidate,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [ValidateSet('Horizontal', 'Vertical')][string]$Variant,
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][int]$TimeoutHours
    )

    $contract = Get-ProfileFrameContract -ProfilePath $ProfilePath
    $deadline = [DateTimeOffset]::Now.AddHours($TimeoutHours)
    $lastCount = -1
    $outputRoot = $null

    while ([DateTimeOffset]::Now -lt $deadline) {
        if ($null -eq $outputRoot) {
            $outputRoot = Resolve-ManagedOutputRoot -Candidate $Candidate -Variant $Variant -RepoRoot $RepoRoot -ScenePath $ScenePath -ProfilePath $ProfilePath
        }

        if ($null -ne $outputRoot) {
            $count = Get-PublishedFrameCount -OutputRoot $outputRoot -FrameContract $contract
            if ($count -ne $lastCount) {
                Write-Notice ("{0}: {1}/{2} validated published frames detected at {3}" -f $Variant, $count, $contract.FrameCount, $outputRoot)
                $lastCount = $count
            }
            if ($count -eq $contract.FrameCount) {
                return [pscustomobject]@{
                    Variant = $Variant
                    OutputRoot = $outputRoot
                    FrameContract = $contract
                }
            }
            if ($count -gt $contract.FrameCount) {
                Throw-OperatorError "$Variant output contains more canonical frames than the profile contract."
            }
        }

        Start-Sleep -Seconds $PollSeconds
    }

    Throw-OperatorError "$Variant render did not reach the complete validated frame count within $TimeoutHours hours. Use Mission Control to inspect the persisted job; do not delete frames or locks."
}

function Test-EncodingEnabled {
    param(
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [ValidateSet('Delivery', 'Master')][string]$Kind
    )
    $profile = Read-JsonFile -Path $ProfilePath -Label 'Render profile'
    try {
        $node = if ($Kind -eq 'Delivery') { $profile.encoding.delivery } else { $profile.encoding.master }
        if ($null -eq $node) { return $false }
        $enabledProperty = @($node.PSObject.Properties | Where-Object { $_.Name -ieq 'enabled' } | Select-Object -First 1)
        if ($enabledProperty.Count -gt 0) { return [bool]$enabledProperty[0].Value }
        return $true
    }
    catch {
        return $true
    }
}

function Get-EncodingState {
    param(
        [Parameter(Mandatory = $true)][string]$OutputRoot,
        [Parameter(Mandatory = $true)][string]$ProfilePath
    )

    $deliveryEnabled = Test-EncodingEnabled -ProfilePath $ProfilePath -Kind Delivery
    $masterEnabled = Test-EncodingEnabled -ProfilePath $ProfilePath -Kind Master

    $deliveryFiles = @()
    $masterFiles = @()
    $encodeManifests = @()
    $qaFiles = @()

    $deliveryDirectory = Join-Path $OutputRoot 'delivery'
    $masterDirectory = Join-Path $OutputRoot 'master'
    $manifestDirectory = Join-Path $OutputRoot 'manifests'
    $qaDirectory = Join-Path $OutputRoot 'qa'

    if (Test-Path -LiteralPath $deliveryDirectory -PathType Container) {
        $deliveryFiles = @(Get-ChildItem -LiteralPath $deliveryDirectory -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -ieq '.mp4' -and $_.Name -notmatch '(?i)partial|tmp' })
    }
    if (Test-Path -LiteralPath $masterDirectory -PathType Container) {
        $masterFiles = @(Get-ChildItem -LiteralPath $masterDirectory -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -ieq '.mov' -and $_.Name -notmatch '(?i)partial|tmp' })
    }
    if (Test-Path -LiteralPath $manifestDirectory -PathType Container) {
        $encodeManifests = @(Get-ChildItem -LiteralPath $manifestDirectory -File -Filter '*encode-manifest*.json' -ErrorAction SilentlyContinue)
    }
    if (Test-Path -LiteralPath $qaDirectory -PathType Container) {
        $qaFiles = @(Get-ChildItem -LiteralPath $qaDirectory -File -Filter '*.json' -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notmatch '(?i)pending|partial|tmp' })
    }

    $deliveryComplete = (-not $deliveryEnabled) -or ($deliveryFiles.Count -gt 0)
    $masterComplete = (-not $masterEnabled) -or ($masterFiles.Count -gt 0)
    $minimumManifests = ([int]$deliveryEnabled) + ([int]$masterEnabled)
    $manifestComplete = $encodeManifests.Count -ge $minimumManifests
    $qaComplete = $qaFiles.Count -gt 0

    return [pscustomobject]@{
        Complete = ($deliveryComplete -and $masterComplete -and $manifestComplete -and $qaComplete)
        DeliveryEnabled = $deliveryEnabled
        MasterEnabled = $masterEnabled
        DeliveryFiles = $deliveryFiles
        MasterFiles = $masterFiles
        EncodeManifests = $encodeManifests
        QaFiles = $qaFiles
    }
}

function Test-EncodingActivity {
    param(
        [Parameter(Mandatory = $true)][string]$OutputRoot,
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][DateTimeOffset]$NotBefore
    )

    $state = Get-EncodingState -OutputRoot $OutputRoot -ProfilePath $ProfilePath
    if ($state.Complete) {
        return $true
    }

    $cutoffUtc = $NotBefore.UtcDateTime
    $directories = @(
        (Join-Path $OutputRoot 'logs'),
        (Join-Path $OutputRoot 'delivery'),
        (Join-Path $OutputRoot 'master'),
        (Join-Path $OutputRoot 'manifests'),
        (Join-Path $OutputRoot 'qa')
    )

    foreach ($directory in $directories) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            continue
        }
        foreach ($file in @(Get-ChildItem -LiteralPath $directory -File -Recurse -ErrorAction SilentlyContinue)) {
            if ($file.LastWriteTimeUtc -lt $cutoffUtc) {
                continue
            }
            if ($file.Name -match '(?i)encode_|ffmpeg|partial') {
                return $true
            }
        }
    }
    return $false
}

function Wait-ForEncodingActivity {
    param(
        [Parameter(Mandatory = $true)]$RenderResult,
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][DateTimeOffset]$NotBefore,
        [Parameter(Mandatory = $true)][int]$TimeoutMinutes
    )

    $deadline = [DateTimeOffset]::Now.AddMinutes($TimeoutMinutes)
    while ([DateTimeOffset]::Now -lt $deadline) {
        if (Test-EncodingActivity `
            -OutputRoot $RenderResult.OutputRoot `
            -ProfilePath $ProfilePath `
            -NotBefore $NotBefore) {
            Write-Notice "$($RenderResult.Variant) encoding activity was detected by managed output state."
            return
        }
        Start-Sleep -Seconds $PollSeconds
    }

    Throw-OperatorError (
        "$($RenderResult.Variant) encoding did not start within $TimeoutMinutes minutes. " +
        'The complete frame sequence remains preserved. Open Mission Control > Encode, ' +
        'select this exact output, and start Encode delivery + master.'
    )
}

function Wait-ForEncodingCompletion {
    param(
        [Parameter(Mandatory = $true)]$RenderResult,
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][int]$TimeoutHours
    )

    $deadline = [DateTimeOffset]::Now.AddHours($TimeoutHours)
    $lastSummary = ''
    while ([DateTimeOffset]::Now -lt $deadline) {
        $state = Get-EncodingState -OutputRoot $RenderResult.OutputRoot -ProfilePath $ProfilePath
        $summary = "delivery=$($state.DeliveryFiles.Count), master=$($state.MasterFiles.Count), encodeManifests=$($state.EncodeManifests.Count), qa=$($state.QaFiles.Count)"
        if ($summary -ne $lastSummary) {
            Write-Notice "$($RenderResult.Variant) encode state: $summary"
            $lastSummary = $summary
        }
        if ($state.Complete) {
            return $state
        }
        Start-Sleep -Seconds $PollSeconds
    }
    Throw-OperatorError "$($RenderResult.Variant) encoding/QA did not complete within $TimeoutHours hours. Preserve the complete frame sequence and diagnose from Mission Control."
}

function Write-DiscoveryReport {
    param(
        [Parameter(Mandatory = $true)]$Candidate,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string]$ResolvedAudio,
        [string]$ResolvedCue
    )

    $stateRoot = Join-Path $RepoRoot '.trackprompt-data\andromeda-latest-production-runner'
    New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    if ($null -eq $script:RunStateDirectory) {
        $timestamp = [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')
        $script:RunStateDirectory = Join-Path $stateRoot $timestamp
        New-Item -ItemType Directory -Path $script:RunStateDirectory -Force | Out-Null
    }

    $report = [ordered]@{
        schemaVersion = '1.0.0'
        kind = 'andromeda-latest-production-discovery'
        generatedAt = [DateTimeOffset]::Now.ToString('o')
        mode = $Mode
        repositoryRoot = $RepoRoot
        enableVertical = [bool]$EnableVertical
        selected = [ordered]@{
            releaseTimestamp = $Candidate.ReleaseTimestamp.ToString('o')
            bundleDirectory = $Candidate.BundleDirectory
            packageManifest = $Candidate.PackageManifestPath
            packageManifestSha256 = $Candidate.PackageManifestSha256
            calibration = $Candidate.CalibrationPath
            calibrationSha256 = $Candidate.CalibrationSha256
            technicalAuthorization = $Candidate.TechnicalAuthorizationPath
            technicalAuthorizationSha256 = $Candidate.TechnicalAuthorizationSha256
            releaseReport = $Candidate.ReleaseReportPath
            releaseReportSha256 = $Candidate.ReleaseReportSha256
            horizontalScene = $Candidate.HorizontalScenePath
            horizontalProfile = $Candidate.HorizontalProfilePath
            verticalScene = $Candidate.VerticalScenePath
            verticalProfile = $Candidate.VerticalProfilePath
            enabledVariantIds = @($Candidate.EnabledVariantIds)
            aggregateP90Seconds = $Candidate.AggregateP90Seconds
            held = [bool]$Candidate.Held
        }
        privateSources = [ordered]@{
            audioResolved = ($null -ne $ResolvedAudio)
            audioSha256 = $(if ($null -ne $Candidate.SourceAudioBinding) { $Candidate.SourceAudioBinding.Sha256 } else { $null })
            cueResolved = ($null -ne $ResolvedCue)
            cueSha256 = $(if ($null -ne $Candidate.SourceCueBinding) { $Candidate.SourceCueBinding.Sha256 } else { $null })
            note = 'Private physical paths are deliberately not persisted by this helper.'
        }
    }

    $reportPath = Join-Path $script:RunStateDirectory 'discovery-report.json'
    $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Notice "Local discovery report: $reportPath"
    return $reportPath
}

function Show-CandidateSummary {
    param([Parameter(Mandatory = $true)]$Candidate)
    Write-Host ""
    Write-Host 'Selected coherent release:' -ForegroundColor Green
    Write-Host "  Timestamp:             $($Candidate.ReleaseTimestamp)"
    Write-Host "  Bundle:                $($Candidate.BundleDirectory)"
    Write-Host "  Package:               $($Candidate.PackageManifestPath)"
    Write-Host "  Calibration:           $($Candidate.CalibrationPath)"
    Write-Host "  Technical auth:        $($Candidate.TechnicalAuthorizationPath)"
    Write-Host "  Horizontal scene:      $($Candidate.HorizontalScenePath)"
    Write-Host "  Horizontal profile:    $($Candidate.HorizontalProfilePath)"
    Write-Host "  Enabled variants:      $([string]::Join(', ', @($Candidate.EnabledVariantIds)))"
    if ($null -ne $Candidate.AggregateP90Seconds) {
        Write-Host ("  Aggregate P90:         {0:N1} hours" -f ($Candidate.AggregateP90Seconds / 3600.0))
    }
    if ($EnableVertical) {
        Write-Host "  Vertical scene:        $($Candidate.VerticalScenePath)"
        Write-Host "  Vertical profile:      $($Candidate.VerticalProfilePath)"
    }
}

try {
    Write-Stage 'Resolving repository and production tools'
    $repo = Resolve-ExistingDirectory -Path $RepositoryRoot -Label 'TrackPrompt Studio repository'
    $requiredTools = @(
        (Join-Path $repo 'production\andromeda-v2\invoke-production.ps1'),
        (Join-Path $repo 'production\andromeda-v2\new-operator-authorization.ps1'),
        (Join-Path $repo 'WZHK-Media-Launcher.cmd')
    )
    foreach ($tool in $requiredTools) {
        Resolve-ExistingFile -Path $tool -Label 'Required production tool' | Out-Null
    }

    Write-Stage 'Discovering the newest coherent release bundle'
    $candidates = Find-ReleaseCandidates -RepoRoot $repo -ExplicitPath $ReleaseBundlePath
    $candidate = Select-ReleaseCandidate -Candidates $candidates -AllowOlder:$AllowOlderCompatibleRelease
    $script:SelectedCandidate = $candidate

    if ($EnableVertical -and $candidate.EnabledVariantIds -notcontains 'vertical-9x16-1080p') {
        Throw-OperatorError 'Vertical was requested, but the selected release does not enable the separately authored vertical-9x16-1080p variant. A horizontal-only bundle cannot be promoted to dual-output at runtime.'
    }
    if ($EnableVertical -and ($null -eq $candidate.VerticalScenePath -or $null -eq $candidate.VerticalProfilePath)) {
        Throw-OperatorError 'Vertical was requested, but the exact vertical scene/profile could not be resolved.'
    }

    Show-CandidateSummary -Candidate $candidate
    $discoveryReport = Write-DiscoveryReport -Candidate $candidate -RepoRoot $repo

    if ($Mode -eq 'Discover') {
        Write-Host ""
        Write-Host 'DISCOVERY COMPLETE — no preflight, render, or encode was started.' -ForegroundColor Green
        exit 0
    }

    Write-Stage 'Validating and opening Mission Control'
    Invoke-LauncherValidation -RepoRoot $repo
    Start-MissionControlDashboard -RepoRoot $repo
    Invoke-PathHarnessIfAvailable -RepoRoot $repo -Candidate $candidate

    Write-Stage 'Running canonical read-only inspection'
    Invoke-ProductionWrapper -RepoRoot $repo -Action Inspect -Candidate $candidate

    Write-Stage 'Running canonical read-only preflight'
    Invoke-ProductionWrapper -RepoRoot $repo -Action Preflight -Candidate $candidate

    if ($Mode -eq 'Preflight') {
        Write-Host ""
        Write-Host 'PREFLIGHT COMPLETE — no production render or encode was started.' -ForegroundColor Green
        exit 0
    }

    Write-Stage 'Resolving exact private source identities'
    $audio = Resolve-PrivateSource -Candidate $candidate -Kind Audio -ExplicitPath $SourceAudioPath -RepoRoot $repo -ExtraSearchRoots $SourceSearchRoot
    $cue = Resolve-PrivateSource -Candidate $candidate -Kind Cue -ExplicitPath $SourceCuePath -RepoRoot $repo -ExtraSearchRoots $SourceSearchRoot
    Write-DiscoveryReport -Candidate $candidate -RepoRoot $repo -ResolvedAudio $audio -ResolvedCue $cue | Out-Null

    $operatorAuthorization = Ensure-OperatorAuthorization -RepoRoot $repo -Candidate $candidate

    $horizontalToken = Resolve-AuthorizationToken -ExplicitToken $AuthorizationToken -Candidate $candidate -ScenePath $candidate.HorizontalScenePath -ProfilePath $candidate.HorizontalProfilePath
    $verticalToken = $null
    if ($EnableVertical) {
        $verticalToken = Resolve-AuthorizationToken -ExplicitToken $VerticalAuthorizationToken -Candidate $candidate -ScenePath $candidate.VerticalScenePath -ProfilePath $candidate.VerticalProfilePath
    }

    $matrixId = Get-MatrixId -CalibrationJson $candidate.CalibrationJson
    $packagePrefix = $candidate.PackageManifestSha256.Substring(0, 12).ToUpperInvariant()
    $confirmation = "START ANDROMEDA V2 $matrixId PACKAGE $packagePrefix"

    Write-Stage 'Final operator start confirmation'
    Write-WarningLine 'This is the production boundary. The repository wrapper will revalidate every identity and fail closed on any mismatch.'
    Write-Host "  Matrix:       $matrixId"
    Write-Host "  Package SHA:  $($candidate.PackageManifestSha256)"
    Write-Host "  Variants:     $([string]::Join(', ', @($candidate.EnabledVariantIds)))"
    Write-Host "  Type exactly: $confirmation" -ForegroundColor Yellow
    $typed = Read-Host 'Confirmation'
    if ($typed -cne $confirmation) {
        Throw-OperatorError 'Production confirmation did not match. Nothing was started.'
    }

    Write-Stage 'Starting or resuming the exact authorized render'
    Start-MissionControlDashboard -RepoRoot $repo
    Invoke-ProductionWrapper `
        -RepoRoot $repo `
        -Action StartOrResume `
        -Candidate $candidate `
        -PrivateAudio $audio `
        -PrivateCue $cue `
        -OperatorAuthorizationPath $operatorAuthorization `
        -HorizontalToken $horizontalToken `
        -VerticalToken $verticalToken

    Write-Stage 'Confirming complete validated frame sequences'
    $horizontalResult = Wait-ForRenderCompletion `
        -Candidate $candidate `
        -RepoRoot $repo `
        -Variant Horizontal `
        -ScenePath $candidate.HorizontalScenePath `
        -ProfilePath $candidate.HorizontalProfilePath `
        -TimeoutHours $RenderWaitHours

    $renderResults = @($horizontalResult)
    if ($EnableVertical) {
        $renderResults += Wait-ForRenderCompletion `
            -Candidate $candidate `
            -RepoRoot $repo `
            -Variant Vertical `
            -ScenePath $candidate.VerticalScenePath `
            -ProfilePath $candidate.VerticalProfilePath `
            -TimeoutHours $RenderWaitHours
    }

    if ($Mode -eq 'Start') {
        Write-Host ""
        Write-Host 'RENDER COMPLETE — encoding was not requested by this mode.' -ForegroundColor Green
        foreach ($result in $renderResults) { Write-Host "  $($result.Variant): $($result.OutputRoot)" }
        exit 0
    }

    Write-Stage 'Handing verified sequences to Mission Control encoding'
    Start-MissionControlDashboard -RepoRoot $repo

    $alreadyComplete = $true
    foreach ($result in $renderResults) {
        $profilePath = if ($result.Variant -eq 'Horizontal') { $candidate.HorizontalProfilePath } else { $candidate.VerticalProfilePath }
        if (-not (Get-EncodingState -OutputRoot $result.OutputRoot -ProfilePath $profilePath).Complete) {
            $alreadyComplete = $false
        }
    }

    if (-not $alreadyComplete) {
        Write-Host ""
        Write-Host 'In Mission Control:' -ForegroundColor Yellow
        Write-Host '  1. Open Encode.'
        Write-Host '  2. Select the completed output variant.'
        Write-Host '  3. Choose Encode delivery + master and complete its confirmations.'
        if ($EnableVertical) {
            Write-Host '  4. Repeat for the separately enabled vertical variant.'
        }
        Write-Host 'The script will not invoke an undocumented encode endpoint or bypass the explicit UI confirmation.'
        Write-Host 'No additional terminal response is needed; this window now watches for managed encode activity.'

        $encodeHandoffStartedAt = [DateTimeOffset]::Now.AddMinutes(-2)
        foreach ($result in $renderResults) {
            $profilePath = if ($result.Variant -eq 'Horizontal') { $candidate.HorizontalProfilePath } else { $candidate.VerticalProfilePath }
            $state = Get-EncodingState -OutputRoot $result.OutputRoot -ProfilePath $profilePath
            if (-not $state.Complete) {
                Wait-ForEncodingActivity `
                    -RenderResult $result `
                    -ProfilePath $profilePath `
                    -NotBefore $encodeHandoffStartedAt `
                    -TimeoutMinutes $EncodeStartWaitMinutes
            }
        }
    }

    $finalStates = @()
    foreach ($result in $renderResults) {
        $profilePath = if ($result.Variant -eq 'Horizontal') { $candidate.HorizontalProfilePath } else { $candidate.VerticalProfilePath }
        $finalStates += Wait-ForEncodingCompletion -RenderResult $result -ProfilePath $profilePath -TimeoutHours $EncodeWaitHours
    }

    Write-Host ""
    Write-Host 'RENDER, ENCODING, AND STRUCTURAL QA COMPLETE' -ForegroundColor Green
    for ($index = 0; $index -lt $renderResults.Count; $index++) {
        $result = $renderResults[$index]
        $state = $finalStates[$index]
        Write-Host ""
        Write-Host "  $($result.Variant): $($result.OutputRoot)"
        foreach ($file in @($state.DeliveryFiles + $state.MasterFiles)) {
            Write-Host "    Media: $($file.FullName)"
        }
        foreach ($qa in $state.QaFiles) {
            Write-Host "    QA:    $($qa.FullName)"
        }
    }
    Write-Host ""
    Write-Host 'Retain the lossless frame sequence until human full-timeline visual and A/V review is complete.' -ForegroundColor Yellow
    exit 0
}
catch {
    Write-Host ""
    Write-Host 'ANDROMEDA PRODUCTION HELPER STOPPED SAFELY' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($null -ne $script:RunStateDirectory) {
        Write-Host "Local run state: $script:RunStateDirectory" -ForegroundColor DarkGray
    }
    Write-Host 'No guard was bypassed. Preserve any valid published frames and inspect Mission Control for persisted state.' -ForegroundColor Yellow
    exit 1
}
