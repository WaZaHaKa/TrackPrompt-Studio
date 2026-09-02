[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [string]$PrepDirectory = "",
    [switch]$NoAutoWatcher,
    [switch]$ValidateOnly,
    [switch]$ListProfiles,
    [switch]$ValidateProfile,
    [switch]$RenderProfile,
    [string]$ProfilePath = ""
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

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = $PSScriptRoot
}
$RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)
$moduleRoot = Join-Path $RepositoryRoot "tools\wzhk-launcher"
$modulePaths = @(
    (Join-Path $moduleRoot "WZHK.UI.psm1"),
    (Join-Path $moduleRoot "WZHK.Discovery.psm1"),
    (Join-Path $moduleRoot "WZHK.Profiles.psm1"),
    (Join-Path $moduleRoot "WZHK.ProfileBuilder.psm1"),
    (Join-Path $moduleRoot "WZHK.Execution.psm1"),
    (Join-Path $moduleRoot "WZHK.Calibration.psm1"),
    (Join-Path $moduleRoot "WZHK.Performance.psm1"),
    (Join-Path $moduleRoot "WZHK.Outsource.psm1"),
    (Join-Path $moduleRoot "WZHK.Cloud.psm1"),
    (Join-Path $moduleRoot "WZHK.Brev.psm1")
)
$renderScript = Join-Path $RepositoryRoot "render-trackprompt-final.ps1"
$encoderScript = Join-Path $RepositoryRoot "encode-trackprompt-final.ps1"
$watcherScript = Join-Path $RepositoryRoot "tools\watch-trackprompt-final-render.ps1"
$validationScript = Join-Path $RepositoryRoot "tools\test-wzhk-mission-control.ps1"
$cmdLauncher = Join-Path $RepositoryRoot "WZHK-Media-Launcher.cmd"
$testOutputRoot = Join-Path $RepositoryRoot "test-output"
$finalOutputRoot = Join-Path $RepositoryRoot "final-output"
$profileRoot = Join-Path $RepositoryRoot "render-profiles"

$moduleImportFailures = New-Object System.Collections.Generic.List[string]
foreach ($modulePath in $modulePaths) {
    if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
        $moduleImportFailures.Add("Required module is missing: $modulePath")
        continue
    }

    try {
        Import-Module $modulePath -Force -DisableNameChecking -ErrorAction Stop
    }
    catch {
        $moduleImportFailures.Add("Could not import $modulePath : $($_.Exception.Message)")
    }
}

function Invoke-WzhkValidation {
    $failures = New-Object System.Collections.Generic.List[string]
    foreach ($failure in $moduleImportFailures) {
        $failures.Add($failure)
    }

    $requiredFiles = @(
        [pscustomobject]@{ Label = "CMD launcher"; Path = $cmdLauncher },
        [pscustomobject]@{ Label = "Mission Control"; Path = $PSCommandPath },
        [pscustomobject]@{ Label = "Production renderer"; Path = $renderScript },
        [pscustomobject]@{ Label = "Final encoder"; Path = $encoderScript },
        [pscustomobject]@{ Label = "Render watcher"; Path = $watcherScript },
        [pscustomobject]@{ Label = "Validation test"; Path = $validationScript },
        [pscustomobject]@{ Label = "UI module"; Path = $modulePaths[0] },
        [pscustomobject]@{ Label = "Discovery module"; Path = $modulePaths[1] },
        [pscustomobject]@{ Label = "Profiles module"; Path = $modulePaths[2] },
        [pscustomobject]@{ Label = "Profile builder module"; Path = $modulePaths[3] },
        [pscustomobject]@{ Label = "Execution module"; Path = $modulePaths[4] },
        [pscustomobject]@{ Label = "Calibration module"; Path = $modulePaths[5] },
        [pscustomobject]@{ Label = "Performance module"; Path = $modulePaths[6] },
        [pscustomobject]@{ Label = "Outsource module"; Path = $modulePaths[7] },
        [pscustomobject]@{ Label = "Cloud module"; Path = $modulePaths[8] },
        [pscustomobject]@{ Label = "Brev module"; Path = $modulePaths[9] },
        [pscustomobject]@{ Label = "Calibration entrypoint"; Path = (Join-Path $RepositoryRoot "tools\calibrate-trackprompt-render.ps1") },
        [pscustomobject]@{ Label = "Remote exporter"; Path = (Join-Path $RepositoryRoot "tools\export-trackprompt-render-package.ps1") },
        [pscustomobject]@{ Label = "Remote worker"; Path = (Join-Path $RepositoryRoot "render_trackprompt_worker.py") },
        [pscustomobject]@{ Label = "Cloud CLI"; Path = (Join-Path $RepositoryRoot "cloud_render\cli.py") }
    )

    foreach ($requiredFile in $requiredFiles) {
        if (-not (Test-Path -LiteralPath $requiredFile.Path -PathType Leaf)) {
            $failures.Add("$($requiredFile.Label) is missing: $($requiredFile.Path)")
        }
    }

    if (-not (Test-Path -LiteralPath $testOutputRoot -PathType Container)) {
        $failures.Add("Preparation-package root is missing: $testOutputRoot")
    }

    $parseFiles = @(
        $PSCommandPath,
        $modulePaths[0],
        $modulePaths[1],
        $modulePaths[2],
        $modulePaths[3],
        $modulePaths[4],
        $modulePaths[5],
        $modulePaths[6],
        $modulePaths[7],
        $modulePaths[8],
        $modulePaths[9],
        $watcherScript,
        $renderScript,
        $encoderScript,
        $validationScript,
        (Join-Path $RepositoryRoot "tools\calibrate-trackprompt-render.ps1"),
        (Join-Path $RepositoryRoot "tools\export-trackprompt-render-package.ps1"),
        (Join-Path $RepositoryRoot "tools\validate-trackprompt-render-package.ps1"),
        (Join-Path $RepositoryRoot "tools\import-trackprompt-remote-frames.ps1"),
        (Join-Path $RepositoryRoot "render-trackprompt-worker.ps1")
    )
    $parsedFileCount = 0
    foreach ($parseFile in $parseFiles) {
        if (-not (Test-Path -LiteralPath $parseFile -PathType Leaf)) {
            continue
        }

        $tokens = $null
        $parseErrors = $null
        try {
            [System.Management.Automation.Language.Parser]::ParseFile(
                $parseFile,
                [ref]$tokens,
                [ref]$parseErrors
            ) | Out-Null
            $parsedFileCount += 1

            foreach ($parseError in @($parseErrors)) {
                $failures.Add([string]::Format(
                    "Parser error in {0} at {1}:{2}: {3}",
                    $parseFile,
                    $parseError.Extent.StartLineNumber,
                    $parseError.Extent.StartColumnNumber,
                    $parseError.Message
                ))
            }
        }
        catch {
            $failures.Add("Could not parse $parseFile : $($_.Exception.Message)")
        }
    }

    $requiredFunctions = @(
        "Initialize-WzhkConsole",
        "Write-WzhkLogo",
        "Write-WzhkFrameTop",
        "Write-WzhkFrameDivider",
        "Write-WzhkFrameBottom",
        "Write-WzhkFrameLine",
        "Write-WzhkScreenHeader",
        "Get-WzhkMenuPage",
        "Get-WzhkMenuDigitIndex",
        "Show-WzhkKeypadMenu",
        "Show-WzhkMissionControlMenu",
        "Read-WzhkTextInput",
        "Read-WzhkIntegerInput",
        "Confirm-WzhkTwoStage",
        "Read-WzhkYesNo",
        "Show-WzhkMessage",
        "Show-WzhkDoneAnimation",
        "Get-WzhkHashPrefix",
        "Get-WzhkProfileInfo",
        "Get-WzhkSceneInfo",
        "Get-WzhkPrepCandidates",
        "Find-WzhkApprovedScene",
        "Get-WzhkProfileCandidates",
        "Find-WzhkAuthorizationToken",
        "Get-WzhkOutputStats",
        "Get-WzhkOutputCandidates",
        "New-WzhkOutputPath",
        "Get-WzhkRenderProfileTemplates",
        "New-WzhkRenderProfile",
        "Test-WzhkRenderProfile",
        "Save-WzhkRenderProfile",
        "Import-WzhkRenderProfile",
        "Get-WzhkSavedRenderProfiles",
        "Invoke-WzhkProfileBuilder",
        "Start-WzhkRenderWatcher",
        "Invoke-WzhkRenderMode",
        "Open-WzhkOutput",
        "Get-WzhkAdaptiveChunkPlan",
        "Get-WzhkMachineFingerprint",
        "Get-WzhkRenderSafetyAudit",
        "Select-WzhkRecommendedCalibrationProfile",
        "Get-WzhkNvidiaTelemetry",
        "Test-WzhkThermalSafety",
        "New-WzhkRemoteChunkDistribution",
        "Get-WzhkOutsourceEstimate",
        "Resolve-WzhkCloudPython",
        "Get-WzhkCloudCliReadiness",
        "Invoke-WzhkCloudCli",
        "Get-WzhkCloudDashboardLines",
        "Find-WzhkBrevExecutable",
        "Get-WzhkBrevCandidateGpuNames",
        "New-WzhkBrevBenchmarkAuthorizationToken",
        "Test-WzhkBrevBenchmarkPlanLock",
        "Test-WzhkBrevBenchmarkAuthorization",
        "Get-WzhkBrevReadiness",
        "Request-WzhkStopAfterCurrentChunk",
        "Cancel-WzhkStopAfterCurrentChunk"
    )
    $exportedFunctionCount = 0
    foreach ($requiredFunction in $requiredFunctions) {
        if ($null -eq (Get-Command -Name $requiredFunction -CommandType Function -ErrorAction SilentlyContinue)) {
            $failures.Add("Required exported function is unavailable: $requiredFunction")
        }
        else {
            $exportedFunctionCount += 1
        }
    }

    $prepCount = 0
    $profileCount = 0
    $approvedSceneCount = 0
    if (Test-Path -LiteralPath $testOutputRoot -PathType Container) {
        try {
            $prepCandidates = @(Get-WzhkPrepCandidates -TestOutputRoot $testOutputRoot)
            $prepCount = $prepCandidates.Count
            foreach ($prepCandidate in $prepCandidates) {
                $discoveredScene = Find-WzhkApprovedScene -PrepPath $prepCandidate.FullName
                if (
                    $null -ne $discoveredScene -and
                    (Split-Path -Leaf (Split-Path -Parent $discoveredScene)) -ieq "approved-candidate"
                ) { $approvedSceneCount += 1 }
                $profileCount += @(Get-WzhkProfileCandidates -PrepPath $prepCandidate.FullName).Count
            }
        }
        catch {
            $failures.Add("Read-only preparation-package discovery failed: $($_.Exception.Message)")
        }
    }

    $outputCount = 0
    if (Test-Path -LiteralPath $finalOutputRoot -PathType Container) {
        try {
            $outputCount = @(Get-WzhkOutputCandidates -FinalOutputRoot $finalOutputRoot).Count
        }
        catch {
            $failures.Add("Read-only output discovery failed: $($_.Exception.Message)")
        }
    }

    $savedProfileCount = 0
    if (Test-Path -LiteralPath $profileRoot -PathType Container) {
        try {
            $savedProfileCount = @(Get-WzhkSavedRenderProfiles -Directory $profileRoot -Recurse).Count
        }
        catch {
            $failures.Add("Read-only saved-profile discovery failed: $($_.Exception.Message)")
        }
    }

    Write-Host "WZHK Mission Control validation"
    Write-Host ("  Windows PowerShell: " + $PSVersionTable.PSVersion)
    Write-Host ("  Parsed files: " + $parsedFileCount)
    Write-Host ("  Imported functions: " + $exportedFunctionCount + "/" + $requiredFunctions.Count)
    Write-Host ("  Preparation packages: " + $prepCount)
    Write-Host ("  Approved scenes: " + $approvedSceneCount)
    Write-Host ("  Render profiles: " + $profileCount)
    Write-Host ("  Saved reusable profiles: " + $savedProfileCount)
    Write-Host ("  Existing outputs: " + $outputCount)

    if ($failures.Count -gt 0) {
        Write-Host ("  Status: FAILED (" + $failures.Count + ")") -ForegroundColor Red
        foreach ($failure in $failures) {
            Write-Host ("  - " + $failure) -ForegroundColor Red
        }
        Write-Host "WZHK Mission Control validation failed." -ForegroundColor Red
        return $false
    }

    Write-Host "  Status: OK" -ForegroundColor Green
    Write-Host "WZHK Mission Control validation passed." -ForegroundColor Green
    return $true
}

if ($ValidateOnly) {
    if (Invoke-WzhkValidation) {
        exit 0
    }
    exit 1
}

if ($moduleImportFailures.Count -gt 0) {
    foreach ($failure in $moduleImportFailures) {
        Write-Host $failure -ForegroundColor Red
    }
    exit 1
}

function Resolve-WzhkValidationPython {
    foreach ($candidate in @(
        (Join-Path $RepositoryRoot "backend\.venv\Scripts\python.exe"),
        (Join-Path $RepositoryRoot ".venv\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }
    return ""
}

$nonInteractiveModeCount = 0
foreach ($modeSwitch in @($ListProfiles, $ValidateProfile, $RenderProfile)) {
    if ([bool]$modeSwitch) { $nonInteractiveModeCount += 1 }
}
if ($nonInteractiveModeCount -gt 1) {
    [Console]::Error.WriteLine("-ListProfiles, -ValidateProfile, and -RenderProfile are mutually exclusive.")
    exit 2
}

if (($ValidateProfile -or $RenderProfile) -and [string]::IsNullOrWhiteSpace($ProfilePath)) {
    [Console]::Error.WriteLine("-ProfilePath is required with -ValidateProfile or -RenderProfile.")
    exit 2
}

if ($ListProfiles) {
    $profiles = @(Get-WzhkSavedRenderProfiles -Directory $profileRoot -Recurse)
    if ($profiles.Count -eq 0) {
        Write-Host "No saved render profiles were found."
        exit 0
    }
    foreach ($profile in $profiles) {
        Write-Host ([string]::Format(
            "{0} | {1}x{2} | {3} fps | {4} | {5}",
            $profile.Name,
            $profile.Width,
            $profile.Height,
            $profile.Fps,
            $(if ($profile.Valid) { "VALID" } else { "INVALID" }),
            $profile.Path
        ))
    }
    exit 0
}

if ($ValidateProfile) {
    try {
        $resolvedValidationProfile = [IO.Path]::GetFullPath($ProfilePath)
        $profileValidation = Test-WzhkRenderProfile -Path $resolvedValidationProfile -VerifyFiles
        $rendererPayload = $null
        $rendererErrors = New-Object System.Collections.Generic.List[string]
        $toolingPath = Join-Path $RepositoryRoot "tools\final_render_tooling.py"
        $validationPython = Resolve-WzhkValidationPython
        if ([string]::IsNullOrWhiteSpace($validationPython) -or -not (Test-Path -LiteralPath $toolingPath -PathType Leaf)) {
            $rendererErrors.Add("Authoritative renderer validation runtime/tooling is unavailable.")
        }
        else {
            $rendererArguments = @($toolingPath, "validate-profile", "--profile", $resolvedValidationProfile)
            if ($null -ne $profileValidation.Profile) {
                $rendererScene = ""
                if (
                    $null -ne $profileValidation.Profile.PSObject.Properties["approvedScene"] -and
                    $null -ne $profileValidation.Profile.approvedScene.PSObject.Properties["path"]
                ) {
                    $rendererScene = [string]$profileValidation.Profile.approvedScene.path
                }
                elseif ($null -ne $profileValidation.Profile.PSObject.Properties["approvedScenePath"]) {
                    $rendererScene = [string]$profileValidation.Profile.approvedScenePath
                }
                if (-not [string]::IsNullOrWhiteSpace($rendererScene) -and (Test-Path -LiteralPath $rendererScene -PathType Leaf)) {
                    $rendererArguments += @("--scene", $rendererScene)
                }
            }
            $previousPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = "Continue"
                $rendererOutput = @(& $validationPython @rendererArguments 2>&1)
                $rendererExitCode = $LASTEXITCODE
            }
            finally { $ErrorActionPreference = $previousPreference }
            $rendererText = (($rendererOutput | ForEach-Object { [string]$_ }) -join "`n").Trim()
            try { $rendererPayload = $rendererText | ConvertFrom-Json -ErrorAction Stop }
            catch { $rendererErrors.Add("Authoritative renderer validator returned unreadable output.") }
            if ($rendererExitCode -ne 0) {
                if ($null -ne $rendererPayload -and $null -ne $rendererPayload.PSObject.Properties["error"]) {
                    $rendererErrors.Add([string]$rendererPayload.error.code + ": " + [string]$rendererPayload.error.message)
                }
                elseif (-not [string]::IsNullOrWhiteSpace($rendererText)) { $rendererErrors.Add($rendererText) }
                else { $rendererErrors.Add("Authoritative renderer validator exited with code $rendererExitCode.") }
            }
        }
        $combinedErrors = @($profileValidation.Errors) + @($rendererErrors)
        $validAcrossLayers = ($profileValidation.Valid -and $rendererErrors.Count -eq 0 -and $null -ne $rendererPayload -and [bool]$rendererPayload.ok)
        $payload = [ordered]@{
            ok = [bool]$validAcrossLayers
            profilePath = $resolvedValidationProfile
            contentSha256 = $profileValidation.ContentSha256
            errors = @($combinedErrors)
            warnings = @($profileValidation.Warnings)
            rendererValidation = $rendererPayload
        }
        Write-Output ($payload | ConvertTo-Json -Depth 10)
        if ($validAcrossLayers) { exit 0 }
        exit 1
    }
    catch {
        Write-Error $_.Exception.Message
        exit 1
    }
}

$script:CliRenderProfilePath = if ($RenderProfile) { [IO.Path]::GetFullPath($ProfilePath) } else { "" }

Initialize-WzhkConsole

if (-not (Test-Path -LiteralPath $renderScript -PathType Leaf)) {
    Show-WzhkMessage -Title "STARTUP ERROR" -Lines @(
        "The production renderer was not found.",
        $renderScript
    ) -Color Red
    exit 1
}

if (-not (Test-Path -LiteralPath $testOutputRoot -PathType Container)) {
    Show-WzhkMessage -Title "STARTUP ERROR" -Lines @(
        "The TrackPrompt test-output directory was not found.",
        $testOutputRoot
    ) -Color Red
    exit 1
}

function Get-DisplayValue {
    param([AllowNull()][object]$Value, [string]$Fallback = "unknown")

    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return $Fallback
    }
    return [string]$Value
}

function Select-PrepPackage {
    param([string]$Current = "")

    $candidates = @(Get-WzhkPrepCandidates -TestOutputRoot $testOutputRoot)
    if ($candidates.Count -eq 0) {
        Show-WzhkMessage -Title "NO PREPARATION PACKAGE" -Lines @(
            "No final-render-prep-* directory was found under:",
            $testOutputRoot,
            "Run the final-render preparation workflow before production rendering."
        ) -Color Yellow
        return $null
    }

    $items = New-Object System.Collections.Generic.List[object]
    foreach ($candidate in @($candidates | Select-Object -First 9)) {
        $scene = Find-WzhkApprovedScene -PrepPath $candidate.FullName
        $sceneLabel = if ($null -eq $scene) { "No approved .blend found" } else { Split-Path -Leaf $scene }
        $items.Add([pscustomobject]@{
            Label = $candidate.Name
            Description = [string]::Format(
                "{0}  •  {1}",
                $candidate.LastWriteTime.ToString("yyyy-MM-dd HH:mm"),
                $sceneLabel
            )
            Enabled = ($null -ne $scene)
            Value = $candidate.FullName
        })
    }

    return Show-WzhkKeypadMenu `
        -Title "SELECT FINAL-RENDER PREPARATION PACKAGE" `
        -Items $items.ToArray() `
        -Context @(
            "The newest package is shown first.",
            "Select the package that contains the artist-approved frozen scene."
        )
}

function Select-Profile {
    param([Parameter(Mandatory = $true)][string]$PrepPath)

    $profiles = @(Get-WzhkProfileCandidates -PrepPath $PrepPath)
    if ($profiles.Count -eq 0) {
        Show-WzhkMessage -Title "NO RENDER PROFILE" -Lines @(
            "No render-profile*.json file was found in:",
            $PrepPath
        ) -Color Red
        return $null
    }

    $items = New-Object System.Collections.Generic.List[object]
    foreach ($profile in @($profiles | Select-Object -First 9)) {
        $resolution = if ($null -ne $profile.Width -and $null -ne $profile.Height) {
            [string]::Format("{0}×{1}", $profile.Width, $profile.Height)
        }
        else {
            "resolution unknown"
        }

        $fpsText = if ($null -ne $profile.Fps) { "$($profile.Fps) fps" } else { "fps unknown" }
        $items.Add([pscustomobject]@{
            Label = [string]::Format(
                "{0}  [{1}]",
                $profile.Name,
                $profile.Slug.ToUpperInvariant()
            )
            Description = [string]::Format(
                "{0}  •  {1}  •  frames {2}–{3}  •  hash {4}",
                $resolution,
                $fpsText,
                $profile.FrameStart,
                $profile.FrameEnd,
                $profile.Hash
            )
            Enabled = $profile.ValidJson
            Value = $profile
        })
    }

    $selection = Show-WzhkKeypadMenu `
        -Title "SELECT OUTPUT PROFILE" `
        -Items $items.ToArray() `
        -Context @(
            "Only profiles that exist in the selected authorization package are offered.",
            "A 4K profile must report 3840×2160; a renamed 1440p profile is not 4K."
        )

    if ($null -eq $selection) {
        return $null
    }
    return $selection.Value
}

function Get-SavedProfileEntries {
    return @(Get-WzhkSavedRenderProfiles -Directory $profileRoot -Recurse)
}

function Get-AllWzhkOutputCandidates {
    $outputMap = @{}
    $roots = @($finalOutputRoot)
    foreach ($savedProfile in @(Get-SavedProfileEntries)) {
        if (-not [string]::IsNullOrWhiteSpace([string]$savedProfile.OutputRoot)) { $roots += [string]$savedProfile.OutputRoot }
    }
    foreach ($root in @($roots | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        $rootItem = Get-Item -LiteralPath $root
        if (Test-Path -LiteralPath (Join-Path $rootItem.FullName "manifests\render-manifest.json") -PathType Leaf) {
            $outputMap[$rootItem.FullName.ToUpperInvariant()] = $rootItem
        }
        foreach ($directory in @(Get-WzhkOutputCandidates -FinalOutputRoot $rootItem.FullName)) {
            $outputMap[$directory.FullName.ToUpperInvariant()] = $directory
        }
    }
    return @($outputMap.Values | Sort-Object LastWriteTimeUtc -Descending)
}

function Select-SavedProfile {
    param(
        [string]$Title = "SELECT SAVED RENDER PROFILE",
        [string]$ExcludePath = "",
        [switch]$AllowInvalid
    )

    $profiles = @(Get-SavedProfileEntries | Where-Object {
        [string]::IsNullOrWhiteSpace($ExcludePath) -or $_.Path -ine $ExcludePath
    })
    if ($profiles.Count -eq 0) {
        Show-WzhkMessage -Title "NO SAVED PROFILES" -Lines @(
            "No reusable render profile is available under:",
            $profileRoot,
            "Choose CREATE NEW RENDER PROFILE first."
        ) -Color Yellow
        return $null
    }

    $pageSize = 7
    $page = 0
    $pageCount = [Math]::Max(1, [int][Math]::Ceiling($profiles.Count / [double]$pageSize))
    while ($true) {
        $items = New-Object System.Collections.Generic.List[object]
        foreach ($profile in @($profiles | Select-Object -Skip ($page * $pageSize) -First $pageSize)) {
            $resolution = [string]::Format("{0}×{1}", $profile.Width, $profile.Height)
            if ([int]$profile.Width -eq 3840 -and [int]$profile.Height -eq 2160) { $resolution = "NATIVE 4K — 3840×2160" }
            $status = if ($profile.Valid) { "VALID" } else { "INVALID" }
            $authorization = if ($profile.AuthorizationRecordValid) { "AUTHORIZED" } else { "UNAUTHORIZED" }
            $sceneShort = if ($profile.SceneSha256.Length -ge 12) { $profile.SceneSha256.Substring(0, 12) } else { $profile.SceneSha256 }
            $profileShort = if ($profile.FileSha256.Length -ge 12) { $profile.FileSha256.Substring(0, 12) } else { $profile.FileSha256 }
            $compatibleOutputs = 0
            if ($profile.Valid) {
                try {
                    $loadedForBrowser = Import-WzhkRenderProfile -Path $profile.Path
                    $browserScenePath = [string]$loadedForBrowser.approvedScene.path
                    $outputMap = @{}
                    $browserRoots = @($finalOutputRoot)
                    if (-not [string]::IsNullOrWhiteSpace([string]$profile.OutputRoot)) { $browserRoots += [string]$profile.OutputRoot }
                    foreach ($browserRoot in @($browserRoots | Select-Object -Unique)) {
                        if (-not (Test-Path -LiteralPath $browserRoot -PathType Container)) { continue }
                        foreach ($directory in @(Get-WzhkOutputCandidates -FinalOutputRoot $browserRoot)) { $outputMap[$directory.FullName.ToUpperInvariant()] = $directory }
                    }
                    if ($profile.OutputPolicy -in @("select-compatible", "resume-compatible") -and (Test-Path -LiteralPath $profile.OutputRoot -PathType Container)) {
                        $configuredDirectory = Get-Item -LiteralPath $profile.OutputRoot
                        $outputMap[$configuredDirectory.FullName.ToUpperInvariant()] = $configuredDirectory
                    }
                    foreach ($directory in $outputMap.Values) {
                        if ((Test-WzhkOutputCompatibility -ProfilePath $profile.Path -ScenePath $browserScenePath -OutputPath $directory.FullName).Compatible) { $compatibleOutputs += 1 }
                    }
                }
                catch { $compatibleOutputs = 0 }
            }
            $modified = [string]$profile.UpdatedAt
            if ($modified.Length -gt 19) { $modified = $modified.Substring(0, 19).Replace("T", " ") }
            $items.Add([pscustomobject]@{
                Label = [string]::Format("{0}  [{1} / {2}]", $profile.Name, $status, $authorization)
                Description = [string]::Format(
                    "ID {0} • {1} • {2} fps • {3} • scene {4} • profile {5} • modified {6} • outputs {7}",
                    $profile.ProfileId, $resolution, $profile.Fps, $profile.QualityMode, $sceneShort, $profileShort, $modified, $compatibleOutputs
                )
                Enabled = ([bool]$profile.Valid -or [bool]$AllowInvalid)
                Value = [pscustomobject]@{ Kind = "Profile"; Profile = $profile }
            })
        }
        if ($page -gt 0) {
            $items.Add([pscustomobject]@{ Label = "PREVIOUS PAGE"; Description = "Show earlier saved profiles."; Enabled = $true; Value = [pscustomobject]@{ Kind = "Previous" } })
        }
        if ($page -lt ($pageCount - 1)) {
            $items.Add([pscustomobject]@{ Label = "NEXT PAGE"; Description = "Show later saved profiles."; Enabled = $true; Value = [pscustomobject]@{ Kind = "Next" } })
        }

        $selection = Show-WzhkKeypadMenu -Title $Title -Items $items.ToArray() -Context @(
            "SAVED PROFILE objects are distinct from built-in TEMPLATE starting points.",
            "Production authorization is recalculated from the exact saved-file SHA-256.",
            [string]::Format("Page {0} of {1}  •  {2} saved profiles", ($page + 1), $pageCount, $profiles.Count)
        )
        if ($null -eq $selection) { return $null }
        if ($selection.Value.Kind -eq "Previous") { $page -= 1; continue }
        if ($selection.Value.Kind -eq "Next") { $page += 1; continue }
        return $selection.Value.Profile
    }
}

function Get-WzhkAuthorizationRecordPath {
    param([Parameter(Mandatory = $true)][string]$SavedProfilePath)

    $directory = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($SavedProfilePath))
    $baseName = [IO.Path]::GetFileNameWithoutExtension($SavedProfilePath)
    return Join-Path $directory ($baseName + ".authorization.json")
}

function Get-WzhkAuthorizationRequestPath {
    param([Parameter(Mandatory = $true)][string]$SavedProfilePath)

    $directory = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($SavedProfilePath))
    $baseName = [IO.Path]::GetFileNameWithoutExtension($SavedProfilePath)
    return Join-Path $directory ($baseName + ".authorization-request.json")
}

function Get-WzhkSavedAuthorization {
    param(
        [Parameter(Mandatory = $true)][string]$SavedProfilePath,
        [Parameter(Mandatory = $true)][string]$ScenePath
    )

    $recordPath = Get-WzhkAuthorizationRecordPath -SavedProfilePath $SavedProfilePath
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
        return [pscustomobject]@{ Valid = $false; Issues = @("No local authorization record exists."); AuthorizationToken = ""; Path = $recordPath }
    }
    $validation = Test-WzhkProfileAuthorizationRecord `
        -ProfilePath $SavedProfilePath `
        -ScenePath $ScenePath `
        -RecordPath $recordPath
    return [pscustomobject]@{
        Valid = [bool]$validation.Valid
        Issues = @($validation.Issues)
        AuthorizationToken = [string]$validation.AuthorizationToken
        Path = $recordPath
    }
}

function Select-SavedProfileOutput {
    param(
        [Parameter(Mandatory = $true)][string]$SavedProfilePath,
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][string]$ScenePath
    )

    $configuredOutput = if ($null -ne $Profile.output.PSObject.Properties["rootDirectory"]) { [string]$Profile.output.rootDirectory } else { $finalOutputRoot }
    try { $configuredOutput = [IO.Path]::GetFullPath($configuredOutput) } catch { $configuredOutput = $finalOutputRoot }
    $outputPolicy = if ($null -ne $Profile.output.PSObject.Properties["policy"]) { [string]$Profile.output.policy } else { "create-new" }
    $existingMap = @{}
    if ($outputPolicy -in @("select-compatible", "resume-compatible") -and (Test-Path -LiteralPath $configuredOutput -PathType Container)) {
        $configuredItem = Get-Item -LiteralPath $configuredOutput
        $existingMap[$configuredItem.FullName.ToUpperInvariant()] = $configuredItem
    }
    $discoveryRoot = if ($outputPolicy -eq "create-new") { $configuredOutput } else { $finalOutputRoot }
    if (Test-Path -LiteralPath $discoveryRoot -PathType Container) {
        foreach ($candidate in @(Get-WzhkOutputCandidates -FinalOutputRoot $discoveryRoot)) {
            $existingMap[$candidate.FullName.ToUpperInvariant()] = $candidate
        }
    }

    $items = New-Object System.Collections.Generic.List[object]
    foreach ($directory in @($existingMap.Values | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 6)) {
        $compatibility = Test-WzhkOutputCompatibility `
            -ProfilePath $SavedProfilePath `
            -ScenePath $ScenePath `
            -OutputPath $directory.FullName
        $stats = Get-WzhkOutputStats -OutputPath $directory.FullName -TotalFrames ([int]$Profile.timeline.frameCount)
        $issueText = if ($compatibility.Compatible) {
            [string]::Format("MATCHED  •  {0:N2}% published", $stats.Percent)
        }
        else {
            "REJECTED  •  " + (@($compatibility.Issues | ForEach-Object { $_.Code }) -join ", ")
        }
        $items.Add([pscustomobject]@{
            Label = $directory.Name
            Description = $issueText
            Enabled = [bool]$compatibility.Compatible
            Value = [pscustomobject]@{ Kind = "Existing"; Path = $directory.FullName }
        })
    }

    $profileSlug = ConvertTo-WzhkProfileSlug -Name ([string]$Profile.profileId)
    $resolutionBase = if ([int]$Profile.resolution.width -eq 1920 -and [int]$Profile.resolution.height -eq 1080) {
        "1080p"
    }
    elseif ([int]$Profile.resolution.width -eq 2560 -and [int]$Profile.resolution.height -eq 1440) {
        "1440p"
    }
    elseif ([int]$Profile.resolution.width -eq 3840 -and [int]$Profile.resolution.height -eq 2160) {
        "4k"
    }
    else {
        [string]::Format("{0}x{1}", $Profile.resolution.width, $Profile.resolution.height)
    }
    $fpsSlug = ([double]$Profile.timeline.fps).ToString("0.###", [Globalization.CultureInfo]::InvariantCulture).Replace(".", "p")
    $dynamicRange = if ($null -ne $Profile.resolution.PSObject.Properties["dynamicRange"]) { [string]$Profile.resolution.dynamicRange } else { "SDR" }
    $resolutionSlug = ConvertTo-WzhkProfileSlug -Name ($resolutionBase + "-" + $fpsSlug + "-" + $dynamicRange)
    $directoryPattern = if ($null -ne $Profile.output.PSObject.Properties["directoryPattern"]) { [string]$Profile.output.directoryPattern } else { "{project}-{preset}-{resolution}-{timestamp}" }
    $newRoot = if ($outputPolicy -eq "create-new") { $configuredOutput } else { $finalOutputRoot }
    $newPath = New-WzhkOutputPath `
        -FinalOutputRoot $newRoot `
        -ProfileSlug $profileSlug `
        -ProjectSlug ([string]$Profile.project) `
        -PresetSlug ([string]$Profile.preset) `
        -ResolutionSlug $resolutionSlug `
        -DirectoryPattern $directoryPattern
    $items.Add([pscustomobject]@{
        Label = "CREATE A NEW UNIQUE RENDER SUBFOLDER HERE"
        Description = $newPath
        Enabled = $true
        Value = [pscustomobject]@{ Kind = "New"; Path = $newPath }
    })
    $items.Add([pscustomobject]@{
        Label = "ENTER CUSTOM OUTPUT PATH"
        Description = "The path is still checked for exact scene/profile/resolution/FPS/format/range compatibility."
        Enabled = $true
        Value = [pscustomobject]@{ Kind = "Custom"; Path = "" }
    })

    $selection = Show-WzhkKeypadMenu -Title "SELECT OUTPUT / RESUME TARGET" -Items $items.ToArray() -Context @(
        "Only matching outputs can be resumed.",
        "A 1440p output can never be reused by a native 4K profile.",
        "Saved output policy: $outputPolicy  •  saved path: $configuredOutput"
    )
    if ($null -eq $selection) { return "" }

    $selectedPath = [string]$selection.Value.Path
    if ($selection.Value.Kind -eq "Custom") {
        $entered = Read-WzhkTextInput `
            -Prompt "Absolute output directory" `
            -Default $newPath `
            -Required `
            -MaximumLength 240 `
            -AllowCancel
        if ($null -eq $entered) { return "" }
        $selectedPath = [IO.Path]::GetFullPath([string]$entered)
    }

    $finalCompatibility = Test-WzhkOutputCompatibility `
        -ProfilePath $SavedProfilePath `
        -ScenePath $ScenePath `
        -OutputPath $selectedPath
    if (-not $finalCompatibility.Compatible) {
        Show-WzhkMessage -Title "OUTPUT INCOMPATIBLE" -Lines @(
            @($finalCompatibility.Issues | ForEach-Object { $_.Code + ": " + $_.Message })
        ) -Color Red
        return ""
    }
    return $selectedPath
}

function Get-WzhkApprovedSceneCandidatesForBuilder {
    $seen = @{}
    $candidates = New-Object System.Collections.Generic.List[object]
    foreach ($prep in @(Get-WzhkPrepCandidates -TestOutputRoot $testOutputRoot)) {
        $scenePath = Find-WzhkApprovedScene -PrepPath $prep.FullName
        if ([string]::IsNullOrWhiteSpace($scenePath)) { continue }
        $fullScenePath = [IO.Path]::GetFullPath($scenePath)
        $approvedCandidateRoot = [IO.Path]::GetFullPath((Join-Path $prep.FullName "approved-candidate")).TrimEnd('\') + '\'
        if (-not $fullScenePath.StartsWith($approvedCandidateRoot, [StringComparison]::OrdinalIgnoreCase)) { continue }
        if ($seen.ContainsKey($fullScenePath.ToUpperInvariant())) { continue }
        $seen[$fullScenePath.ToUpperInvariant()] = $true
        $sceneInfo = Get-WzhkSceneInfo -Path $fullScenePath
        if (-not [bool]$sceneInfo.ManifestValid -or [string]::IsNullOrWhiteSpace([string]$sceneInfo.ManifestPath)) { continue }
        $manifestHash = if (
            -not [string]::IsNullOrWhiteSpace([string]$sceneInfo.ManifestPath) -and
            (Test-Path -LiteralPath $sceneInfo.ManifestPath -PathType Leaf)
        ) {
            Get-WzhkHashPrefix -Path $sceneInfo.ManifestPath -Length 64
        }
        else { "" }
        if ([string]::IsNullOrWhiteSpace($manifestHash)) { continue }
        $sceneHash = Get-WzhkHashPrefix -Path $fullScenePath -Length 64
        $matchingProfile = @(
            Get-WzhkProfileCandidates -PrepPath $prep.FullName |
                Where-Object {
                    $_.ValidJson -and
                    [string]$_.SceneHash -ieq $sceneHash -and
                    [string]$_.SceneManifestHash -ieq $manifestHash
                } |
                Select-Object -First 1
        )
        if ($matchingProfile.Count -ne 1) { continue }
        if (
            $null -eq $sceneInfo.BlenderVersion -or
            $null -eq $sceneInfo.FrameStart -or
            $null -eq $sceneInfo.FrameEnd -or
            $null -eq $sceneInfo.Fps -or
            $null -eq $sceneInfo.ActiveCamera -or
            $null -eq $sceneInfo.Preset
        ) { continue }
        $candidates.Add([pscustomobject]@{
            Path = $fullScenePath
            Sha256 = $sceneHash
            Hash = $sceneHash
            ManifestPath = [string]$sceneInfo.ManifestPath
            ManifestSha256 = $manifestHash
            PackagePath = $prep.FullName
            PackageName = $prep.Name
            BaseProfilePath = [string]$matchingProfile[0].Path
            Verification = "exact scene SHA-256 + exact manifest SHA-256 + approved render-profile identity"
            BlenderVersion = [string]$sceneInfo.BlenderVersion
            ObjectCount = $sceneInfo.ObjectCount
            MaterialCount = $sceneInfo.MaterialCount
            CollectionCount = $sceneInfo.CollectionCount
            FCurveCount = $sceneInfo.FCurveCount
            FrameStart = [int]$sceneInfo.FrameStart
            FrameEnd = [int]$sceneInfo.FrameEnd
            Fps = [double]$sceneInfo.Fps
            ActiveCamera = [string]$sceneInfo.ActiveCamera
            Preset = [string]$sceneInfo.Preset
            MacroStateCount = $sceneInfo.MacroStateCount
            AudioBusCurveCount = $sceneInfo.AudioBusCurveCount
        })
    }
    return $candidates.ToArray()
}

function Select-OutputDirectory {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $existing = @(Get-WzhkOutputCandidates -FinalOutputRoot $finalOutputRoot -ProfileSlug $Profile.Slug)
    $items = New-Object System.Collections.Generic.List[object]

    foreach ($directory in @($existing | Select-Object -First 7)) {
        $stats = Get-WzhkOutputStats -OutputPath $directory.FullName -TotalFrames $Profile.FrameEnd
        $items.Add([pscustomobject]@{
            Label = "RESUME / INSPECT  " + $directory.Name
            Description = [string]::Format(
                "{0:N2}% published  •  {1} frames  •  latest frame {2}",
                $stats.Percent,
                $stats.Published,
                $stats.LatestFrame
            )
            Enabled = $true
            Value = $directory.FullName
        })
    }

    $newPath = New-WzhkOutputPath -FinalOutputRoot $finalOutputRoot -ProfileSlug $Profile.Slug
    $items.Add([pscustomobject]@{
        Label = "CREATE NEW OUTPUT"
        Description = $newPath
        Enabled = $true
        Value = $newPath
    })

    $selection = Show-WzhkKeypadMenu `
        -Title "SELECT OUTPUT / RESUME TARGET" `
        -Items $items.ToArray() `
        -Context @(
            "Production rendering is resumable.",
            "Select an existing compatible output to continue, or create a new isolated output."
        )

    if ($null -eq $selection) {
        return $null
    }
    return [string]$selection.Value
}

function Get-ModeDetails {
    param(
        [string]$ModeLabel,
        [object]$Scene,
        [object]$Profile,
        [string]$OutputPath,
        [string]$AuthorizationToken
    )

    $stats = Get-WzhkOutputStats -OutputPath $OutputPath -TotalFrames $Profile.FrameEnd
    $resolution = if ($null -ne $Profile.Width -and $null -ne $Profile.Height) {
        [string]::Format("{0}×{1}", $Profile.Width, $Profile.Height)
    }
    else {
        "unknown"
    }
    $tokenState = if ([string]::IsNullOrWhiteSpace($AuthorizationToken)) {
        "NOT FOUND"
    }
    else {
        "MATCHED TO SCENE + PROFILE"
    }

    return @(
        "MODE             : $ModeLabel",
        "PREP PACKAGE     : $script:SelectedPrep",
        "SCENE            : $($Scene.Path)",
        "SCENE SHA-12     : $($Scene.Hash)",
        "SCENE MANIFEST   : $(Get-DisplayValue $Scene.ManifestPath)",
        "SCENE COUNTS     : objects $(Get-DisplayValue $Scene.ObjectCount)  •  materials $(Get-DisplayValue $Scene.MaterialCount)  •  collections $(Get-DisplayValue $Scene.CollectionCount)",
        "ANIMATION        : F-curves $(Get-DisplayValue $Scene.FCurveCount)  •  macro states $(Get-DisplayValue $Scene.MacroStateCount)  •  audio-bus curves $(Get-DisplayValue $Scene.AudioBusCurveCount)",
        "PROFILE          : $($Profile.Path)",
        "PROFILE SHA-12   : $($Profile.Hash)",
        "RESOLUTION       : $resolution  •  $(Get-DisplayValue $Profile.Fps) fps  •  $($Profile.Slug.ToUpperInvariant())",
        "FRAME CONTRACT   : $($Profile.FrameStart)–$($Profile.FrameEnd)  •  format $(Get-DisplayValue $Profile.Format)",
        "OUTPUT           : $OutputPath",
        "CURRENT PROGRESS : $($stats.Published) published  •  $($stats.Inflight) in-flight  •  $([Math]::Round($stats.Percent, 2))%",
        "AUTHORIZATION    : $tokenState",
        "VISUAL WATCHER   : $(if (Test-Path -LiteralPath $watcherScript) { 'READY' } else { 'NOT INSTALLED' })"
    )
}

function Confirm-WzhkMode {
    param(
        [string]$ModeLabel,
        [string[]]$Details,
        [switch]$Production
    )

    Write-WzhkScreenHeader -Subtitle "1 // MODE CONFIRMATION"
    foreach ($line in $Details) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color White
    }

    $locked = Read-WzhkYesNo `
        -Prompt "Lock this mode and configuration?" `
        -YesText "LOCK MODE" `
        -NoText "FIX MODE"

    if (-not $locked) {
        return $false
    }

    Write-WzhkScreenHeader -Subtitle "1 // FINAL CONFIRMATION"
    foreach ($line in $Details) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color White
    }

    if ($Production) {
        Write-WzhkFrameDivider
        Write-WzhkFrameLine -Text "  WARNING: This starts or resumes the complete production frame-sequence render." -Color Yellow
        Write-WzhkFrameLine -Text "  The exact authorization token remains enforced by render-trackprompt-final.ps1." -Color Yellow
    }
    else {
        Write-WzhkFrameDivider
        Write-WzhkFrameLine -Text "  This inspection mode does not render the complete frame sequence." -Color Green
    }

    return Read-WzhkYesNo `
        -Prompt "Final confirmation: execute now?" `
        -YesText "EXECUTE" `
        -NoText "CANCEL"
}

function Invoke-WzhkAuthorizationWizard {
    param([Parameter(Mandatory = $true)][string]$SavedProfilePath)

    try {
        $profile = Import-WzhkRenderProfile -Path $SavedProfilePath -VerifyFiles
        $scenePath = [string]$profile.approvedScene.path
        $requestPath = Get-WzhkAuthorizationRequestPath -SavedProfilePath $SavedProfilePath
        $request = New-WzhkProfileAuthorizationRequest `
            -ProfilePath $SavedProfilePath `
            -ScenePath $scenePath `
            -Path $requestPath

        $profileFileHash = Get-WzhkFileSha256 -Path $SavedProfilePath
        $sceneHash = Get-WzhkFileSha256 -Path $scenePath
        $details = @(
            "STATUS           : UNAUTHORIZED / REQUEST PENDING",
            "PROFILE          : $($profile.displayName)",
            "PROFILE ID       : $($profile.profileId)",
            "SCENE SHA-256    : $sceneHash",
            "PROFILE SHA-256  : $profileFileHash",
            "RESOLUTION       : $($profile.resolution.width)×$($profile.resolution.height)",
            "TIMELINE         : $($profile.timeline.frameStart)–$($profile.timeline.frameEnd) @ $($profile.timeline.fps) fps",
            "TOKEN            : $($request.AuthorizationToken)",
            "REQUEST          : $requestPath"
        )

        $confirmed = Confirm-WzhkTwoStage `
            -Title "EXACT PROFILE AUTHORIZATION" `
            -Details $details `
            -FirstPrompt "Authorize this exact scene/profile pair?" `
            -FirstYesText "AUTHORIZE EXACT PAIR" `
            -SecondPrompt "Confirm full production authorization for these exact hashes and settings?" `
            -SecondYesText "CONFIRM AUTHORIZATION" `
            -Warnings @(
                "This permits a complete production frame-sequence render.",
                "Any saved-profile byte change or scene change invalidates this record."
            )
        if (-not $confirmed) { return $false }

        $recordPath = Get-WzhkAuthorizationRecordPath -SavedProfilePath $SavedProfilePath
        $record = New-WzhkProfileAuthorizationRecord `
            -ProfilePath $SavedProfilePath `
            -ScenePath $scenePath `
            -SettingsAndHashesReviewed `
            -ProductionRenderAuthorized `
            -Path $recordPath
        Show-WzhkDoneAnimation -Title "PROFILE AUTHORIZED" -Details @(
            "AUTHORIZED PROFILE: $($profile.displayName)",
            "Scene SHA-12: $($sceneHash.Substring(0, 12))",
            "Profile SHA-12: $($profileFileHash.Substring(0, 12))",
            "Record: $($record.Path)"
        )
        return $true
    }
    catch {
        Show-WzhkMessage -Title "AUTHORIZATION FAILED" -Lines @($_.Exception.Message) -Color Red
        return $false
    }
}

function Invoke-WzhkSavedProfileRenderWizard {
    param(
        [ValidateSet("Preflight", "DryRun", "Production")][string]$Mode,
        [string]$SavedProfilePath = ""
    )

    if ([string]::IsNullOrWhiteSpace($SavedProfilePath)) {
        $selected = Select-SavedProfile -Title $(if ($Mode -eq "Production") { "RENDER WITH SAVED PROFILE" } else { "SELECT SAVED PROFILE FOR " + $Mode.ToUpperInvariant() })
        if ($null -eq $selected) { return [pscustomobject]@{ Ok = $false; ExitCode = 2; Status = "cancelled-profile-selection" } }
        $SavedProfilePath = [string]$selected.Path
    }

    try {
        $profile = Import-WzhkRenderProfile -Path $SavedProfilePath -VerifyFiles
        $scenePath = [string]$profile.approvedScene.path
        $authorization = Get-WzhkSavedAuthorization -SavedProfilePath $SavedProfilePath -ScenePath $scenePath
        if ($Mode -eq "Production" -and -not $authorization.Valid) {
            Show-WzhkMessage -Title "PROFILE UNAUTHORIZED" -Lines @(
                "Production requires a valid two-confirmation authorization record.",
                "Profile: $SavedProfilePath",
                "Reason: " + (@($authorization.Issues) -join " "),
                "Use LOAD / EDIT SAVED PROFILE → MORE ACTIONS → GENERATE AUTHORIZATION."
            ) -Color Red
            return [pscustomobject]@{ Ok = $false; ExitCode = 3; Status = "unauthorized" }
        }
        $outputPath = Select-SavedProfileOutput `
            -SavedProfilePath $SavedProfilePath `
            -Profile $profile `
            -ScenePath $scenePath
        if ([string]::IsNullOrWhiteSpace($outputPath)) { return [pscustomobject]@{ Ok = $false; ExitCode = 2; Status = "cancelled-output-selection" } }

        $fileHash = Get-WzhkFileSha256 -Path $SavedProfilePath
        $sceneHash = Get-WzhkFileSha256 -Path $scenePath
        $modeLabel = switch ($Mode) {
            "Preflight" { "PREFLIGHT // SAVED PROFILE" }
            "DryRun" { "DRY RUN // SAVED PROFILE RESUME PLAN" }
            "Production" { "PRODUCTION // SAVED PROFILE" }
        }
        $resolutionLabel = if ([int]$profile.resolution.width -eq 3840 -and [int]$profile.resolution.height -eq 2160) {
            "NATIVE 4K — 3840×2160"
        }
        else {
            [string]::Format("{0}×{1}", $profile.resolution.width, $profile.resolution.height)
        }
        $qualityMode = if ($null -ne $profile.PSObject.Properties["render"] -and $null -ne $profile.render.PSObject.Properties["qualityMode"]) {
            [string]$profile.render.qualityMode
        }
        elseif ($null -ne $profile.PSObject.Properties["quality"] -and $null -ne $profile.quality.PSObject.Properties["mode"]) {
            [string]$profile.quality.mode
        }
        elseif ($null -ne $profile.PSObject.Properties["qualityMode"]) {
            [string]$profile.qualityMode
        }
        else {
            [string]$profile.templateId
        }
        $openOutputWhenComplete = (
            $null -ne $profile.dashboard.PSObject.Properties["openOutputWhenComplete"] -and
            [bool]$profile.dashboard.openOutputWhenComplete
        )
        $details = @(
            "MODE             : $modeLabel",
            "SAVED PROFILE    : $($profile.displayName)",
            "PROFILE ID       : $($profile.profileId)",
            "PROFILE PATH     : $SavedProfilePath",
            "PROFILE SHA-12   : $($fileHash.Substring(0, 12))",
            "APPROVED SCENE   : $scenePath",
            "SCENE SHA-12     : $($sceneHash.Substring(0, 12))",
            "RESOLUTION       : $resolutionLabel",
            "TIMELINE         : frames $($profile.timeline.frameStart)–$($profile.timeline.frameEnd) @ $($profile.timeline.fps) fps",
            "SEQUENCE         : $($profile.imageSequence.format) $($profile.imageSequence.bitDepth)-bit $($profile.imageSequence.colorMode)",
            "QUALITY          : $qualityMode / $($profile.render.samples) samples",
            "CHUNK SIZE       : $($profile.chunking.framesPerChunk)",
            "OUTPUT           : $outputPath",
            "AUTHORIZATION    : $(if ($authorization.Valid) { 'AUTHORIZED' } else { 'UNAUTHORIZED (inspection mode only)' })"
        )

        $confirmed = Confirm-WzhkMode `
            -ModeLabel $modeLabel `
            -Details $details `
            -Production:($Mode -eq "Production")
        if (-not $confirmed) { return [pscustomobject]@{ Ok = $false; ExitCode = 2; Status = "cancelled-confirmation" } }

        Write-WzhkScreenHeader -Subtitle "2 // THE FRAME"
        foreach ($line in $details) { Write-WzhkFrameLine -Text ("  " + $line) -Color White }
        Write-WzhkFrameDivider
        Write-WzhkFrameLine -Text "  LIVE PROCESS OUTPUT" -Color Cyan

        $frameCount = [int]$profile.timeline.frameEnd - [int]$profile.timeline.frameStart + 1
        $autoWatcher = (
            $Mode -eq "Production" -and
            (-not $NoAutoWatcher) -and
            [bool]$profile.dashboard.enabled -and
            [bool]$profile.dashboard.autoLaunch
        )
        $result = Invoke-WzhkRenderMode `
            -Mode $Mode `
            -RenderScript $renderScript `
            -ScenePath $scenePath `
            -ProfilePath $SavedProfilePath `
            -OutputDirectory $outputPath `
            -AuthorizationToken $(if ($authorization.Valid) { $authorization.AuthorizationToken } else { "" }) `
            -WatcherScript $watcherScript `
            -TotalFrames $frameCount `
            -Fps ([double]$profile.timeline.fps) `
            -FrameStart ([int]$profile.timeline.frameStart) `
            -WatcherRefreshSeconds ([int]$profile.dashboard.refreshSeconds) `
            -OpenOutputWhenComplete:$openOutputWhenComplete `
            -AutoWatcher:$autoWatcher

        if ($result.Ok -and $Mode -eq "Production" -and $openOutputWhenComplete -and -not $autoWatcher) {
            try {
                Open-WzhkOutput -OutputDirectory $outputPath -OpenLatestFrame
                Write-WzhkFrameLine -Text "  Saved post-render preference opened the output and latest frame." -Color Green
            }
            catch {
                Write-WzhkFrameLine -Text ("  WARNING: render succeeded, but the output could not be opened: " + $_.Exception.Message) -Color Yellow
            }
        }

        Write-WzhkFrameDivider
        Write-WzhkFrameLine -Text ("  EXIT CODE : " + $result.ExitCode) -Color $(if ($result.Ok) { "Green" } else { "Red" })
        Write-WzhkFrameLine -Text ("  LOG       : " + $result.LogPath) -Color DarkGray
        Write-WzhkFrameBottom
        if ($result.Ok) {
            Show-WzhkDoneAnimation -Title $modeLabel -Details @(
                "Saved JSON was consumed directly.",
                "Output: $outputPath",
                "Log: $($result.LogPath)"
            )
        }
        else {
            Show-WzhkMessage -Title "MISSION INTERRUPTED" -Lines @(
                "The renderer ended with exit code $($result.ExitCode).",
                "No destructive cleanup was performed.",
                "Log: $($result.LogPath)"
            ) -Color Red
        }
        return [pscustomobject]@{ Ok = [bool]$result.Ok; ExitCode = [int]$result.ExitCode; Status = $(if ($result.Ok) { "completed" } else { "renderer-failed" }); Result = $result }
    }
    catch {
        Show-WzhkMessage -Title "PROFILE MODE FAILED" -Lines @($_.Exception.Message) -Color Red
        return [pscustomobject]@{ Ok = $false; ExitCode = 1; Status = "profile-mode-failed"; Error = $_.Exception.Message }
    }
}

function Invoke-WzhkProfilePostSave {
    param([Parameter(Mandatory = $true)][object]$SaveResult)

    $savedPath = [string]$SaveResult.Path
    $savedProfile = $SaveResult.Profile
    Write-WzhkScreenHeader -Subtitle "SAVED PROFILE // AUTHORIZATION"
    Write-WzhkStatusLabel -Label "ARTIFACT" -Status "SAVED"
    Write-WzhkStatusLabel -Label "PRODUCTION" -Status "UNAUTHORIZED"
    Write-WzhkFrameLine -Text ("  PROFILE : " + $savedProfile.displayName) -Color White
    Write-WzhkFrameLine -Text ("  PATH    : " + $savedPath) -Color White
    Write-WzhkFrameLine -Text ("  SHA-12  : " + $SaveResult.FileSha256.Substring(0, 12)) -Color White
    $generateRequest = Read-WzhkYesNo `
        -Prompt "Generate an exact scene/profile authorization request now?" `
        -YesText "GENERATE REQUEST" `
        -NoText "KEEP UNAUTHORIZED"
    if ($generateRequest) {
        $null = Invoke-WzhkAuthorizationWizard -SavedProfilePath $savedPath
    }

    while ($true) {
        $items = @(
            [pscustomobject]@{ Label = "RENDER NOW"; Description = "Requires a currently valid exact authorization record and two render confirmations."; Enabled = $true; Value = "Render" },
            [pscustomobject]@{ Label = "RUN PREFLIGHT"; Description = "Validate profile, scene, Blender, storage, and output without rendering."; Enabled = $true; Value = "Preflight" },
            [pscustomobject]@{ Label = "RUN DRY-RUN / RESUME PLAN"; Description = "Show exact missing chunks without output initialization or rendering."; Enabled = $true; Value = "DryRun" },
            [pscustomobject]@{ Label = "RETURN TO MAIN MENU"; Description = "Keep the saved profile for later use."; Enabled = $true; Value = "Return" }
        )
        $selection = Show-WzhkKeypadMenu -Title "SAVED PROFILE // NEXT ACTION" -Items $items -Context @(
            "The saved JSON remains the source of truth.",
            "No mode reconstructs settings from menu labels."
        )
        if ($null -eq $selection -or $selection.Value -eq "Return") { return }
        switch ([string]$selection.Value) {
            "Render" { $null = Invoke-WzhkSavedProfileRenderWizard -Mode "Production" -SavedProfilePath $savedPath }
            "Preflight" { $null = Invoke-WzhkSavedProfileRenderWizard -Mode "Preflight" -SavedProfilePath $savedPath }
            "DryRun" { $null = Invoke-WzhkSavedProfileRenderWizard -Mode "DryRun" -SavedProfilePath $savedPath }
        }
    }
}

function Invoke-WzhkCreateProfileWizard {
    if ([string]::IsNullOrWhiteSpace($script:SelectedPrep) -or -not (Test-Path -LiteralPath $script:SelectedPrep -PathType Container)) {
        $selectedPrepForCreate = Select-PrepPackage
        if ($null -eq $selectedPrepForCreate) { return }
        $script:SelectedPrep = [string]$selectedPrepForCreate.Value
    }
    $scenes = @(
        Get-WzhkApprovedSceneCandidatesForBuilder |
            Where-Object { $_.PackagePath -ieq $script:SelectedPrep }
    )
    if ($scenes.Count -eq 0) {
        Show-WzhkMessage -Title "NO VERIFIED APPROVED SCENE" -Lines @(
            "The selected preparation package has no scene bound to an exact approved manifest/profile identity.",
            $script:SelectedPrep,
            "No profile was created."
        ) -Color Red
        return
    }

    $orderedScenes = @($scenes | Sort-Object PackageName)
    $baseProfile = $null
    foreach ($candidate in $orderedScenes) {
        try {
            $baseProfile = Import-WzhkRenderProfile -Path $candidate.BaseProfilePath -Normalize
            break
        }
        catch { continue }
    }

    $result = Invoke-WzhkProfileBuilder `
        -SceneCandidates $orderedScenes `
        -BaseProfile $baseProfile `
        -ProfileDirectory $profileRoot `
        -DefaultProject "trip-to-andromeda" `
        -DefaultPreset "space-journey"
    if ($null -ne $result) { Invoke-WzhkProfilePostSave -SaveResult $result }
}

function Show-WzhkSavedProfileInspection {
    param([Parameter(Mandatory = $true)][string]$SavedProfilePath)

    try {
        $profile = Import-WzhkRenderProfile -Path $SavedProfilePath -VerifyFiles
        $authorization = Get-WzhkSavedAuthorization -SavedProfilePath $SavedProfilePath -ScenePath ([string]$profile.approvedScene.path)
        $savedFileSha256 = Get-WzhkFileSha256 -Path $SavedProfilePath
        Show-WzhkProfileSummary -Profile $profile -Title "SAVED PROFILE // INSPECT" -AuthorizationStatus $(if ($authorization.Valid) { "AUTHORIZED" } else { "UNAUTHORIZED" }) -SavedFileSha256 $savedFileSha256 -SafetyLines @(
            "SAVED-FILE SHA-256: $savedFileSha256",
            "AUTHORIZATION: $(if ($authorization.Valid) { 'AUTHORIZED' } else { 'UNAUTHORIZED' })",
            "AUTH RECORD: $($authorization.Path)"
        )
        Write-WzhkFrameDivider
        Write-WzhkFrameLine -Text "  Press any key to return to the saved-profile actions." -Color DarkGray
        Write-WzhkFrameBottom
        $null = [Console]::ReadKey($true)
    }
    catch {
        Show-WzhkMessage -Title "PROFILE INSPECTION FAILED" -Lines @($_.Exception.Message) -Color Red
    }
}

function Invoke-WzhkCompareSavedProfiles {
    param([Parameter(Mandatory = $true)][string]$LeftPath)

    $rightInfo = Select-SavedProfile -Title "COMPARE AGAINST SAVED PROFILE" -ExcludePath $LeftPath
    if ($null -eq $rightInfo) { return }
    try {
        $left = Import-WzhkRenderProfile -Path $LeftPath
        $right = Import-WzhkRenderProfile -Path $rightInfo.Path
        $differences = @(Compare-WzhkRenderProfiles -Left $left -Right $right)
        if ($differences.Count -eq 0) {
            Write-WzhkScreenHeader -Subtitle "SAVED PROFILE // COMPARE"
            Write-WzhkFrameLine -Text ("  LEFT  : " + $left.displayName) -Color Cyan
            Write-WzhkFrameLine -Text ("  RIGHT : " + $right.displayName) -Color Magenta
            Write-WzhkFrameDivider
            Write-WzhkFrameLine -Text "  No resolved setting differences were found." -Color Green
            Write-WzhkFrameDivider
            Write-WzhkFrameLine -Text "  Press any key to return." -Color DarkGray
            Write-WzhkFrameBottom
            $null = [Console]::ReadKey($true)
        }
        else {
            $pageSize = 12
            $page = 0
            $pageCount = [int][Math]::Ceiling($differences.Count / [double]$pageSize)
            while ($true) {
                Write-WzhkScreenHeader -Subtitle ([string]::Format("SAVED PROFILE // COMPARE // PAGE {0} OF {1}", ($page + 1), $pageCount))
                Write-WzhkFrameLine -Text ("  LEFT  : " + $left.displayName) -Color Cyan
                Write-WzhkFrameLine -Text ("  RIGHT : " + $right.displayName) -Color Magenta
                Write-WzhkFrameLine -Text ([string]::Format("  DIFFERENCES: {0} total; every difference is available across these pages.", $differences.Count)) -Color Yellow
                Write-WzhkFrameDivider
                foreach ($difference in @($differences | Select-Object -Skip ($page * $pageSize) -First $pageSize)) {
                    Write-WzhkFrameLine -Text ([string]::Format(
                        "  {0}: {1}  →  {2}",
                        $difference.Path,
                        $difference.Left,
                        $difference.Right
                    )) -Color White
                }
                Write-WzhkFrameDivider
                Write-WzhkFrameLine -Text "  [N] next page  •  [P] previous page  •  any other key returns" -Color DarkGray
                Write-WzhkFrameBottom
                $key = [Console]::ReadKey($true)
                if ($key.Key -eq [ConsoleKey]::N -and $page -lt ($pageCount - 1)) { $page += 1; continue }
                if ($key.Key -eq [ConsoleKey]::P -and $page -gt 0) { $page -= 1; continue }
                break
            }
        }
    }
    catch {
        Show-WzhkMessage -Title "PROFILE COMPARISON FAILED" -Lines @($_.Exception.Message) -Color Red
    }
}

function Invoke-WzhkSavedProfileMoreActions {
    param([Parameter(Mandatory = $true)][string]$SavedProfilePath)

    while ($true) {
        $items = @(
            [pscustomobject]@{ Label = "COMPARE PROFILES"; Description = "Show resolved differences against another saved profile."; Enabled = $true; Value = "Compare" },
            [pscustomobject]@{ Label = "GENERATE / RENEW AUTHORIZATION"; Description = "Create a pending request and require two exact confirmations."; Enabled = $true; Value = "Authorize" },
            [pscustomobject]@{ Label = "EXPORT / REFRESH SUMMARY"; Description = "Regenerate the sibling human-readable summary without changing settings."; Enabled = $true; Value = "Summary" },
            [pscustomobject]@{ Label = "DELETE PROFILE"; Description = "Remove the local profile and sibling records only after two confirmations."; Enabled = $true; Value = "Delete" },
            [pscustomobject]@{ Label = "RETURN"; Description = "Return to profile actions."; Enabled = $true; Value = "Return" }
        )
        $selection = Show-WzhkKeypadMenu -Title "SAVED PROFILE // MORE ACTIONS" -Items $items
        if ($null -eq $selection -or $selection.Value -eq "Return") { return $false }
        switch ([string]$selection.Value) {
            "Compare" { Invoke-WzhkCompareSavedProfiles -LeftPath $SavedProfilePath }
            "Authorize" { $null = Invoke-WzhkAuthorizationWizard -SavedProfilePath $SavedProfilePath }
            "Summary" {
                try {
                    $profile = Import-WzhkRenderProfile -Path $SavedProfilePath
                    $save = Save-WzhkRenderProfile -Profile $profile -Path $SavedProfilePath -Force
                    Show-WzhkMessage -Title "PROFILE SUMMARY EXPORTED" -Lines @($save.SummaryPath) -Color Green
                }
                catch { Show-WzhkMessage -Title "SUMMARY EXPORT FAILED" -Lines @($_.Exception.Message) -Color Red }
            }
            "Delete" {
                $profile = Import-WzhkRenderProfile -Path $SavedProfilePath
                $confirmed = Confirm-WzhkTwoStage `
                    -Title "DELETE SAVED PROFILE" `
                    -Details @("PROFILE: $($profile.displayName)", "PATH: $SavedProfilePath") `
                    -FirstPrompt "Select this exact local profile for deletion?" `
                    -FirstYesText "SELECT PROFILE" `
                    -SecondPrompt "Final confirmation: delete profile, summary, request, and authorization record?" `
                    -SecondYesText "DELETE PROFILE" `
                    -Warnings @("Render outputs are never deleted by this action.")
                if ($confirmed) {
                    $null = Remove-WzhkRenderProfile -Path $SavedProfilePath -ConfirmDeletion
                    Show-WzhkDoneAnimation -Title "PROFILE DELETED" -Details @(
                        "Removed local profile artifacts: $SavedProfilePath",
                        "Existing render outputs were preserved."
                    )
                    return $true
                }
            }
        }
    }
}

function Invoke-WzhkSavedProfileManager {
    $selected = Select-SavedProfile -Title "LOAD / EDIT SAVED PROFILE" -AllowInvalid
    if ($null -eq $selected) { return }
    $savedPath = [string]$selected.Path

    if (-not [bool]$selected.Valid) {
        while (Test-Path -LiteralPath $savedPath -PathType Leaf) {
            $recoveryItems = @(
                [pscustomobject]@{ Label = "INSPECT VALIDATION ERRORS"; Description = "Show why this parseable saved profile is invalid."; Enabled = $true; Value = "Inspect" },
                [pscustomobject]@{ Label = "EDIT / REPAIR PROFILE"; Description = "Load recoverable values into the 13-stage builder and validate before save."; Enabled = $true; Value = "Edit" },
                [pscustomobject]@{ Label = "DELETE INVALID PROFILE"; Description = "Delete only this local profile and sibling records after two confirmations."; Enabled = $true; Value = "Delete" },
                [pscustomobject]@{ Label = "RETURN"; Description = "Return to Mission Control without changing the file."; Enabled = $true; Value = "Return" }
            )
            $recovery = Show-WzhkKeypadMenu -Title "INVALID SAVED PROFILE // RECOVERY" -Items $recoveryItems -Context @(
                "PROFILE: $($selected.Name)",
                "PATH: $savedPath",
                "Invalid profiles can never render, preflight, dry-run, or authorize."
            )
            if ($null -eq $recovery -or $recovery.Value -eq "Return") { return }
            switch ([string]$recovery.Value) {
                "Inspect" {
                    Show-WzhkMessage -Title "INVALID PROFILE // VALIDATION ERRORS" -Lines @(
                        "PATH: $savedPath",
                        "SAVED-FILE SHA-256: $($selected.FileSha256)",
                        @($selected.Errors | ForEach-Object { "ERROR: " + [string]$_ })
                    ) -Color Red
                }
                "Edit" {
                    try {
                        $rawProfile = Get-Content -LiteralPath $savedPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
                        $repairResult = Invoke-WzhkProfileBuilder `
                            -SceneCandidates @(Get-WzhkApprovedSceneCandidatesForBuilder) `
                            -InitialProfile $rawProfile `
                            -InitialProfilePath $savedPath `
                            -ProfileDirectory $profileRoot
                        if ($null -ne $repairResult) {
                            Invoke-WzhkProfilePostSave -SaveResult $repairResult
                            return
                        }
                    }
                    catch { Show-WzhkMessage -Title "PROFILE REPAIR FAILED" -Lines @($_.Exception.Message) -Color Red }
                }
                "Delete" {
                    $confirmed = Confirm-WzhkTwoStage `
                        -Title "DELETE INVALID SAVED PROFILE" `
                        -Details @("PROFILE: $($selected.Name)", "PATH: $savedPath") `
                        -FirstPrompt "Select this exact invalid local profile for deletion?" `
                        -FirstYesText "SELECT PROFILE" `
                        -SecondPrompt "Final confirmation: delete profile and sibling local records?" `
                        -SecondYesText "DELETE PROFILE" `
                        -Warnings @("Render outputs are never deleted by this action.")
                    if ($confirmed) {
                        $null = Remove-WzhkRenderProfile -Path $savedPath -ConfirmDeletion
                        Show-WzhkDoneAnimation -Title "INVALID PROFILE DELETED" -Details @(
                            "Removed local profile artifacts: $savedPath",
                            "Existing render outputs were preserved."
                        )
                        return
                    }
                }
            }
        }
        return
    }

    while (Test-Path -LiteralPath $savedPath -PathType Leaf) {
        $profile = Import-WzhkRenderProfile -Path $savedPath
        $items = @(
            [pscustomobject]@{ Label = "INSPECT"; Description = "Review complete normalized settings, hashes, warnings, and authorization."; Enabled = $true; Value = "Inspect" },
            [pscustomobject]@{ Label = "RENDER"; Description = "Render only with a valid exact authorization and two confirmations."; Enabled = $true; Value = "Render" },
            [pscustomobject]@{ Label = "PREFLIGHT"; Description = "Run read-only production preflight."; Enabled = $true; Value = "Preflight" },
            [pscustomobject]@{ Label = "DRY RUN / RESUME PLAN"; Description = "Read-only missing-frame and compatibility plan."; Enabled = $true; Value = "DryRun" },
            [pscustomobject]@{ Label = "EDIT / SAVE AS NEW"; Description = "Load all values into the 13-stage builder; choose same path or a new path at save."; Enabled = $true; Value = "Edit" },
            [pscustomobject]@{ Label = "DUPLICATE"; Description = "Create a new stable ID, renderer ID, file, and pending authorization state."; Enabled = $true; Value = "Duplicate" },
            [pscustomobject]@{ Label = "RENAME"; Description = "Change the display name atomically; authorization becomes stale."; Enabled = $true; Value = "Rename" },
            [pscustomobject]@{ Label = "MORE ACTIONS"; Description = "Compare, authorize, export summary, or delete."; Enabled = $true; Value = "More" },
            [pscustomobject]@{ Label = "RETURN"; Description = "Return to Mission Control."; Enabled = $true; Value = "Return" }
        )
        $selection = Show-WzhkKeypadMenu -Title "SAVED PROFILE // ACTION" -Items $items -Context @(
            "SAVED PROFILE: $($profile.displayName)",
            "PATH: $savedPath"
        )
        if ($null -eq $selection -or $selection.Value -eq "Return") { return }
        switch ([string]$selection.Value) {
            "Inspect" { Show-WzhkSavedProfileInspection -SavedProfilePath $savedPath }
            "Render" { $null = Invoke-WzhkSavedProfileRenderWizard -Mode "Production" -SavedProfilePath $savedPath }
            "Preflight" { $null = Invoke-WzhkSavedProfileRenderWizard -Mode "Preflight" -SavedProfilePath $savedPath }
            "DryRun" { $null = Invoke-WzhkSavedProfileRenderWizard -Mode "DryRun" -SavedProfilePath $savedPath }
            "Edit" {
                $result = Invoke-WzhkProfileBuilder `
                    -SceneCandidates @(Get-WzhkApprovedSceneCandidatesForBuilder) `
                    -InitialProfile $profile `
                    -InitialProfilePath $savedPath `
                    -ProfileDirectory $profileRoot
                if ($null -ne $result) {
                    $savedPath = [string]$result.Path
                    Invoke-WzhkProfilePostSave -SaveResult $result
                }
            }
            "Duplicate" {
                $newName = Read-WzhkTextInput -Prompt "Duplicate display name" -Default ($profile.displayName + " Copy") -Required -MaximumLength 80 -AllowCancel
                if ($null -ne $newName) {
                    try {
                        $copy = Copy-WzhkRenderProfile -Profile $profile -NewDisplayName $newName
                        $copyId = ConvertTo-WzhkProfileSlug -Name $newName
                        $copy = Set-WzhkProfileValue -Profile $copy -PropertyPath "profileId" -Value $copyId.ToUpperInvariant()
                        $copy = Set-WzhkProfileValue -Profile $copy -PropertyPath "authorization.profile" -Value $copyId.ToUpperInvariant()
                        $projectDirectory = Join-Path $profileRoot (ConvertTo-WzhkProfileSlug -Name ([string]$copy.project))
                        $copyPath = Join-Path $projectDirectory ((ConvertTo-WzhkProfileSlug -Name $copyId) + ".json")
                        $copySave = Save-WzhkRenderProfile -Profile $copy -Path $copyPath
                        Invoke-WzhkProfilePostSave -SaveResult $copySave
                    }
                    catch { Show-WzhkMessage -Title "PROFILE DUPLICATE FAILED" -Lines @($_.Exception.Message) -Color Red }
                }
            }
            "Rename" {
                $newName = Read-WzhkTextInput -Prompt "New profile display name" -Default $profile.displayName -Required -MaximumLength 80 -AllowCancel
                if ($null -ne $newName) {
                    try {
                        $renamed = Rename-WzhkRenderProfile -Profile $profile -NewDisplayName $newName
                        $renameSave = Save-WzhkRenderProfile -Profile $renamed -Path $savedPath -Force
                        Show-WzhkDoneAnimation -Title "PROFILE RENAMED" -Details @(
                            "Name: $($renameSave.Profile.displayName)",
                            "Authorization must be renewed because the saved-file hash changed."
                        )
                    }
                    catch { Show-WzhkMessage -Title "PROFILE RENAME FAILED" -Lines @($_.Exception.Message) -Color Red }
                }
            }
            "More" {
                if (Invoke-WzhkSavedProfileMoreActions -SavedProfilePath $savedPath) { return }
            }
        }
    }
}

function Invoke-RenderWizard {
    param(
        [ValidateSet("Preflight", "DryRun", "Production")][string]$Mode
    )

    $scenePath = Find-WzhkApprovedScene -PrepPath $script:SelectedPrep
    if ($null -eq $scenePath) {
        Show-WzhkMessage -Title "APPROVED SCENE MISSING" -Lines @(
            "No approved Blender scene was found in:",
            $script:SelectedPrep
        ) -Color Red
        return
    }

    $profile = Select-Profile -PrepPath $script:SelectedPrep
    if ($null -eq $profile) {
        return
    }

    $outputPath = Select-OutputDirectory -Profile $profile
    if ([string]::IsNullOrWhiteSpace($outputPath)) {
        return
    }

    $scene = Get-WzhkSceneInfo -Path $scenePath
    $token = Find-WzhkAuthorizationToken `
        -PrepPath $script:SelectedPrep `
        -ScenePath $scenePath `
        -ProfilePath $profile.Path

    $modeLabel = switch ($Mode) {
        "Preflight" { "PREFLIGHT // BLENDER + STORAGE + PROFILE" }
        "DryRun" { "DRY RUN // RESUME PLAN + MISSING CHUNKS" }
        "Production" { "PRODUCTION // START OR RESUME FINAL RENDER" }
    }

    if ($Mode -eq "Production" -and [string]::IsNullOrWhiteSpace($token)) {
        Show-WzhkMessage -Title "AUTHORIZATION TOKEN NOT FOUND" -Lines @(
            "The selected profile is not authorized for this exact frozen scene.",
            "Scene hash: $($scene.Hash)",
            "Profile hash: $($profile.Hash)",
            "Generate or select the matching authorization package before production rendering."
        ) -Color Red
        return
    }

    $details = Get-ModeDetails `
        -ModeLabel $modeLabel `
        -Scene $scene `
        -Profile $profile `
        -OutputPath $outputPath `
        -AuthorizationToken $token

    $confirmed = Confirm-WzhkMode `
        -ModeLabel $modeLabel `
        -Details $details `
        -Production:($Mode -eq "Production")

    if (-not $confirmed) {
        return
    }

    Write-WzhkScreenHeader -Subtitle "2 // THE FRAME"
    foreach ($line in $details) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color White
    }
    Write-WzhkFrameDivider
    Write-WzhkFrameLine -Text "  LIVE PROCESS OUTPUT" -Color Cyan
    Write-WzhkFrameLine -Text ""

    $result = Invoke-WzhkRenderMode `
        -Mode $Mode `
        -RenderScript $renderScript `
        -ScenePath $scenePath `
        -ProfilePath $profile.Path `
        -OutputDirectory $outputPath `
        -AuthorizationToken $token `
        -WatcherScript $watcherScript `
        -TotalFrames $profile.FrameEnd `
        -AutoWatcher:(($Mode -eq "Production") -and (-not $NoAutoWatcher))

    Write-WzhkFrameDivider
    Write-WzhkFrameLine -Text ("  EXIT CODE : " + $result.ExitCode) -Color $(if ($result.Ok) { "Green" } else { "Red" })
    Write-WzhkFrameLine -Text ("  LOG       : " + $result.LogPath) -Color DarkGray
    Write-WzhkFrameBottom

    if ($result.Ok) {
        Show-WzhkDoneAnimation -Title $modeLabel -Details @(
            "Mode completed successfully.",
            "Output: $outputPath",
            "Log: $($result.LogPath)"
        )
    }
    else {
        Show-WzhkMessage -Title "MISSION INTERRUPTED" -Lines @(
            "The selected mode ended with exit code $($result.ExitCode).",
            "No destructive cleanup was performed.",
            "Review the framed output and log:",
            $result.LogPath
        ) -Color Red
    }
}

function Invoke-WatcherWizard {
    $allOutputs = @(Get-AllWzhkOutputCandidates)
    if ($allOutputs.Count -eq 0) {
        Show-WzhkMessage -Title "NO FINAL OUTPUT" -Lines @(
            "No output directory exists under:",
            $finalOutputRoot
        ) -Color Yellow
        return
    }

    $items = New-Object System.Collections.Generic.List[object]
    foreach ($directory in @($allOutputs | Select-Object -First 9)) {
        $stats = Get-WzhkOutputStats -OutputPath $directory.FullName
        $items.Add([pscustomobject]@{
            Label = $directory.Name
            Description = [string]::Format(
                "{0:N2}% published  •  {1} in-flight  •  latest frame {2}",
                $stats.Percent,
                $stats.Inflight,
                $stats.LatestFrame
            )
            Enabled = $true
            Value = $directory.FullName
        })
    }

    $selection = Show-WzhkKeypadMenu `
        -Title "SELECT OUTPUT TO WATCH" `
        -Items $items.ToArray() `
        -Context @("The dashboard shows the newest frame, two progress bars, ETA, active chunk, and Blender log.")

    if ($null -eq $selection) {
        return
    }

    $outputPath = [string]$selection.Value
    $matchedProfilePath = ""
    $dashboardTotalFrames = 13029
    $dashboardFrameStart = 1
    $dashboardFps = 30.0
    $dashboardRefreshSeconds = 6
    $manifestPath = Join-Path $outputPath "manifests\render-manifest.json"
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        try {
            $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
            $manifestProfileHash = [string]$manifest.renderProfile.sha256
            $matched = @(Get-SavedProfileEntries | Where-Object { $_.FileSha256 -eq $manifestProfileHash } | Select-Object -First 1)
            if ($matched.Count -eq 1) {
                $matchedProfilePath = [string]$matched[0].Path
                $dashboardProfile = Import-WzhkRenderProfile -Path $matchedProfilePath
                $dashboardFrameStart = [int]$dashboardProfile.timeline.frameStart
                $dashboardTotalFrames = [int]$dashboardProfile.timeline.frameEnd - $dashboardFrameStart + 1
                $dashboardFps = [double]$dashboardProfile.timeline.fps
                $dashboardRefreshSeconds = [int]$dashboardProfile.dashboard.refreshSeconds
            }
            elseif ($null -ne $manifest.PSObject.Properties["frameContract"]) {
                $dashboardFrameStart = [int]$manifest.frameContract.frameStart
                $dashboardTotalFrames = [int]$manifest.frameContract.frameCount
                $dashboardFps = [double]$manifest.frameContract.fps
            }
        }
        catch {
            $matchedProfilePath = ""
        }
    }
    $details = @(
        "MODE             : VISUAL PROGRESS DASHBOARD",
        "OUTPUT           : $outputPath",
        "SAVED PROFILE    : $(if ([string]::IsNullOrWhiteSpace($matchedProfilePath)) { 'manifest metadata' } else { $matchedProfilePath })",
        "WATCHER          : $watcherScript",
        "REFRESH          : every $dashboardRefreshSeconds seconds",
        "DISPLAY          : latest frame + rendered/published progress + ETA + logs"
    )

    if (-not (Confirm-WzhkMode -ModeLabel "VISUAL PROGRESS DASHBOARD" -Details $details)) {
        return
    }

    Write-WzhkScreenHeader -Subtitle "2 // THE FRAME"
    foreach ($line in $details) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color White
    }
    Write-WzhkFrameDivider

    try {
        $process = Start-WzhkRenderWatcher `
            -WatcherScript $watcherScript `
            -OutputDirectory $outputPath `
            -TotalFrames $dashboardTotalFrames `
            -RefreshSeconds $dashboardRefreshSeconds `
            -Fps $dashboardFps `
            -FrameStart $dashboardFrameStart `
            -ProfilePath $matchedProfilePath

        Write-WzhkFrameLine -Text ("  Dashboard process started: PID " + $process.Id) -Color Green
        Write-WzhkFrameLine -Text "  A browser window will open with the visual progress frame." -Color Cyan
        Write-WzhkFrameBottom

        Show-WzhkDoneAnimation -Title "DASHBOARD ONLINE" -Details @(
            "Visual progress monitoring is active.",
            "Output: $outputPath"
        )
    }
    catch {
        Write-WzhkFrameLine -Text ("  ERROR: " + $_.Exception.Message) -Color Red
        Write-WzhkFrameBottom
        $null = [Console]::ReadKey($true)
    }
}

function Invoke-OpenOutputWizard {
    $allOutputs = @(Get-AllWzhkOutputCandidates)
    if ($allOutputs.Count -eq 0) {
        Show-WzhkMessage -Title "NO FINAL OUTPUT" -Lines @(
            "No output directory exists under:",
            $finalOutputRoot
        ) -Color Yellow
        return
    }

    $items = New-Object System.Collections.Generic.List[object]
    foreach ($directory in @($allOutputs | Select-Object -First 9)) {
        $stats = Get-WzhkOutputStats -OutputPath $directory.FullName
        $items.Add([pscustomobject]@{
            Label = $directory.Name
            Description = [string]::Format(
                "{0:N2}% published  •  latest frame {1}",
                $stats.Percent,
                $stats.LatestFrame
            )
            Enabled = $true
            Value = $directory.FullName
        })
    }

    $selection = Show-WzhkKeypadMenu -Title "OPEN OUTPUT + LATEST FRAME" -Items $items.ToArray()
    if ($null -eq $selection) {
        return
    }

    $outputPath = [string]$selection.Value
    $details = @(
        "MODE             : OPEN OUTPUT + LATEST FRAME",
        "OUTPUT           : $outputPath",
        "ACTION           : open Explorer and the newest browser-compatible frame"
    )

    if (-not (Confirm-WzhkMode -ModeLabel "OPEN OUTPUT" -Details $details)) {
        return
    }

    Write-WzhkScreenHeader -Subtitle "2 // THE FRAME"
    foreach ($line in $details) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color White
    }
    Write-WzhkFrameDivider

    try {
        Open-WzhkOutput -OutputDirectory $outputPath -OpenLatestFrame
        Write-WzhkFrameLine -Text "  Output folder opened." -Color Green
        Write-WzhkFrameBottom
        Show-WzhkDoneAnimation -Title "OUTPUT ONLINE" -Details @("Opened: $outputPath")
    }
    catch {
        Write-WzhkFrameLine -Text ("  ERROR: " + $_.Exception.Message) -Color Red
        Write-WzhkFrameBottom
        $null = [Console]::ReadKey($true)
    }
}

function Invoke-WzhkCalibrationWizard {
    param(
        [ValidateSet("", "Generate720", "GenerateRecommended")]
        [string]$InitialAction = ""
    )

    $selected = Select-SavedProfile -Title "CALIBRATE THIS PC // SELECT SOURCE PROFILE"
    if ($null -eq $selected) { return }
    $profile = Import-WzhkRenderProfile -Path $selected.Path -VerifyFiles
    $scenePath = [string]$profile.approvedScenePath
    $safety = Get-WzhkRenderSafetyAudit -RepositoryRoot $RepositoryRoot
    $actions = @(
        [pscustomobject]@{ Label = "CALIBRATE THIS PC"; Description = "Create the machine fingerprint, staged matrix, evidence directory, and safety gate."; Enabled = $true; Value = "Plan" },
        [pscustomobject]@{ Label = "RUN CALIBRATION CANDIDATE"; Description = "Render the six required stills plus both 30-frame production ranges when the GPU is idle."; Enabled = [bool]$safety.SafeForGpuCalibration; Value = "Run" },
        [pscustomobject]@{ Label = "REVIEW CALIBRATION CANDIDATE"; Description = "Record PASS, PASS WITH DOCUMENTED CAVEAT, or FAIL after viewing all eight bounded outputs."; Enabled = $true; Value = "Review" },
        [pscustomobject]@{ Label = "GENERATE RECOMMENDED PROFILE"; Description = "Finalize the fastest fully measured candidate passing human quality gates."; Enabled = $true; Value = "Finalize" },
        [pscustomobject]@{ Label = "GENERATE 720P HYPER PROFILE"; Description = "Generated by Finalize from the fastest passing native 1280x720 candidate."; Enabled = $true; Value = "Finalize" },
        [pscustomobject]@{ Label = "GENERATE 1080P RECOMMENDED PROFILE"; Description = "Generated when measured 1080p evidence wins or is selected per resolution."; Enabled = $true; Value = "Finalize" },
        [pscustomobject]@{ Label = "GENERATE 1440P BALANCED PROFILE"; Description = "Generated only from passing bounded 2560x1440 calibration evidence."; Enabled = $true; Value = "Finalize" },
        [pscustomobject]@{ Label = "GENERATE 4K BALANCED PROFILE"; Description = "Generated only when native 3840x2160 passes and remains practical."; Enabled = $true; Value = "Finalize" },
        [pscustomobject]@{ Label = "COMPARE CALIBRATED PROFILES"; Description = "Use LOAD / EDIT SAVED PROFILE and COMPARE after finalization."; Enabled = $true; Value = "Compare" }
    )
    $choice = $null
    if ($InitialAction -eq "Generate720") {
        $choice = [pscustomobject]@{ Label = "GENERATE 720P HYPER PROFILE"; Value = "Finalize" }
    }
    elseif ($InitialAction -eq "GenerateRecommended") {
        $choice = [pscustomobject]@{ Label = "GENERATE RECOMMENDED PROFILE"; Value = "Finalize" }
    }
    else {
        $choice = Show-WzhkKeypadMenu -Title "CALIBRATION-AWARE PROFILE ACTIONS" -Items $actions -Context @(
            "GPU safety: $($safety.Reason)",
            "The approved scene is never modified; candidates bind its exact SHA-256.",
            "Visual review remains mandatory before recommendation."
        )
    }
    if ($null -eq $choice) { return }
    $calibrationScript = Join-Path $RepositoryRoot "tools\calibrate-trackprompt-render.ps1"
    if ($choice.Value -eq "Compare") {
        Show-WzhkMessage -Title "COMPARE CALIBRATED PROFILES" -Lines @("Open LOAD / EDIT SAVED PROFILE, choose a calibrated profile, then choose COMPARE PROFILE.") -Color Cyan
        return
    }
    if ($choice.Value -eq "Plan") {
        $goalChoice = Show-WzhkKeypadMenu -Title "CALIBRATION GOAL" -Items @(
            [pscustomobject]@{ Label = "FASTEST ACCEPTABLE"; Description = "Favor maximum validated frames/hour subject to hard quality gates."; Enabled = $true; Value = "FASTEST ACCEPTABLE" },
            [pscustomobject]@{ Label = "RECOMMENDED BALANCED"; Description = "Balance release quality, recovery, time, and storage."; Enabled = $true; Value = "RECOMMENDED BALANCED" },
            [pscustomobject]@{ Label = "HIGHEST PRACTICAL QUALITY"; Description = "Favor quality while retaining a practical measured runtime."; Enabled = $true; Value = "HIGHEST PRACTICAL QUALITY" },
            [pscustomobject]@{ Label = "CUSTOM"; Description = "Create the full staged evidence matrix for operator-led selection."; Enabled = $true; Value = "CUSTOM" }
        ) -Context @("All goals test native 720p, 1080p, 1440p, and 4K candidates; human review remains mandatory.")
        if ($null -eq $goalChoice) { return }
        $result = @(& $calibrationScript -Mode Plan -Goal ([string]$goalChoice.Value) -ApprovedScenePath $scenePath -BaseProfilePath $selected.Path 2>&1)
        Show-WzhkMessage -Title "CALIBRATION PLAN" -Lines @($result | ForEach-Object { [string]$_ }) -Color $(if ($LASTEXITCODE -eq 0) { "Cyan" } else { "Red" })
        return
    }
    $directory = Read-WzhkTextInput -Prompt "Calibration evidence directory" -Required -AllowCancel
    if ($null -eq $directory) { return }
    if ($choice.Value -eq "Run") {
        $candidateId = Read-WzhkTextInput -Prompt "Candidate ID from calibration.json" -Required -AllowCancel
        if ($null -eq $candidateId) { return }
        $result = @(& $calibrationScript -Mode RunCandidate -ApprovedScenePath $scenePath -BaseProfilePath $selected.Path -CalibrationDirectory $directory -CandidateId $candidateId 2>&1)
    }
    elseif ($choice.Value -eq "Review") {
        $candidateId = Read-WzhkTextInput -Prompt "Candidate ID from calibration.json" -Required -AllowCancel
        if ($null -eq $candidateId) { return }
        $qualityChoice = Show-WzhkKeypadMenu -Title "HARD VISUAL QUALITY GATE" -Items @(
            [pscustomobject]@{ Label = "PASS"; Description = "No critical artistic, temporal, color, or technical regression."; Enabled = $true; Value = "PASS" },
            [pscustomobject]@{ Label = "PASS WITH DOCUMENTED CAVEAT"; Description = "Acceptable with a precise noncritical caveat."; Enabled = $true; Value = "PASS WITH DOCUMENTED CAVEAT" },
            [pscustomobject]@{ Label = "FAIL"; Description = "Reject this candidate from recommendation."; Enabled = $true; Value = "FAIL" }
        ) -Context @("Review hero, orbit rails, stars, glow, gradients, AgX color, composition, and both consecutive sequences.")
        if ($null -eq $qualityChoice) { return }
        $notes = Read-WzhkTextInput -Prompt "Quality notes (required for caveat/fail)" -AllowCancel
        if ($null -eq $notes) { return }
        if ([string]$qualityChoice.Value -ne "PASS" -and [string]::IsNullOrWhiteSpace($notes)) {
            Show-WzhkMessage -Title "QUALITY NOTES REQUIRED" -Lines @("A caveat or failure requires a documented reason.") -Color Yellow
            return
        }
        $result = @(& $calibrationScript -Mode ReviewCandidate -ApprovedScenePath $scenePath -BaseProfilePath $selected.Path -CalibrationDirectory $directory -CandidateId $candidateId -QualityResult ([string]$qualityChoice.Value) -QualityNotes $notes 2>&1)
    }
    else {
        $result = @(& $calibrationScript -Mode Finalize -ApprovedScenePath $scenePath -BaseProfilePath $selected.Path -CalibrationDirectory $directory 2>&1)
    }
    Show-WzhkMessage -Title "CALIBRATION RESULT" -Lines @($result | ForEach-Object { [string]$_ }) -Color $(if ($LASTEXITCODE -eq 0) { "Green" } else { "Red" })
}

function Invoke-WzhkStopRequestWizard {
    param([switch]$Cancel)
    $outputs = @(Get-AllWzhkOutputCandidates)
    if ($outputs.Count -eq 0) { Show-WzhkMessage -Title "NO OUTPUT" -Lines @("No initialized render output is available.") -Color Yellow; return }
    $items = @($outputs | Select-Object -First 12 | ForEach-Object { [pscustomobject]@{ Label = $_.Name; Description = $_.FullName; Enabled = $true; Value = $_.FullName } })
    $selection = Show-WzhkKeypadMenu -Title $(if ($Cancel) { "CANCEL STOP REQUEST" } else { "REQUEST STOP AFTER CURRENT CHUNK" }) -Items $items -Context @("No frame, chunk, mutex, or render process is removed by this action.")
    if ($null -eq $selection) { return }
    $output = [string]$selection.Value
    if ($Cancel) {
        if (-not (Confirm-WzhkTwoStage -Title "CANCEL STOP REQUEST" -Details @("OUTPUT: $output") -FirstPrompt "Cancel only the stop marker?" -FirstYesText "REVIEW CANCELLATION" -SecondPrompt "Final confirmation: allow future chunks after resume?" -SecondYesText "CANCEL STOP REQUEST")) { return }
        $result = Cancel-WzhkStopAfterCurrentChunk -OutputDirectory $output -OperatorConfirmed
        Show-WzhkMessage -Title "STOP REQUEST CANCELLED" -Lines @($result.Path, "No frame was changed.") -Color Green
        return
    }
    $manifest = Get-Content -LiteralPath (Join-Path $output "manifests\render-manifest.json") -Raw | ConvertFrom-Json
    $matching = @(Get-SavedProfileEntries | Where-Object { $_.FileSha256 -eq [string]$manifest.renderProfile.sha256 } | Select-Object -First 1)
    if ($matching.Count -ne 1) { Show-WzhkMessage -Title "PROFILE NOT FOUND" -Lines @("The exact saved profile for this output is unavailable; no marker was written.") -Color Red; return }
    $profile = Import-WzhkRenderProfile -Path $matching[0].Path -VerifyFiles
    if (-not (Confirm-WzhkTwoStage -Title "STOP AFTER CURRENT CHUNK" -Details @("OUTPUT: $output", "PROFILE SHA: $($matching[0].FileSha256)") -FirstPrompt "Request a safe chunk-boundary stop?" -FirstYesText "REVIEW STOP" -SecondPrompt "Write the local stop marker now?" -SecondYesText "REQUEST STOP")) { return }
    $request = Request-WzhkStopAfterCurrentChunk -OutputDirectory $output -ProfilePath $matching[0].Path -ScenePath ([string]$profile.approvedScenePath)
    Show-WzhkMessage -Title "STOP REQUESTED" -Lines @($request.Path, "The renderer will validate and publish the current chunk, then exit before another chunk.") -Color Yellow
}

function Invoke-WzhkPerformanceWizard {
    $telemetry = Get-WzhkNvidiaTelemetry
    $powerSource = Get-WzhkPowerSource
    $competingGpu = @(Get-WzhkCompetingGpuProcesses)
    $statePath = Join-Path $RepositoryRoot "test-output\performance\exclusive-performance-state.json"
    $items = @(
        [pscustomobject]@{ Label = "ENABLE EXCLUSIVE PERFORMANCE MODE"; Description = "Record the current power plan, optionally select High Performance, and set one Blender process to High priority."; Enabled = $true; Value = "Start" },
        [pscustomobject]@{ Label = "RESTORE PREVIOUS PERFORMANCE STATE"; Description = "Restore the recorded Windows power plan after rendering."; Enabled = (Test-Path -LiteralPath $statePath -PathType Leaf); Value = "Stop" }
    )
    $choice = Show-WzhkKeypadMenu -Title "EXCLUSIVE PERFORMANCE MODE" -Items $items -Context @(
        "GPU: $(if ($telemetry.Available) { $telemetry.GpuModel + ' / ' + $telemetry.TemperatureC + ' C' } else { 'telemetry unavailable' })",
        "POWER: $(if ($powerSource.Available) { $powerSource.PowerLineStatus } else { 'unavailable' }) / competing GPU processes: $($competingGpu.Count)",
        "Realtime priority is forbidden. Unrelated apps and services are never terminated automatically."
    )
    if ($null -eq $choice) { return }
    if ($choice.Value -eq "Stop") {
        $restored = Stop-WzhkExclusivePerformanceMode -StatePath $statePath
        Show-WzhkMessage -Title "PERFORMANCE STATE RESTORED" -Lines @("Power plan restored: $($restored.previousPowerPlanGuid)") -Color Green
        return
    }
    $blender = @(Get-Process -Name blender -ErrorAction SilentlyContinue)
    $pid = if ($blender.Count -eq 1) { [int]$blender[0].Id } else { 0 }
    if (-not (Confirm-WzhkTwoStage -Title "EXCLUSIVE PERFORMANCE MODE" -Details @("Blender PID: $(if ($pid -gt 0) { $pid } else { 'none/ambiguous' })", "State: $statePath", "AC POWER: $($powerSource.PowerLineStatus)", "GPU PROCESSES: $($competingGpu.Count) detected; none will be terminated") -FirstPrompt "Review reversible system tuning?" -FirstYesText "REVIEW" -SecondPrompt "Enable High Performance, sleep inhibition, and safe Blender priority now?" -SecondYesText "ENABLE")) { return }
    $state = Start-WzhkExclusivePerformanceMode -StatePath $statePath -OperatorConfirmed -UseHighPerformancePowerPlan -BlenderProcessId $pid
    Show-WzhkMessage -Title "PERFORMANCE MODE ENABLED" -Lines @("Previous power plan: $($state.previousPowerPlanGuid)", "Restore state: $statePath") -Color Yellow
}

function Invoke-WzhkOutsourceWizard {
    param(
        [ValidateSet("", "Create", "Validate", "Plan", "Estimate", "Import")]
        [string]$InitialAction = ""
    )

    $selected = Select-SavedProfile -Title "OUTSOURCE / REMOTE RENDER // SELECT PROFILE"
    if ($null -eq $selected) { return }
    $profile = Import-WzhkRenderProfile -Path $selected.Path -VerifyFiles
    $items = @(
        [pscustomobject]@{ Label = "CREATE REMOTE RENDER PACKAGE"; Description = "Sanitize the scene, remove audio, build checksums, and run a local clean-environment smoke. No upload."; Enabled = $true; Value = "Create" },
        [pscustomobject]@{ Label = "VALIDATE REMOTE PACKAGE"; Description = "Verify package, scene, profile, and per-file hashes."; Enabled = $true; Value = "Validate" },
        [pscustomobject]@{ Label = "GENERATE CHUNK DISTRIBUTION"; Description = "Produce non-overlapping local/remote assignments."; Enabled = $true; Value = "Plan" },
        [pscustomobject]@{ Label = "ESTIMATE OUTSOURCE TIME / COST"; Description = "Use provider-entered performance and rates; no unsupported performance claim."; Enabled = $true; Value = "Estimate" },
        [pscustomobject]@{ Label = "IMPORT / VERIFY / MERGE RETURNED FRAMES"; Description = "Quarantine, verify, atomically publish only missing valid frames, and preserve duplicates for QA."; Enabled = $true; Value = "Import" }
    )
    $choice = if ([string]::IsNullOrWhiteSpace($InitialAction)) {
        Show-WzhkKeypadMenu -Title "OUTSOURCE / REMOTE RENDER" -Items $items -Context @(
            "Private source audio, lyrics, transcripts, cues, prompts, local paths, and credentials are excluded by default.",
            "The provider may gain access to scene design, materials, geometry, and included assets.",
            "No network upload or purchase occurs here."
        )
    }
    else { [pscustomobject]@{ Value = $InitialAction } }
    if ($null -eq $choice) { return }
    if ($choice.Value -eq "Create") {
        if (-not (Confirm-WzhkTwoStage -Title "REMOTE PACKAGE PRIVACY" -Details @("PROFILE: $($selected.Path)", "SCENE: $($profile.approvedScenePath)") -Warnings @("The provider can inspect the packaged scene.", "Private audio remains local and is muxed only after returned frames validate.") -FirstPrompt "Acknowledge the privacy boundary?" -FirstYesText "ACKNOWLEDGE" -SecondPrompt "Create an audio-free local package now?" -SecondYesText "CREATE PACKAGE")) { return }
        $result = @(& (Join-Path $RepositoryRoot "tools\export-trackprompt-render-package.ps1") -ApprovedScenePath ([string]$profile.approvedScenePath) -RenderProfilePath $selected.Path -PrivacyConfirmed -AcknowledgeSceneDisclosure 2>&1)
        Show-WzhkMessage -Title "REMOTE PACKAGE RESULT" -Lines @($result | ForEach-Object { [string]$_ }) -Color $(if ($LASTEXITCODE -eq 0) { "Green" } else { "Red" })
        return
    }
    if ($choice.Value -eq "Validate") {
        $package = Read-WzhkTextInput -Prompt "Remote package directory" -Required -AllowCancel
        if ($null -eq $package) { return }
        $result = @(& (Join-Path $RepositoryRoot "tools\validate-trackprompt-render-package.ps1") -PackageDirectory $package 2>&1)
        Show-WzhkMessage -Title "REMOTE PACKAGE VALIDATION" -Lines @($result | ForEach-Object { [string]$_ }) -Color $(if ($LASTEXITCODE -eq 0) { "Green" } else { "Red" })
        return
    }
    if ($choice.Value -eq "Plan") {
        $package = Read-WzhkTextInput -Prompt "Validated remote package directory" -Required -AllowCancel
        if ($null -eq $package) { return }
        $manifestPath = Join-Path ([IO.Path]::GetFullPath($package)) "package-manifest.json"
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            Show-WzhkMessage -Title "PACKAGE MANIFEST MISSING" -Lines @($manifestPath, "No distribution was generated.") -Color Red
            return
        }
        $workerText = Read-WzhkTextInput -Prompt "Number of remote workers (1-64)" -Required -AllowCancel
        if ($null -eq $workerText) { return }
        $chunkText = Read-WzhkTextInput -Prompt "Frames per chunk (1-1200)" -Required -AllowCancel
        if ($null -eq $chunkText) { return }
        [int]$workerCount = 0
        [int]$framesPerChunk = 0
        if (-not [int]::TryParse($workerText, [ref]$workerCount) -or $workerCount -lt 1 -or $workerCount -gt 64 -or -not [int]::TryParse($chunkText, [ref]$framesPerChunk) -or $framesPerChunk -lt 1 -or $framesPerChunk -gt 1200) {
            Show-WzhkMessage -Title "INVALID DISTRIBUTION SETTINGS" -Lines @("Remote workers must be 1-64 and frames per chunk must be 1-1200.") -Color Red
            return
        }
        $localChoice = Show-WzhkKeypadMenu -Title "HYBRID LOCAL PARTICIPATION" -Items @(
            [pscustomobject]@{ Label = "INCLUDE ONE LOCAL WORKER"; Description = "Assign disjoint ranges to this PC; no local render starts automatically."; Enabled = $true; Value = $true },
            [pscustomobject]@{ Label = "REMOTE WORKERS ONLY"; Description = "Assign every range to remote workers."; Enabled = $true; Value = $false }
        ) -Context @("Assignments never overlap. This action only writes a plan.")
        if ($null -eq $localChoice) { return }
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $assignments = @(New-WzhkRemoteChunkDistribution -FrameStart ([int]$manifest.frameContract.frameStart) -FrameEnd ([int]$manifest.frameContract.frameEnd) -FramesPerChunk $framesPerChunk -RemoteWorkers $workerCount -IncludeLocalWorker:([bool]$localChoice.Value) -SceneSha256 ([string]$manifest.scene.sha256) -ProfileSha256 ([string]$manifest.profile.sha256) -OutputFormat ([string]$manifest.frameContract.format))
        $distribution = [pscustomobject][ordered]@{
            schemaVersion = "1.0.0"
            kind = "trackprompt-remote-chunk-distribution"
            generatedAt = (Get-Date).ToUniversalTime().ToString("o")
            packageId = [string]$manifest.packageId
            packageSha256 = [string]$manifest.packageSha256
            localWorkerIncluded = [bool]$localChoice.Value
            assignments = $assignments
        }
        $distributionPath = Join-Path ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($package))) ("chunk-distribution-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + ".json")
        $null = Save-WzhkCalibrationJson -Calibration $distribution -Path $distributionPath
        Show-WzhkMessage -Title "CHUNK DISTRIBUTION READY" -Lines @("Assignments: $($assignments.Count)", "Path: $distributionPath", "No render or network action was started.") -Color Green
        return
    }
    if ($choice.Value -eq "Estimate") {
        $secondsText = Read-WzhkTextInput -Prompt "Provider benchmark seconds per frame" -Required -AllowCancel
        if ($null -eq $secondsText) { return }
        $workersText = Read-WzhkTextInput -Prompt "Parallel provider workers (1-256)" -Required -AllowCancel
        if ($null -eq $workersText) { return }
        $hourlyText = Read-WzhkTextInput -Prompt "Provider hourly rate (0 if unused)" -Required -AllowCancel
        if ($null -eq $hourlyText) { return }
        $perFrameText = Read-WzhkTextInput -Prompt "Provider per-frame price (0 if unused)" -Required -AllowCancel
        if ($null -eq $perFrameText) { return }
        [double]$secondsPerFrame = 0
        [double]$hourlyRate = 0
        [double]$perFramePrice = 0
        [int]$workers = 0
        if (-not [double]::TryParse($secondsText, [ref]$secondsPerFrame) -or $secondsPerFrame -le 0 -or -not [int]::TryParse($workersText, [ref]$workers) -or $workers -lt 1 -or $workers -gt 256 -or -not [double]::TryParse($hourlyText, [ref]$hourlyRate) -or $hourlyRate -lt 0 -or -not [double]::TryParse($perFrameText, [ref]$perFramePrice) -or $perFramePrice -lt 0) {
            Show-WzhkMessage -Title "INVALID ESTIMATE SETTINGS" -Lines @("Use positive seconds/frame and workers, with nonnegative price values.") -Color Red
            return
        }
        $frameCount = [int]$profile.timeline.frameCount
        $estimate = Get-WzhkOutsourceEstimate -SecondsPerFrame $secondsPerFrame -FrameCount $frameCount -Workers $workers -HourlyRate $hourlyRate -PerFramePrice $perFramePrice
        $localHours = if ($null -ne $profile.PSObject.Properties["calibration"]) { [string]$profile.calibration.expectedTotalHours } else { "not calibrated" }
        Show-WzhkMessage -Title "OUTSOURCE TIME / COST ESTIMATE" -Lines @(
            "Provider GPU-hours: $($estimate.TotalGpuHours)",
            "Provider wall hours: $($estimate.ExpectedWallHours) expected / $($estimate.ConservativeWallHours) conservative",
            "Cost: $($estimate.ExpectedCost) expected / $($estimate.ConservativeCost) conservative",
            "Local calibrated hours: $localHours",
            "Confidence: $($estimate.Confidence)",
            $estimate.Note,
            "No purchase or network action was taken."
        ) -Color Cyan
        return
    }
    if ($choice.Value -eq "Import") {
        $package = Read-WzhkTextInput -Prompt "Validated remote package directory" -Required -AllowCancel
        if ($null -eq $package) { return }
        $returned = Read-WzhkTextInput -Prompt "Worker return directory" -Required -AllowCancel
        if ($null -eq $returned) { return }
        $output = Read-WzhkTextInput -Prompt "Compatible managed output directory" -Required -AllowCancel
        if ($null -eq $output) { return }
        if (-not (Confirm-WzhkTwoStage -Title "IMPORT REMOTE FRAMES" -Details @("PACKAGE: $package", "RETURN: $returned", "OUTPUT: $output", "LOCAL PROFILE: $($selected.Path)") -Warnings @("Returned data enters quarantine before validation.", "An existing valid local frame is never overwritten.") -FirstPrompt "Review the package and returned-frame identities?" -FirstYesText "REVIEW IMPORT" -SecondPrompt "Validate and atomically publish only missing valid frames?" -SecondYesText "IMPORT VALID FRAMES")) { return }
        $result = @(& (Join-Path $RepositoryRoot "tools\import-trackprompt-remote-frames.ps1") -ReturnDirectory $returned -PackageDirectory $package -LocalProfilePath $selected.Path -LocalScenePath ([string]$profile.approvedScenePath) -OutputDirectory $output -OperatorConfirmed 2>&1)
        Show-WzhkMessage -Title "REMOTE FRAME IMPORT" -Lines @($result | ForEach-Object { [string]$_ }) -Color $(if ($LASTEXITCODE -eq 0) { "Green" } else { "Red" })
        return
    }
}

function Invoke-WzhkProfileMenu {
    $choice = Show-WzhkKeypadMenu -Title "CREATE / EDIT PROFILE" -Items @(
        [pscustomobject]@{ Label = "CREATE NEW RENDER PROFILE"; Description = "Build a normalized exact render contract from a reviewed template."; Enabled = $true; Value = "Create" },
        [pscustomobject]@{ Label = "LOAD / EDIT SAVED PROFILE"; Description = "Inspect, edit, duplicate, compare, authorize, or manage a saved profile."; Enabled = (@(Get-SavedProfileEntries).Count -gt 0); Value = "Edit" }
    ) -Context @("Saved profile JSON is the production source of truth. Any saved-file change invalidates its old authorization.")
    if ($null -eq $choice) { return }
    if ($choice.Value -eq "Create") { Invoke-WzhkCreateProfileWizard }
    else { Invoke-WzhkSavedProfileManager }
}

function Invoke-WzhkLocalRenderMenu {
    $outputsAvailable = (@(Get-AllWzhkOutputCandidates).Count -gt 0)
    $choice = Show-WzhkKeypadMenu -Title "LOCAL RENDER" -Items @(
        [pscustomobject]@{ Label = "START / RESUME RENDER"; Description = "Require exact saved-profile authorization and both production confirmations."; Enabled = $true; Value = "Render" },
        [pscustomobject]@{ Label = "FINAL RENDER PREFLIGHT"; Description = "Validate scene, profile, Blender, storage, and output without rendering."; Enabled = $true; Value = "Preflight" },
        [pscustomobject]@{ Label = "DRY-RUN / RESUME PLAN"; Description = "Show exact missing chunks and compatible output without rendering."; Enabled = $true; Value = "DryRun" },
        [pscustomobject]@{ Label = "EXCLUSIVE PERFORMANCE MODE"; Description = "Apply only reversible, explicitly confirmed local tuning."; Enabled = $true; Value = "Performance" },
        [pscustomobject]@{ Label = "REQUEST STOP AFTER CURRENT CHUNK"; Description = "Finish, validate, and publish the current chunk, then stop."; Enabled = $outputsAvailable; Value = "RequestStop" },
        [pscustomobject]@{ Label = "CANCEL STOP REQUEST"; Description = "Remove only the operator stop marker after two confirmations."; Enabled = $outputsAvailable; Value = "CancelStop" },
        [pscustomobject]@{ Label = "VISUAL PROGRESS DASHBOARD"; Description = "Inspect published progress, timing, storage, and latest frame."; Enabled = $outputsAvailable; Value = "Watch" },
        [pscustomobject]@{ Label = "OPEN OUTPUT + LATEST FRAME"; Description = "Open an existing managed output without changing it."; Enabled = $outputsAvailable; Value = "Open" }
    ) -Context @("One local Blender GPU renderer is the default. Profile and scene hashes govern resume compatibility.")
    if ($null -eq $choice) { return }
    switch ([string]$choice.Value) {
        "Render" { $null = Invoke-WzhkSavedProfileRenderWizard -Mode "Production" }
        "Preflight" { $null = Invoke-WzhkSavedProfileRenderWizard -Mode "Preflight" }
        "DryRun" { $null = Invoke-WzhkSavedProfileRenderWizard -Mode "DryRun" }
        "Performance" { Invoke-WzhkPerformanceWizard }
        "RequestStop" { Invoke-WzhkStopRequestWizard }
        "CancelStop" { Invoke-WzhkStopRequestWizard -Cancel }
        "Watch" { Invoke-WatcherWizard }
        "Open" { Invoke-OpenOutputWizard }
    }
}

function Invoke-WzhkLocalOperationsMenu {
    $outputsAvailable = (@(Get-AllWzhkOutputCandidates).Count -gt 0)
    $choice = Show-WzhkKeypadMenu -Title "LOCAL OPERATIONS / SAFETY" -Items @(
        [pscustomobject]@{ Label = "EXCLUSIVE PERFORMANCE MODE"; Description = "Enable or restore reversible local performance state."; Enabled = $true; Value = "Performance" },
        [pscustomobject]@{ Label = "REQUEST STOP AFTER CURRENT CHUNK"; Description = "Write an identity-bound safe-stop marker."; Enabled = $outputsAvailable; Value = "RequestStop" },
        [pscustomobject]@{ Label = "CANCEL STOP REQUEST"; Description = "Cancel only a previously written safe-stop marker."; Enabled = $outputsAvailable; Value = "CancelStop" },
        [pscustomobject]@{ Label = "VISUAL PROGRESS DASHBOARD"; Description = "Open the local render dashboard."; Enabled = $outputsAvailable; Value = "Watch" },
        [pscustomobject]@{ Label = "OPEN OUTPUT + LATEST FRAME"; Description = "Open one managed output and its latest published frame."; Enabled = $outputsAvailable; Value = "Open" },
        [pscustomobject]@{ Label = "CHANGE PREPARATION PACKAGE"; Description = "Choose another frozen approved-scene package for profile creation."; Enabled = $true; Value = "ChangePrep" }
    )
    if ($null -eq $choice) { return }
    switch ([string]$choice.Value) {
        "Performance" { Invoke-WzhkPerformanceWizard }
        "RequestStop" { Invoke-WzhkStopRequestWizard }
        "CancelStop" { Invoke-WzhkStopRequestWizard -Cancel }
        "Watch" { Invoke-WatcherWizard }
        "Open" { Invoke-OpenOutputWizard }
        "ChangePrep" {
            $selection = Select-PrepPackage -Current $script:SelectedPrep
            if ($null -ne $selection) { $script:SelectedPrep = [string]$selection.Value }
        }
    }
}

function ConvertFrom-WzhkCapturedJson {
    param([object[]]$CapturedOutput)

    for ($index = $CapturedOutput.Count - 1; $index -ge 0; $index -= 1) {
        $text = [string]$CapturedOutput[$index]
        if ([string]::IsNullOrWhiteSpace($text)) { continue }
        try { return ($text | ConvertFrom-Json -ErrorAction Stop) }
        catch { continue }
    }
    return $null
}

function Test-WzhkPathWithinDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Directory
    )

    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $fullDirectory = [IO.Path]::GetFullPath($Directory).TrimEnd('\')
    if ($fullPath.Equals($fullDirectory, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $fullPath.StartsWith(($fullDirectory + '\'), [StringComparison]::OrdinalIgnoreCase)
}

function Test-WzhkPathsOverlap {
    param(
        [Parameter(Mandatory = $true)][string]$First,
        [Parameter(Mandatory = $true)][string]$Second
    )

    return (
        (Test-WzhkPathWithinDirectory -Path $First -Directory $Second) -or
        (Test-WzhkPathWithinDirectory -Path $Second -Directory $First)
    )
}

function Get-WzhkReturnedChunkFrameRange {
    param([Parameter(Mandatory = $true)][string]$ManifestPath)

    $manifestFile = Get-Item -LiteralPath $ManifestPath -ErrorAction Stop
    if ($manifestFile.Length -gt 16MB) { throw "Returned chunk manifest exceeds the 16 MiB planning limit." }
    $payload = Get-Content -LiteralPath $manifestFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    if ([string]$payload.kind -ne "trackprompt-cloud-chunk-output") { throw "Returned manifest kind is not a cloud chunk output." }
    $framesProperty = $payload.PSObject.Properties["frames"]
    if ($null -eq $framesProperty -or $null -eq $framesProperty.Value) { throw "Returned manifest has no frame records." }
    $numbers = New-Object System.Collections.Generic.List[int]
    foreach ($record in @($framesProperty.Value)) {
        $frameProperty = $record.PSObject.Properties["frame"]
        $frameNumber = 0
        if ($null -eq $frameProperty -or -not [int]::TryParse(
            [string]$frameProperty.Value,
            [Globalization.NumberStyles]::Integer,
            [Globalization.CultureInfo]::InvariantCulture,
            [ref]$frameNumber
        ) -or $frameNumber -lt 1) {
            throw "Returned manifest contains an invalid frame number."
        }
        $numbers.Add($frameNumber)
    }
    if ($numbers.Count -eq 0) { throw "Returned manifest has no frame records." }
    $ordered = @($numbers.ToArray() | Sort-Object -Unique)
    if ($ordered.Count -ne $numbers.Count) { throw "Returned manifest contains duplicate frame numbers." }
    $start = [int]$ordered[0]
    $end = [int]$ordered[$ordered.Count - 1]
    if (($end - $start + 1) -ne $ordered.Count) { throw "Returned chunk frame records are not contiguous." }
    return [pscustomobject][ordered]@{ Start = $start; End = $end; Count = $ordered.Count }
}

function Invoke-WzhkCloudManifestValidationWizard {
    param([string]$ManifestPath = "")

    if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
        $ManifestPath = Read-WzhkTextInput -Prompt "Sealed cloud package manifest path" -Required -MaximumLength 1000 -AllowCancel
        if ($null -eq $ManifestPath) { return $null }
    }
    $ManifestPath = [IO.Path]::GetFullPath($ManifestPath)
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        Show-WzhkMessage -Title "CLOUD MANIFEST MISSING" -Lines @($ManifestPath, "No state was changed.") -Color Red
        return $null
    }
    try {
        $result = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "validate-manifest" -Arguments @("--path", $ManifestPath)
        if (-not $result.Ok) {
            $message = if ($null -ne $result.Error) { [string]$result.Error.message } else { "Cloud manifest validation failed closed." }
            Show-WzhkMessage -Title "CLOUD MANIFEST INVALID" -Lines @($message, "No state was changed.") -Color Red
            return $null
        }
        $identities = $result.Data.identities
        $frameRange = $result.Data.frameRange
        Show-WzhkMessage -Title "CLOUD MANIFEST VALID" -Lines @(
            "PATH: $ManifestPath",
            "KIND: $($result.Data.kind)",
            "SCENE SHA: $($identities.scene_sha256)",
            "PROFILE SHA: $($identities.profile_sha256)",
            "PACKAGE SHA: $($identities.package_sha256)",
            "FRAMES: $($frameRange.start)-$($frameRange.end)",
            "Validation was offline and changed no state."
        ) -Color Green
        return [pscustomobject]@{ Path = $ManifestPath; Result = $result }
    }
    catch {
        Show-WzhkMessage -Title "CLOUD MANIFEST INVALID" -Lines @($_.Exception.Message, "No state was changed.") -Color Red
        return $null
    }
}

function Invoke-WzhkCloudPackageWizard {
    $mode = Show-WzhkKeypadMenu -Title "SANITIZED CLOUD PACKAGE" -Items @(
        [pscustomobject]@{ Label = "CREATE NEW SANITIZED PACKAGE + CLOUD MANIFEST"; Description = "Run the existing Blender sanitizer and clean smoke, then validate and bridge it into the sealed cloud protocol."; Enabled = (@(Get-SavedProfileEntries).Count -gt 0); Value = "Create" },
        [pscustomobject]@{ Label = "BRIDGE EXISTING VALIDATED SANITIZED PACKAGE"; Description = "Validate an existing audio-free remote package and write a separate sealed cloud manifest; no upload."; Enabled = $true; Value = "Bridge" }
    ) -Context @(
        "This is an offline local preparation action.",
        "It never contacts Brev, uploads a package, provisions a VM, or authorizes a render."
    )
    if ($null -eq $mode) { return }

    $remotePackage = ""
    $localSanitizerSmoke = "not run; an existing validated package was bridged"
    if ($mode.Value -eq "Create") {
        $selected = Select-SavedProfile -Title "CLOUD PACKAGE // SELECT EXACT SAVED PROFILE"
        if ($null -eq $selected) { return }
        $profile = Import-WzhkRenderProfile -Path $selected.Path -VerifyFiles
        if (-not (Confirm-WzhkTwoStage -Title "CLOUD PACKAGE PRIVACY" -Details @(
            "PROFILE: $($selected.Path)",
            "SCENE: $($profile.approvedScenePath)"
        ) -Warnings @(
            "The sanitized scene can disclose visual design, materials, geometry, textures, and included assets.",
            "The bridge validates the audio-free package protocol; it does not prove visual equivalence to the approved original.",
            "Private audio remains local. No upload or provider command is run."
        ) -FirstPrompt "Acknowledge the scene-disclosure boundary?" -FirstYesText "ACKNOWLEDGE" -SecondPrompt "Create and validate the local sanitized package now?" -SecondYesText "CREATE LOCAL PACKAGE")) { return }
        try {
            $captured = @(& (Join-Path $RepositoryRoot "tools\export-trackprompt-render-package.ps1") -ApprovedScenePath ([string]$profile.approvedScenePath) -RenderProfilePath $selected.Path -PrivacyConfirmed -AcknowledgeSceneDisclosure 2>&1)
            $exportPayload = ConvertFrom-WzhkCapturedJson -CapturedOutput $captured
            if ($null -eq $exportPayload -or [string]::IsNullOrWhiteSpace([string]$exportPayload.Package)) {
                Show-WzhkMessage -Title "SANITIZED PACKAGE FAILED" -Lines @($captured | ForEach-Object { [string]$_ }) -Color Red
                return
            }
            $remotePackage = [IO.Path]::GetFullPath([string]$exportPayload.Package)
            $localSanitizerSmoke = "completed by the exporter as a bounded local headless Blender smoke"
        }
        catch {
            Show-WzhkMessage -Title "SANITIZED PACKAGE FAILED" -Lines @($_.Exception.Message, "No cloud manifest or upload was created.") -Color Red
            return
        }
    }
    else {
        $remotePackage = Read-WzhkTextInput -Prompt "Existing validated sanitized package directory" -Required -MaximumLength 1000 -AllowCancel
        if ($null -eq $remotePackage) { return }
        $remotePackage = [IO.Path]::GetFullPath($remotePackage)
        if (-not (Test-Path -LiteralPath $remotePackage -PathType Container)) {
            Show-WzhkMessage -Title "SANITIZED PACKAGE MISSING" -Lines @($remotePackage, "No state was changed.") -Color Red
            return
        }
        if (-not (Confirm-WzhkTwoStage -Title "CLOUD PACKAGE BRIDGE" -Details @("REMOTE PACKAGE: $remotePackage") -Warnings @(
            "The bridge revalidates all legacy package hashes and privacy exclusions.",
            "It writes only a local cloud manifest and performs no upload or provider action."
        ) -FirstPrompt "Review the existing sanitized package?" -FirstYesText "REVIEW PACKAGE" -SecondPrompt "Write a sealed cloud manifest beside it?" -SecondYesText "WRITE CLOUD MANIFEST")) { return }
    }

    $defaultManifest = Join-Path ([IO.Path]::GetDirectoryName($remotePackage.TrimEnd('\'))) "cloud-package.manifest.json"
    $cloudManifest = Read-WzhkTextInput -Prompt "Cloud manifest output path" -Default $defaultManifest -Required -MaximumLength 1000 -AllowCancel
    if ($null -eq $cloudManifest) { return }
    $cloudManifest = [IO.Path]::GetFullPath($cloudManifest)
    if (Test-WzhkPathWithinDirectory -Path $cloudManifest -Directory $remotePackage) {
        Show-WzhkMessage -Title "INVALID CLOUD MANIFEST LOCATION" -Lines @(
            "REMOTE PACKAGE: $remotePackage",
            "REQUESTED MANIFEST: $cloudManifest",
            "The cloud manifest must be outside the immutable remote package tree. No state was changed."
        ) -Color Red
        return
    }
    if (Test-Path -LiteralPath $cloudManifest) {
        Show-WzhkMessage -Title "CLOUD MANIFEST EXISTS" -Lines @($cloudManifest, "Existing manifests are never overwritten. Choose a new path.") -Color Red
        return
    }
    try {
        $result = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "prepare-manifest" -Arguments @(
            "--remote-package", $remotePackage,
            "--output", $cloudManifest
        ) -AllowLocalMutation
        if (-not $result.Ok) {
            $message = if ($null -ne $result.Error) { [string]$result.Error.message } else { "Cloud package bridge failed closed." }
            Show-WzhkMessage -Title "CLOUD PACKAGE BRIDGE FAILED" -Lines @($message, "No upload or provider command was run.") -Color Red
            return
        }
        Show-WzhkMessage -Title "SEALED CLOUD MANIFEST READY" -Lines @(
            "REMOTE PACKAGE: $remotePackage",
            "CLOUD MANIFEST: $($result.Data.output)",
            "MANIFEST SHA: $($result.Data.manifestSha256)",
            "SCENE SHA: $($result.Data.identities.scene_sha256)",
            "PROFILE SHA: $($result.Data.identities.profile_sha256)",
            "PACKAGE SHA: $($result.Data.identities.package_sha256)",
            "FRAMES: $($result.Data.frameRange.start)-$($result.Data.frameRange.end)",
            "SOURCE PACKAGE VALIDATED: $($result.Data.sourcePackageValidated)",
            "LOCAL SANITIZER SMOKE: $localSanitizerSmoke.",
            "OFFLINE ONLY - no upload, network, provider, production render, or billable action occurred."
        ) -Color Green
    }
    catch { Show-WzhkMessage -Title "CLOUD PACKAGE BRIDGE FAILED" -Lines @($_.Exception.Message, "No upload or provider command was run.") -Color Red }
}

function Invoke-WzhkCloudSchedulerInitWizard {
    $validated = Invoke-WzhkCloudManifestValidationWizard
    if ($null -eq $validated) { return }
    $defaultDatabase = Join-Path ([IO.Path]::GetDirectoryName($validated.Path)) "cloud-scheduler.sqlite3"
    $database = Read-WzhkTextInput -Prompt "Offline scheduler SQLite path" -Default $defaultDatabase -Required -MaximumLength 1000 -AllowCancel
    if ($null -eq $database) { return }
    $database = [IO.Path]::GetFullPath($database)
    $jobId = Read-WzhkTextInput -Prompt "New scheduler job ID" -Default ("offline-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")) -Required -MaximumLength 128 -Pattern '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' -ValidationMessage "Use 1-128 letters, digits, dots, underscores, or hyphens." -AllowCancel
    if ($null -eq $jobId) { return }
    $framesPerChunk = Read-WzhkIntegerInput -Prompt "Frames per dynamic chunk" -Default 150 -Minimum 1 -Maximum 1200 -AllowCancel
    if ($null -eq $framesPerChunk) { return }
    $maxAttempts = Read-WzhkIntegerInput -Prompt "Maximum attempts per chunk" -Default 3 -Minimum 1 -Maximum 20 -AllowCancel
    if ($null -eq $maxAttempts) { return }
    if (-not (Confirm-WzhkTwoStage -Title "INITIALIZE OFFLINE CLOUD SCHEDULER" -Details @(
        "MANIFEST: $($validated.Path)",
        "DATABASE: $database",
        "JOB ID: $jobId",
        "FRAMES PER CHUNK: $framesPerChunk",
        "MAX ATTEMPTS: $maxAttempts"
    ) -Warnings @(
        "This creates or updates a local SQLite database and inserts one hash-bound job.",
        "It does not contact Brev, start a worker, render a frame, or create a billable resource."
    ) -FirstPrompt "Review the local scheduler plan?" -FirstYesText "LOCK OFFLINE PLAN" -SecondPrompt "Create this local scheduler job now?" -SecondYesText "INITIALIZE LOCAL JOB")) { return }
    try {
        $result = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "scheduler-init" -Arguments @(
            "--database", $database,
            "--job-id", $jobId,
            "--package-manifest", $validated.Path,
            "--frames-per-chunk", ([string]$framesPerChunk),
            "--max-attempts", ([string]$maxAttempts)
        ) -AllowLocalMutation
        if (-not $result.Ok) {
            $message = if ($null -ne $result.Error) { [string]$result.Error.message } else { "Offline scheduler initialization failed closed." }
            Show-WzhkMessage -Title "OFFLINE SCHEDULER FAILED" -Lines @($message, "No provider command was run.") -Color Red
            return
        }
        Show-WzhkMessage -Title "OFFLINE SCHEDULER READY" -Lines @(
            "DATABASE: $database",
            "JOB ID: $($result.Data.jobId)",
            "CHUNKS: $($result.Data.chunkCount)",
            "No provider, network, render, or billable action occurred."
        ) -Color Green
    }
    catch { Show-WzhkMessage -Title "OFFLINE SCHEDULER FAILED" -Lines @($_.Exception.Message, "No provider command was run.") -Color Red }
}

function Invoke-WzhkCloudMockWorkerWizard {
    $validated = Invoke-WzhkCloudManifestValidationWizard
    if ($null -eq $validated) { return }
    $database = Read-WzhkTextInput -Prompt "Existing offline scheduler SQLite path" -Default (Join-Path ([IO.Path]::GetDirectoryName($validated.Path)) "cloud-scheduler.sqlite3") -Required -MaximumLength 1000 -AllowCancel
    if ($null -eq $database) { return }
    $database = [IO.Path]::GetFullPath($database)
    if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
        Show-WzhkMessage -Title "SCHEDULER DATABASE MISSING" -Lines @($database, "Initialize an offline scheduler job first.") -Color Red
        return
    }
    $storageRoot = Read-WzhkTextInput -Prompt "Synthetic mock object-storage directory" -Default (Join-Path ([IO.Path]::GetDirectoryName($validated.Path)) "cloud-mock-storage") -Required -MaximumLength 1000 -AllowCancel
    if ($null -eq $storageRoot) { return }
    $storageRoot = [IO.Path]::GetFullPath($storageRoot)
    $jobId = Read-WzhkTextInput -Prompt "Existing scheduler job ID" -Required -MaximumLength 128 -Pattern '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' -ValidationMessage "Use 1-128 letters, digits, dots, underscores, or hyphens." -AllowCancel
    if ($null -eq $jobId) { return }
    $workerId = Read-WzhkTextInput -Prompt "Synthetic mock worker ID" -Default "mock-worker-1" -Required -MaximumLength 128 -Pattern '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' -ValidationMessage "Use 1-128 letters, digits, dots, underscores, or hyphens." -AllowCancel
    if ($null -eq $workerId) { return }
    $runMode = Show-WzhkKeypadMenu -Title "MOCK WORKER BOUND" -Items @(
        [pscustomobject]@{ Label = "RUN EXACTLY ONE MOCK CHUNK"; Description = "Claim and generate one synthetic chunk, then return."; Enabled = $true; Value = "Once" },
        [pscustomobject]@{ Label = "RUN MOCK WORKER UNTIL QUEUE IS IDLE"; Description = "Generate every remaining synthetic chunk; may create many local fixture files."; Enabled = $true; Value = "UntilIdle" }
    ) -Context @("MOCK ONLY: this never starts Blender and cannot validate production rendering.")
    if ($null -eq $runMode) { return }
    if (-not (Confirm-WzhkTwoStage -Title "RUN OFFLINE MOCK WORKER" -Details @(
        "MANIFEST: $($validated.Path)",
        "DATABASE: $database",
        "STORAGE: $storageRoot",
        "JOB / WORKER: $jobId / $workerId",
        "MODE: $($runMode.Value)"
    ) -Warnings @(
        "Synthetic PNG fixtures and scheduler state will be written locally.",
        "This is not a Blender, GPU, Brev, visual-quality, or cost benchmark."
    ) -FirstPrompt "Review the synthetic local test?" -FirstYesText "REVIEW MOCK TEST" -SecondPrompt "Write mock worker fixtures now?" -SecondYesText "RUN MOCK WORKER")) { return }
    $arguments = @(
        "--package-manifest", $validated.Path,
        "--database", $database,
        "--storage-root", $storageRoot,
        "--job-id", $jobId,
        "--worker-id", $workerId
    )
    if ($runMode.Value -eq "UntilIdle") { $arguments += "--run-until-idle" }
    try {
        $result = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "mock-worker" -Arguments $arguments -AllowLocalMutation
        if (-not $result.Ok) {
            $message = if ($null -ne $result.Error) { [string]$result.Error.message } else { "Mock worker failed closed." }
            Show-WzhkMessage -Title "MOCK WORKER FAILED" -Lines @($message, "No Blender, provider, or network command was run.") -Color Red
            return
        }
        Show-WzhkMessage -Title "MOCK WORKER COMPLETE" -Lines @(
            "MOCK: $($result.Data.mock)",
            "RESULTS: $(@($result.Data.results).Count)",
            "DATABASE: $database",
            "SYNTHETIC STORAGE: $storageRoot",
            "No Blender, provider, network, or billable action occurred."
        ) -Color Green
    }
    catch { Show-WzhkMessage -Title "MOCK WORKER FAILED" -Lines @($_.Exception.Message, "No Blender, provider, or network command was run.") -Color Red }
}

function Invoke-WzhkCloudSchedulerCancelWizard {
    $database = Read-WzhkTextInput -Prompt "Existing offline scheduler SQLite path" -Required -MaximumLength 1000 -AllowCancel
    if ($null -eq $database) { return }
    $database = [IO.Path]::GetFullPath($database)
    if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
        Show-WzhkMessage -Title "SCHEDULER DATABASE MISSING" -Lines @($database, "No state was changed.") -Color Red
        return
    }
    $jobId = Read-WzhkTextInput -Prompt "Scheduler job ID to cancel" -Required -MaximumLength 128 -AllowCancel
    if ($null -eq $jobId) { return }
    if (-not (Confirm-WzhkTwoStage -Title "CANCEL OFFLINE CLOUD JOB" -Details @("DATABASE: $database", "JOB ID: $jobId") -Warnings @(
        "Cancellation changes local scheduler state and prevents new leases for this job.",
        "It does not stop or delete any provider instance."
    ) -FirstPrompt "Review the exact local job?" -FirstYesText "REVIEW CANCELLATION" -SecondPrompt "Cancel this scheduler job now?" -SecondYesText "CANCEL LOCAL JOB")) { return }
    try {
        $result = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "scheduler-cancel" -Arguments @("--database", $database, "--job-id", $jobId) -AllowLocalMutation -AllowDestructive
        if (-not $result.Ok) {
            $message = if ($null -ne $result.Error) { [string]$result.Error.message } else { "Scheduler cancellation failed closed." }
            Show-WzhkMessage -Title "SCHEDULER CANCELLATION FAILED" -Lines @($message) -Color Red
            return
        }
        Show-WzhkMessage -Title "OFFLINE JOB CANCELLED" -Lines @("DATABASE: $database", "JOB ID: $jobId", "No provider resource was stopped or deleted.") -Color Yellow
    }
    catch { Show-WzhkMessage -Title "SCHEDULER CANCELLATION FAILED" -Lines @($_.Exception.Message) -Color Red }
}

function Invoke-WzhkCloudTournamentRankWizard {
    $inputPath = Read-WzhkTextInput -Prompt "Validated benchmark-results JSON path" -Required -MaximumLength 1000 -AllowCancel
    if ($null -eq $inputPath) { return }
    $inputPath = [IO.Path]::GetFullPath($inputPath)
    if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
        Show-WzhkMessage -Title "BENCHMARK RESULTS MISSING" -Lines @($inputPath, "No state was changed.") -Color Red
        return
    }
    try {
        $result = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "tournament-rank" -Arguments @("--input", $inputPath)
        if (-not $result.Ok) {
            $message = if ($null -ne $result.Error) { [string]$result.Error.message } else { "Tournament ranking failed closed." }
            Show-WzhkMessage -Title "TOURNAMENT RANKING FAILED" -Lines @($message, "No provider command was run.") -Color Red
            return
        }
        $ranked = @($result.Data.ranked)
        $lines = New-Object System.Collections.Generic.List[string]
        $lines.Add("INPUT: $inputPath")
        $lines.Add("ELIGIBLE RESULTS: $($ranked.Count)")
        foreach ($entry in @($ranked | Select-Object -First 10)) {
            $lines.Add("#$($entry.rank) $($entry.benchmark.offer.provider) / $($entry.benchmark.offer.gpu_name) / $($entry.benchmark.offer.region) / cost per frame $($entry.cost_per_frame) / p90 $($entry.benchmark.p90_seconds_per_frame)s")
        }
        if ($ranked.Count -eq 0) { $lines.Add("No entry passed every visual, technical, stability, availability, and non-software-rendering gate.") }
        $lines.Add("Ranking was offline; no benchmark, discovery, provider, or billable action was run.")
        Show-WzhkMessage -Title "OFFLINE BENCHMARK RANKING" -Lines $lines.ToArray() -Color Cyan
    }
    catch { Show-WzhkMessage -Title "TOURNAMENT RANKING FAILED" -Lines @($_.Exception.Message, "No provider command was run.") -Color Red }
}

function Invoke-WzhkCloudOfflinePreparationMenu {
    $choice = Show-WzhkKeypadMenu -Title "CLOUD OFFLINE PREPARATION" -Items @(
        [pscustomobject]@{ Label = "VALIDATE SEALED CLOUD MANIFEST"; Description = "Verify schema, privacy, canonical hash, file entries, identities, and frame range without mutation."; Enabled = $true; Value = "Validate" },
        [pscustomobject]@{ Label = "INITIALIZE OFFLINE DYNAMIC SCHEDULER"; Description = "Create a local SQLite job and chunk queue from a validated manifest; no worker starts."; Enabled = $true; Value = "Init" },
        [pscustomobject]@{ Label = "RUN SYNTHETIC MOCK WORKER"; Description = "Exercise local leases, validation, storage, and publication with generated fixtures only."; Enabled = $true; Value = "Mock" },
        [pscustomobject]@{ Label = "RANK SAVED BENCHMARK RESULTS"; Description = "Calculate eligible GPU cost per validated frame from operator-supplied measured results."; Enabled = $true; Value = "Rank" },
        [pscustomobject]@{ Label = "CANCEL OFFLINE SCHEDULER JOB"; Description = "Stop new leases for one exact local job after two confirmations."; Enabled = $true; Value = "Cancel" }
    ) -Context @(
        "All actions here are local and offline.",
        "No action installs Brev, contacts a provider, starts Blender, or provisions a billable VM."
    )
    if ($null -eq $choice) { return }
    switch ([string]$choice.Value) {
        "Validate" { $null = Invoke-WzhkCloudManifestValidationWizard }
        "Init" { Invoke-WzhkCloudSchedulerInitWizard }
        "Mock" { Invoke-WzhkCloudMockWorkerWizard }
        "Rank" { Invoke-WzhkCloudTournamentRankWizard }
        "Cancel" { Invoke-WzhkCloudSchedulerCancelWizard }
    }
}

function Show-WzhkCloudReadiness {
    param([switch]$InspectBrevCli)

    $localReadiness = Get-WzhkCloudCliReadiness -RepositoryRoot $RepositoryRoot
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("CLOUD CLI        : " + $(if ($localReadiness.Ready) { "READY (OFFLINE)" } else { "NOT READY" }))
    $lines.Add("PYTHON           : " + $(if ([string]::IsNullOrWhiteSpace($localReadiness.PythonExecutable)) { "not found" } else { $localReadiness.PythonExecutable }))
    $lines.Add("PROVISIONING     : DISABLED")
    $lines.Add("DETAIL           : " + $localReadiness.Detail)
    if ($localReadiness.Ready) {
        try {
            $cliResult = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "readiness"
            $lines.Add("PYTHON READINESS : " + $(if ($cliResult.Ok) { "PASS" } else { "FAIL CLOSED" }))
            if ($cliResult.Ok -and $null -ne $cliResult.Data -and $null -ne $cliResult.Data.PSObject.Properties["capabilities"]) {
                $lines.Add("CAPABILITIES     : " + (@($cliResult.Data.capabilities) -join ", "))
            }
        }
        catch { $lines.Add("PYTHON READINESS : FAIL CLOSED - " + $_.Exception.Message) }
    }
    try {
        $brev = Get-WzhkBrevReadiness -RepositoryRoot $RepositoryRoot -InspectInstalledCli:$InspectBrevCli
        $lines.Add("BREV CLI         : " + $(if ($brev.Installed) { $brev.Executable } else { "not installed" }))
        $lines.Add("BREV INSPECTION  : " + $(if ($brev.Inspected) { $brev.CliVersion } else { "not run" }))
        $lines.Add("BREV DETAIL      : " + $brev.Detail)
    }
    catch { $lines.Add("BREV INSPECTION  : FAIL CLOSED - " + $_.Exception.Message) }
    $lines.Add("NETWORK          : no discovery request was made")
    $lines.Add("BILLABLE ACTION  : none")
    Show-WzhkMessage -Title "NVIDIA BREV CLOUD READINESS" -Lines $lines.ToArray() -Color $(if ($localReadiness.Ready) { "Cyan" } else { "Yellow" })
}

function Invoke-WzhkBrevCloudWizard {
    $choice = Show-WzhkKeypadMenu -Title "NVIDIA BREV CLOUD RENDER" -Items @(
        [pscustomobject]@{ Label = "OFFLINE CLOUD READINESS"; Description = "Inspect local package/runtime state only; no Brev command or network request."; Enabled = $true; Value = "Readiness" },
        [pscustomobject]@{ Label = "INSPECT INSTALLED BREV CLI"; Description = "Run only local Brev version/help capability inspection when the CLI exists."; Enabled = $true; Value = "InspectBrev" },
        [pscustomobject]@{ Label = "CREATE SANITIZED PACKAGE"; Description = "Create or bridge an audio-free local package into the sealed cloud protocol; no upload."; Enabled = $true; Value = "Package" },
        [pscustomobject]@{ Label = "CLOUD OFFLINE PREPARATION"; Description = "Validate a manifest, initialize/cancel a scheduler, run a mock worker, or rank measured results."; Enabled = $true; Value = "OfflinePrep" },
        [pscustomobject]@{ Label = "PREPARE / REVIEW BENCHMARK TOURNAMENT"; Description = "Prepare the no-provisioning token flow or rank already measured validated results."; Enabled = $true; Value = "Benchmark" },
        [pscustomobject]@{ Label = "GPU DISCOVERY COMMAND PREVIEW"; Description = "Show the explicit future discovery step without contacting Brev."; Enabled = $true; Value = "DiscoveryPreview" },
        [pscustomobject]@{ Label = "BREV SETUP GUIDANCE"; Description = "Show full-VM, authentication, privacy, and benchmark prerequisites."; Enabled = $true; Value = "Guidance" }
    ) -Context @(
        "NVIDIA Brev full GPU VMs are supported; NVIDIA NIM inference containers are not.",
        "Readiness is offline. Discovery is a separate explicit provider action. Provisioning is fail-closed."
    )
    if ($null -eq $choice) { return }
    switch ([string]$choice.Value) {
        "Readiness" { Show-WzhkCloudReadiness }
        "InspectBrev" { Show-WzhkCloudReadiness -InspectBrevCli }
        "Package" { Invoke-WzhkCloudPackageWizard }
        "OfflinePrep" { Invoke-WzhkCloudOfflinePreparationMenu }
        "Benchmark" { Invoke-WzhkCloudBenchmarkMenu }
        "DiscoveryPreview" {
            Show-WzhkMessage -Title "BREV DISCOVERY PREVIEW" -Lines @(
                "Future explicit command:",
                "python -m cloud_render.cli brev-discover --executable <verified-brev-path>",
                "This may contact Brev and is never called by ValidateOnly or readiness.",
                "No discovery command was executed."
            ) -Color Cyan
        }
        "Guidance" { Show-WzhkMessage -Title "NVIDIA BREV SETUP" -Lines @(Get-WzhkBrevSetupGuidance) -Color Cyan }
    }
}

function Invoke-WzhkCloudBenchmarkMenu {
    $choice = Show-WzhkKeypadMenu -Title "CLOUD BENCHMARK TOURNAMENT" -Items @(
        [pscustomobject]@{ Label = "PREPARE ONE-WORKER AUTHORIZATION"; Description = "Validate the exact token and confirmations, then stop without discovery or provisioning."; Enabled = (@(Get-SavedProfileEntries).Count -gt 0); Value = "Prepare" },
        [pscustomobject]@{ Label = "RANK VALIDATED BENCHMARK RESULTS"; Description = "Calculate cost per validated frame from a local benchmark-results JSON file; no provider call."; Enabled = $true; Value = "Rank" }
    ) -Context @(
        "No live Brev benchmark is authorized in this build.",
        "Preparation ends with PREPARED BUT NOT EXECUTED; ranking consumes existing measured evidence only."
    )
    if ($null -eq $choice) { return }
    if ($choice.Value -eq "Prepare") { Invoke-WzhkCloudBenchmarkWizard }
    else { Invoke-WzhkCloudTournamentRankWizard }
}

function Invoke-WzhkCloudBenchmarkWizard {
    $selected = Select-SavedProfile -Title "CLOUD BENCHMARK // SELECT EXACT SAVED PROFILE"
    if ($null -eq $selected) { return }
    $validated = Invoke-WzhkCloudManifestValidationWizard
    if ($null -eq $validated) { return }
    $sealedManifest = Get-Content -LiteralPath $validated.Path -Raw -Encoding UTF8 | ConvertFrom-Json
    $sourcePackage = $sealedManifest.PSObject.Properties["sourcePackage"]
    if ($null -eq $sourcePackage -or $null -eq $sourcePackage.Value -or [string]::IsNullOrWhiteSpace([string]$sourcePackage.Value.sourceProductionProfileSha256)) {
        Show-WzhkMessage -Title "CLOUD MANIFEST PROVENANCE MISSING" -Lines @(
            "The sealed cloud manifest is not bound to a source production profile hash.",
            "Recreate it with CREATE SANITIZED PACKAGE. No provider command was run."
        ) -Color Red
        return
    }
    $profileSha = (Get-FileHash -LiteralPath $selected.Path -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($profileSha -cne ([string]$sourcePackage.Value.sourceProductionProfileSha256).ToUpperInvariant()) {
        Show-WzhkMessage -Title "PROFILE / CLOUD PACKAGE MISMATCH" -Lines @(
            "SELECTED PROFILE SHA: $profileSha",
            "PACKAGE SOURCE PROFILE SHA: $($sourcePackage.Value.sourceProductionProfileSha256)",
            "Select the exact saved profile used to create the sanitized package."
        ) -Color Red
        return
    }
    $identities = $validated.Result.Data.identities
    $frameRange = $validated.Result.Data.frameRange
    if ([int]$frameRange.start -gt 7065 -or [int]$frameRange.end -lt 8120) {
        Show-WzhkMessage -Title "BENCHMARK RANGE OUTSIDE PACKAGE" -Lines @(
            "PACKAGE FRAMES: $($frameRange.start)-$($frameRange.end)",
            "REQUIRED BOUNDED RANGES: 7065-7094 and 8091-8120",
            "Create a package that contains both benchmark ranges. No provider command was run."
        ) -Color Red
        return
    }
    $packageSha = ([string]$identities.package_sha256).ToUpperInvariant()
    $cloudProfileSha = ([string]$identities.profile_sha256).ToUpperInvariant()
    $sceneSha = ([string]$identities.scene_sha256).ToUpperInvariant()
    $budget = Read-WzhkDecimalInput -Prompt "Maximum one-worker benchmark budget in USD" -Default 10.0 -Minimum 0.01 -Maximum 1000000.0 -AllowCancel
    if ($null -eq $budget) { return }
    $expectedToken = New-WzhkBrevBenchmarkAuthorizationToken -PackageSha256 $packageSha -ProfileSha256 $cloudProfileSha -MaxBudget ([decimal]$budget)
    $readiness = Get-WzhkCloudCliReadiness -RepositoryRoot $RepositoryRoot
    if ($readiness.Ready) {
        try {
            $tokenResult = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "authorization-token" -Arguments @(
                "--package-sha", $packageSha.ToUpperInvariant(),
                "--scene-sha", $sceneSha.ToUpperInvariant(),
                "--profile-sha", $cloudProfileSha,
                "--max-budget", ([decimal]$budget).ToString("0.00", [Globalization.CultureInfo]::InvariantCulture)
            )
            if ($tokenResult.Ok -and $null -ne $tokenResult.Data) {
                foreach ($propertyName in @("authorizationToken", "token")) {
                    $property = $tokenResult.Data.PSObject.Properties[$propertyName]
                    if ($null -ne $property -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)) { $expectedToken = [string]$property.Value; break }
                }
            }
        }
        catch {
            Show-WzhkMessage -Title "CLOUD TOKEN FAILURE" -Lines @($_.Exception.Message, "No provider or billable command was run.") -Color Red
            return
        }
    }

    $candidateText = @(Get-WzhkBrevCandidateGpuNames) -join ", "
    Show-WzhkMessage -Title "BENCHMARK PLAN TOKEN" -Lines @(
        "PROFILE: $($selected.Path)",
        "SOURCE PROFILE SHA: $profileSha",
        "CLOUD PROFILE SHA: $cloudProfileSha",
        "CLOUD SCENE SHA: $sceneSha",
        "CLOUD MANIFEST: $($validated.Path)",
        "PACKAGE SHA: $($packageSha.ToUpperInvariant())",
        "MAX BUDGET: $" + ([decimal]$budget).ToString("0.00", [Globalization.CultureInfo]::InvariantCulture),
        "RANGES: 7065-7094 and 8091-8120",
        "WORKERS: exactly 1",
        "UNDISCOVERED CANDIDATE FAMILIES: $candidateText",
        "EXACT TOKEN:",
        $expectedToken,
        "PREPARED BUT NOT EXECUTED"
    ) -Color Cyan
    $enteredToken = Read-WzhkTextInput -Prompt "Type the exact benchmark authorization token" -Required -MaximumLength 240 -AllowCancel
    if ($null -eq $enteredToken) { return }
    $tokenCheck = Test-WzhkBrevBenchmarkPlanLock -ExpectedToken $expectedToken -EnteredToken $enteredToken -WorkerCount 1
    if (@($tokenCheck.Issues | Where-Object { $_ -match 'token' }).Count -gt 0) {
        Show-WzhkMessage -Title "AUTHORIZATION REFUSED" -Lines @("The exact token did not match. No provider command was run.") -Color Red
        return
    }
    $confirmed = Confirm-WzhkTwoStage -Title "OFFLINE BREV BENCHMARK PLAN" -Details @(
        "SOURCE PROFILE SHA: $($profileSha.Substring(0, 12))",
        "CLOUD PROFILE SHA: $($cloudProfileSha.Substring(0, 12))",
        "PACKAGE SHA: $($packageSha.Substring(0, 12).ToUpperInvariant())",
        "MAX BUDGET: $" + ([decimal]$budget).ToString("0.00", [Globalization.CultureInfo]::InvariantCulture),
        "WORKERS: 1",
        "RANGES: 7065-7094 and 8091-8120"
    ) -Warnings @(
        "This offline rehearsal does not persist a live plan and makes no provider call.",
        "No final billable confirmation is requested here. A separately authorized live command remains disabled until current Brev capabilities and the production worker environment are verified."
    ) -FirstPrompt "Review this token-bound offline plan?" -FirstYesText "REVIEW OFFLINE PLAN" -SecondPrompt "Lock this offline plan and exit without provisioning?" -SecondYesText "LOCK CLOUD PLAN"
    if (-not $confirmed) { return }
    $planLockCheck = Test-WzhkBrevBenchmarkPlanLock -ExpectedToken $expectedToken -EnteredToken $enteredToken -WorkerCount 1 -PlanLocked
    if (-not $planLockCheck.Valid) { throw ($planLockCheck.Issues -join " ") }
    Show-WzhkMessage -Title "BREV BENCHMARK PREPARED" -Lines @(
        "PREPARED BUT NOT EXECUTED",
        "The exact token and offline plan lock were validated.",
        "No final billable authorization was requested or recorded.",
        "No Brev discovery, provisioning, upload, render, or network command was run.",
        "Exit without provisioning a fleet."
    ) -Color Green
}

function Invoke-WzhkCloudFleetMonitor {
    $readiness = Get-WzhkCloudCliReadiness -RepositoryRoot $RepositoryRoot
    if (-not $readiness.Ready) {
        Show-WzhkMessage -Title "CLOUD STATUS NOT READY" -Lines @($readiness.Detail, "No provider command was run.") -Color Yellow
        return
    }
    $database = Read-WzhkTextInput -Prompt "Local scheduler SQLite database path" -Required -AllowCancel
    if ($null -eq $database) { return }
    $database = [IO.Path]::GetFullPath($database)
    if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
        Show-WzhkMessage -Title "CLOUD JOB STATUS SNAPSHOT" -Lines @("Scheduler database does not exist: $database", "Snapshot mode will not create a database or contact a provider.") -Color Yellow
        return
    }
    $jobId = Read-WzhkTextInput -Prompt "Cloud render job ID" -Required -AllowCancel
    if ($null -eq $jobId) { return }
    try {
        $result = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "scheduler-status" -Arguments @("--database", $database, "--job-id", $jobId) -AllowLocalMutation
        if (-not $result.Ok) {
            $message = if ($null -ne $result.Error) { [string]$result.Error.message } else { "Scheduler status failed closed." }
            Show-WzhkMessage -Title "CLOUD JOB STATUS SNAPSHOT" -Lines @($message, "No provider command was run.") -Color Yellow
            return
        }
        Show-WzhkMessage -Title "CLOUD JOB STATUS SNAPSHOT" -Lines @(Get-WzhkCloudDashboardLines -Status $result.Data) -Color Cyan
    }
    catch { Show-WzhkMessage -Title "CLOUD JOB STATUS SNAPSHOT" -Lines @($_.Exception.Message, "No provider command was run.") -Color Red }
}

function Invoke-WzhkCloudImportWizard {
    $validated = Invoke-WzhkCloudManifestValidationWizard
    if ($null -eq $validated) { return }
    $returned = Read-WzhkTextInput -Prompt "Downloaded cloud return directory" -Required -MaximumLength 1000 -AllowCancel
    if ($null -eq $returned) { return }
    $returned = [IO.Path]::GetFullPath($returned)
    if (-not (Test-Path -LiteralPath $returned -PathType Container)) {
        Show-WzhkMessage -Title "CLOUD RETURN MISSING" -Lines @($returned, "No state was changed.") -Color Red
        return
    }
    $returnManifest = Read-WzhkTextInput -Prompt "Sealed cloud chunk-output manifest path" -Required -MaximumLength 1000 -AllowCancel
    if ($null -eq $returnManifest) { return }
    $returnManifest = [IO.Path]::GetFullPath($returnManifest)
    if (-not (Test-Path -LiteralPath $returnManifest -PathType Leaf)) {
        Show-WzhkMessage -Title "CLOUD RETURN MANIFEST MISSING" -Lines @($returnManifest, "No state was changed.") -Color Red
        return
    }
    try { $returnedChunk = Get-WzhkReturnedChunkFrameRange -ManifestPath $returnManifest }
    catch {
        Show-WzhkMessage -Title "CLOUD RETURN MANIFEST INVALID" -Lines @(
            $_.Exception.Message,
            "This is a bounded planning check; sealed hashes and identities have not yet been accepted.",
            "No state was changed."
        ) -Color Red
        return
    }
    $quarantineRoot = Read-WzhkTextInput -Prompt "Local cloud-return quarantine root" -Required -MaximumLength 1000 -AllowCancel
    if ($null -eq $quarantineRoot) { return }
    $quarantineRoot = [IO.Path]::GetFullPath($quarantineRoot)
    $outputFrames = Read-WzhkTextInput -Prompt "Compatible managed output frames directory" -Required -MaximumLength 1000 -AllowCancel
    if ($null -eq $outputFrames) { return }
    $outputFrames = [IO.Path]::GetFullPath($outputFrames)
    if (Test-WzhkPathsOverlap -First $returned -Second $quarantineRoot) {
        Show-WzhkMessage -Title "CLOUD IMPORT PATHS OVERLAP" -Lines @(
            "RETURN DIRECTORY: $returned",
            "QUARANTINE ROOT: $quarantineRoot",
            "Quarantine must not contain, equal, or be contained by the returned directory. No state was changed."
        ) -Color Red
        return
    }
    if ((Test-WzhkPathsOverlap -First $returned -Second $outputFrames) -or (Test-WzhkPathsOverlap -First $quarantineRoot -Second $outputFrames)) {
        Show-WzhkMessage -Title "CLOUD IMPORT PATHS OVERLAP" -Lines @(
            "RETURN DIRECTORY: $returned",
            "QUARANTINE ROOT: $quarantineRoot",
            "OUTPUT FRAMES: $outputFrames",
            "Return, quarantine, and canonical output trees must be pairwise disjoint. No state was changed."
        ) -Color Red
        return
    }
    $frameRange = $validated.Result.Data.frameRange
    if ($returnedChunk.Start -lt [int]$frameRange.start -or $returnedChunk.End -gt [int]$frameRange.end) {
        Show-WzhkMessage -Title "CLOUD RETURN RANGE REJECTED" -Lines @(
            "PACKAGE FRAMES: $($frameRange.start)-$($frameRange.end)",
            "DECLARED CHUNK: $($returnedChunk.Start)-$($returnedChunk.End)",
            "The returned chunk is outside the sealed package range. No state was changed."
        ) -Color Red
        return
    }
    if (-not (Confirm-WzhkTwoStage -Title "IMPORT CLOUD PNG RETURN" -Details @(
        "PACKAGE MANIFEST: $($validated.Path)",
        "RETURN DIRECTORY: $returned",
        "RETURN MANIFEST: $returnManifest",
        "QUARANTINE: $quarantineRoot",
        "OUTPUT FRAMES: $outputFrames",
        "PACKAGE FRAMES: $($frameRange.start)-$($frameRange.end)",
        "DECLARED CHUNK: $($returnedChunk.Start)-$($returnedChunk.End) ($($returnedChunk.Count) frames)"
    ) -Warnings @(
        "The return is copied into quarantine before validation.",
        "Only missing canonical PNG frames are atomically published; existing differing frames remain local and are reported as conflicts.",
        "Current cloud-native import validates PNG only. It does not accept EXR or a video master."
    ) -FirstPrompt "Review exact package and return identities?" -FirstYesText "REVIEW CLOUD IMPORT" -SecondPrompt "Quarantine, validate, and publish only missing valid PNG frames?" -SecondYesText "IMPORT CLOUD PNG FRAMES")) { return }
    try {
        $result = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "import-return" -Arguments @(
            "--returned", $returned,
            "--quarantine-root", $quarantineRoot,
            "--output-frames", $outputFrames,
            "--manifest", $returnManifest,
            "--package-manifest", $validated.Path,
            "--frame-start", ([string]$returnedChunk.Start),
            "--frame-end", ([string]$returnedChunk.End)
        ) -AllowLocalMutation -AllowDestructive
        if (-not $result.Ok) {
            $message = if ($null -ne $result.Error) { [string]$result.Error.message } else { "Cloud return import failed closed." }
            Show-WzhkMessage -Title "CLOUD RETURN REJECTED" -Lines @($message, "No provider or network command was run.") -Color Red
            return
        }
        Show-WzhkMessage -Title "CLOUD RETURN IMPORTED" -Lines @(
            "QUARANTINE: $($result.Data.quarantine)",
            "PUBLISHED: $(@($result.Data.publishedFrames).Count)",
            "IDENTICAL: $(@($result.Data.identicalFrames).Count)",
            "CONFLICTS RETAINED: $(@($result.Data.conflicts).Count)",
            "No provider, network, encode, or audio action occurred."
        ) -Color $(if (@($result.Data.conflicts).Count -eq 0) { "Green" } else { "Yellow" })
    }
    catch { Show-WzhkMessage -Title "CLOUD RETURN REJECTED" -Lines @($_.Exception.Message, "No provider or network command was run.") -Color Red }
}

function Invoke-WzhkEncodeMuxWizard {
    $selected = Select-SavedProfile -Title "ENCODE / MUX // SELECT EXACT PROFILE"
    if ($null -eq $selected) { return }
    $profile = Import-WzhkRenderProfile -Path $selected.Path -VerifyFiles
    $productionDirectory = Read-WzhkTextInput -Prompt "Complete verified frame-sequence output directory" -Required -AllowCancel
    if ($null -eq $productionDirectory) { return }
    $audioPath = Read-WzhkTextInput -Prompt "Private local source audio path" -Required -AllowCancel
    if ($null -eq $audioPath) { return }
    $outputPath = Read-WzhkTextInput -Prompt "Final local video output path" -Required -AllowCancel
    if ($null -eq $outputPath) { return }
    $kindChoice = Show-WzhkKeypadMenu -Title "ENCODE OUTPUT KIND" -Items @(
        [pscustomobject]@{ Label = "MASTER"; Description = "Create the configured local mezzanine/master."; Enabled = $true; Value = "Master" },
        [pscustomobject]@{ Label = "DELIVERY"; Description = "Create the configured local delivery file."; Enabled = $true; Value = "Delivery" }
    )
    if ($null -eq $kindChoice) { return }
    $modeChoice = Show-WzhkKeypadMenu -Title "ENCODE / MUX MODE" -Items @(
        [pscustomobject]@{ Label = "PREFLIGHT ONLY"; Description = "Verify sequence, clocks, tools, hashes, and storage without encoding."; Enabled = $true; Value = "Preflight" },
        [pscustomobject]@{ Label = "START LOCAL ENCODE / MUX"; Description = "Encode locally and mux the private audio after two confirmations."; Enabled = $true; Value = "Production" }
    ) -Context @("Private audio remains local and is never included in a cloud package.")
    if ($null -eq $modeChoice) { return }
    $details = @(
        "PROFILE: $($selected.Path)",
        "SCENE: $($profile.approvedScenePath)",
        "FRAMES: $productionDirectory",
        "PRIVATE AUDIO: $audioPath",
        "OUTPUT: $outputPath",
        "KIND: $($kindChoice.Value)",
        "MODE: $($modeChoice.Value)"
    )
    if (-not (Confirm-WzhkTwoStage -Title "LOCAL ENCODE / PRIVATE AUDIO MUX" -Details $details -Warnings @("Encoding starts only in Production mode; Preflight remains read-only.") -FirstPrompt "Review exact local inputs and output?" -FirstYesText "LOCK ENCODE PLAN" -SecondPrompt "Continue with the selected local mode?" -SecondYesText $(if ($modeChoice.Value -eq "Preflight") { "RUN PREFLIGHT" } else { "START LOCAL ENCODE" }))) { return }
    $arguments = @(
        "-ApprovedScenePath", [string]$profile.approvedScenePath,
        "-RenderProfilePath", [string]$selected.Path,
        "-ProductionDirectory", [IO.Path]::GetFullPath($productionDirectory),
        "-AudioPath", [IO.Path]::GetFullPath($audioPath),
        "-OutputPath", [IO.Path]::GetFullPath($outputPath),
        "-OutputKind", [string]$kindChoice.Value
    )
    if ($modeChoice.Value -eq "Preflight") { $arguments += "-Preflight" }
    $result = @(& $encoderScript @arguments 2>&1)
    Show-WzhkMessage -Title "ENCODE / MUX RESULT" -Lines @($result | ForEach-Object { [string]$_ }) -Color $(if ($LASTEXITCODE -eq 0) { "Green" } else { "Red" })
}

if (-not [string]::IsNullOrWhiteSpace($script:CliRenderProfilePath)) {
    if ([Console]::IsInputRedirected) {
        [Console]::Error.WriteLine("-RenderProfile requires an interactive console for both final confirmations. No render was started.")
        exit 2
    }
    $cliRenderResult = Invoke-WzhkSavedProfileRenderWizard -Mode "Production" -SavedProfilePath $script:CliRenderProfilePath
    if ($null -ne $cliRenderResult -and $cliRenderResult.Ok) { exit 0 }
    if ($null -ne $cliRenderResult -and $null -ne $cliRenderResult.PSObject.Properties["ExitCode"]) { exit [int]$cliRenderResult.ExitCode }
    exit 1
}

$script:SelectedPrep = ""
if (-not [string]::IsNullOrWhiteSpace($PrepDirectory)) {
    $resolvedPrep = [IO.Path]::GetFullPath($PrepDirectory)
    if (Test-Path -LiteralPath $resolvedPrep -PathType Container) {
        $script:SelectedPrep = $resolvedPrep
    }
}

while ($true) {
    $savedProfiles = @(Get-SavedProfileEntries)
    $outputs = @(Get-AllWzhkOutputCandidates)
    $choice = Show-WzhkMissionControlMenu `
        -Context @(
            "Selected package: $(if ([string]::IsNullOrWhiteSpace($script:SelectedPrep)) { 'not selected — choose only when creating a profile' } else { $script:SelectedPrep })",
            "Saved profiles: $($savedProfiles.Count)  •  Existing outputs: $($outputs.Count)",
            "TEMPLATE → RESOLVED SETTINGS → SAVED PROFILE → AUTHORIZED PROFILE → ACTIVE RENDER",
            "Production keeps exact authorization, mutex, storage, chunk, and resume checks."
        ) `
        -ProfilesAvailable:($savedProfiles.Count -gt 0) `
        -DashboardAvailable:(Test-Path -LiteralPath $watcherScript -PathType Leaf) `
        -OutputAvailable:($outputs.Count -gt 0)

    if ($null -eq $choice -or $choice.Value -eq "Exit") {
        break
    }

    switch ([string]$choice.Value) {
        "Calibrate" { Invoke-WzhkCalibrationWizard }
        "Profiles" { Invoke-WzhkProfileMenu }
        "Generate720" { Invoke-WzhkCalibrationWizard -InitialAction "Generate720" }
        "GenerateRecommended" { Invoke-WzhkCalibrationWizard -InitialAction "GenerateRecommended" }
        "LocalRender" { Invoke-WzhkLocalRenderMenu }
        "BrevCloud" { Invoke-WzhkBrevCloudWizard }
        "CloudBenchmark" { Invoke-WzhkCloudBenchmarkMenu }
        "CloudFleet" { Invoke-WzhkCloudFleetMonitor }
        "CloudImport" { Invoke-WzhkCloudImportWizard }
        "EncodeMux" { Invoke-WzhkEncodeMuxWizard }
        "LocalOperations" { Invoke-WzhkLocalOperationsMenu }
        "Outsource" { Invoke-WzhkOutsourceWizard }
    }
}

Clear-Host
Write-WzhkLogo
Write-WzhkFrameTop -Title "MISSION CONTROL OFFLINE"
Write-WzhkFrameLine -Text "  No render output was deleted or altered by closing the launcher." -Color Green
Write-WzhkFrameBottom
