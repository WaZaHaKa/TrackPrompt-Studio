[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-WzhkTest {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Assert-WzhkEqual {
    param(
        [AllowNull()][object]$Expected,
        [AllowNull()][object]$Actual,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if ($Expected -ne $Actual) {
        throw ($Message + " Expected: '" + [string]$Expected + "'. Actual: '" + [string]$Actual + "'.")
    }
}

function ConvertTo-WzhkNativeArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }

    $builder = New-Object System.Text.StringBuilder
    $null = $builder.Append('"')
    $backslashCount = 0

    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashCount += 1
            continue
        }

        if ($character -eq '"') {
            if ($backslashCount -gt 0) {
                $null = $builder.Append(('\' * ($backslashCount * 2)))
                $backslashCount = 0
            }
            $null = $builder.Append('\"')
            continue
        }

        if ($backslashCount -gt 0) {
            $null = $builder.Append(('\' * $backslashCount))
            $backslashCount = 0
        }
        $null = $builder.Append($character)
    }

    if ($backslashCount -gt 0) {
        $null = $builder.Append(('\' * ($backslashCount * 2)))
    }
    $null = $builder.Append('"')
    return $builder.ToString()
}

function Invoke-WzhkBoundedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label,
        [ValidateRange(1000, 300000)][int]$TimeoutMilliseconds = 60000
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = $Arguments
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo

    try {
        if (-not $process.Start()) {
            throw ($Label + " did not start.")
        }

        $process.StandardInput.Close()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()

        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            try {
                $process.Kill()
            }
            catch {
                # The bounded validation child may have exited between the timeout and Kill().
            }
            $process.WaitForExit()
            throw ($Label + " timed out after " + $TimeoutMilliseconds + " ms. An interactive prompt may have been reached.")
        }

        # The parameterless wait flushes asynchronous redirected-stream events on .NET Framework.
        $process.WaitForExit()
        $standardOutput = $stdoutTask.Result
        $standardError = $stderrTask.Result

        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            StandardOutput = [string]$standardOutput
            StandardError = [string]$standardError
        }
    }
    finally {
        $process.Dispose()
    }
}

function Assert-WzhkProcessSucceeded {
    param(
        [Parameter(Mandatory = $true)][object]$Result,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ([int]$Result.ExitCode -ne 0) {
        throw (
            $Label + " exited with code " + $Result.ExitCode + "." + [Environment]::NewLine +
            "STDOUT:" + [Environment]::NewLine + $Result.StandardOutput + [Environment]::NewLine +
            "STDERR:" + [Environment]::NewLine + $Result.StandardError
        )
    }
}

function Write-WzhkFixtureText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowEmptyString()][string]$Value
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

function New-WzhkFixtureDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $null = [System.IO.Directory]::CreateDirectory($Path)
    return $Path
}

function New-WzhkFixtureFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    Write-WzhkFixtureText -Path $Path -Value ""
}

$fixtureRoot = $null

try {
    Assert-WzhkTest `
        -Condition ($PSVersionTable.PSEdition -eq "Desktop" -and $PSVersionTable.PSVersion.Major -eq 5 -and $PSVersionTable.PSVersion.Minor -eq 1) `
        -Message ("This test must run in Windows PowerShell 5.1. Current host: " + $PSVersionTable.PSVersion)

    $repositoryRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
    $controlCenterPath = Join-Path $repositoryRoot "wzhk-media-control-center.ps1"
    $launcherPath = Join-Path $repositoryRoot "WZHK-Media-Launcher.cmd"
    $watcherPath = Join-Path $repositoryRoot "tools\watch-trackprompt-final-render.ps1"
    $rendererPath = Join-Path $repositoryRoot "render-trackprompt-final.ps1"
    $encoderPath = Join-Path $repositoryRoot "encode-trackprompt-final.ps1"
    $moduleRoot = Join-Path $repositoryRoot "tools\wzhk-launcher"

    $requiredPaths = @(
        $controlCenterPath,
        $launcherPath,
        $watcherPath,
        $rendererPath,
        $encoderPath,
        (Join-Path $moduleRoot "WZHK.UI.psm1"),
        (Join-Path $moduleRoot "WZHK.Discovery.psm1"),
        (Join-Path $moduleRoot "WZHK.Profiles.psm1"),
        (Join-Path $moduleRoot "WZHK.ProfileBuilder.psm1"),
        (Join-Path $moduleRoot "WZHK.Execution.psm1"),
        (Join-Path $moduleRoot "WZHK.Calibration.psm1"),
        (Join-Path $moduleRoot "WZHK.Performance.psm1"),
        (Join-Path $moduleRoot "WZHK.Outsource.psm1"),
        (Join-Path $moduleRoot "WZHK.Cloud.psm1"),
        (Join-Path $moduleRoot "WZHK.Brev.psm1"),
        (Join-Path $repositoryRoot "cloud_render\cli.py"),
        (Join-Path $repositoryRoot "docs\render-calibration.md"),
        (Join-Path $repositoryRoot "docs\render-profiles.md"),
        (Join-Path $repositoryRoot "docs\local-performance-mode.md"),
        (Join-Path $repositoryRoot "docs\cloud-rendering.md"),
        (Join-Path $repositoryRoot "docs\nvidia-brev-rendering.md"),
        (Join-Path $repositoryRoot "docs\cloud-render-privacy.md"),
        (Join-Path $repositoryRoot "docs\cloud-render-recovery.md"),
        (Join-Path $repositoryRoot "docs\cloud-render-cost-model.md")
    )
    foreach ($requiredPath in $requiredPaths) {
        Assert-WzhkTest `
            -Condition (Test-Path -LiteralPath $requiredPath -PathType Leaf) `
            -Message ("Required WZHK file is missing: " + $requiredPath)
    }

    $moduleFiles = @(
        Get-ChildItem -LiteralPath $moduleRoot -File -Filter "*.psm1" |
            Sort-Object Name
    )
    Assert-WzhkEqual -Expected 10 -Actual $moduleFiles.Count -Message "Expected exactly ten modular WZHK launcher modules."

    $parseFiles = @(
        Get-Item -LiteralPath $controlCenterPath
        $moduleFiles
        Get-Item -LiteralPath $watcherPath
        Get-Item -LiteralPath $rendererPath
        Get-Item -LiteralPath $encoderPath
        Get-Item -LiteralPath $PSCommandPath
    )
    Assert-WzhkEqual -Expected 15 -Actual $parseFiles.Count -Message "The Windows PowerShell parser sweep must cover fifteen files."

    $controlCenterAst = $null
    foreach ($file in $parseFiles) {
        $tokens = $null
        $parseErrors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile(
            $file.FullName,
            [ref]$tokens,
            [ref]$parseErrors
        )

        if (@($parseErrors).Count -gt 0) {
            $messages = @(
                foreach ($parseError in @($parseErrors)) {
                    "line " + $parseError.Extent.StartLineNumber + ", column " + $parseError.Extent.StartColumnNumber + ": " + $parseError.Message
                }
            )
            throw ($file.FullName + " has Windows PowerShell parser errors: " + ($messages -join " | "))
        }

        if ($file.FullName -eq $controlCenterPath) {
            $controlCenterAst = $ast
        }

        $sourceText = [System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)
        $containsNonAscii = $false
        foreach ($character in $sourceText.ToCharArray()) {
            if ([int]$character -gt 127) {
                $containsNonAscii = $true
                break
            }
        }

        if ($containsNonAscii) {
            $prefix = @(Get-Content -LiteralPath $file.FullName -Encoding Byte -TotalCount 3)
            $hasUtf8Bom = (
                $prefix.Count -eq 3 -and
                $prefix[0] -eq 0xEF -and
                $prefix[1] -eq 0xBB -and
                $prefix[2] -eq 0xBF
            )
            Assert-WzhkTest -Condition $hasUtf8Bom -Message ($file.FullName + " contains non-ASCII UI text but is not UTF-8 with BOM.")
        }
    }

    Assert-WzhkTest -Condition ($null -ne $controlCenterAst.ParamBlock) -Message "The control center has no parameter block."
    $validateOnlyParameters = @(
        $controlCenterAst.ParamBlock.Parameters |
            Where-Object { $_.Name.VariablePath.UserPath -eq "ValidateOnly" }
    )
    Assert-WzhkEqual -Expected 1 -Actual $validateOnlyParameters.Count -Message "The control center must expose one -ValidateOnly switch."
    foreach ($parameterName in @("ListProfiles", "ValidateProfile", "RenderProfile", "ProfilePath")) {
        $matchingParameters = @(
            $controlCenterAst.ParamBlock.Parameters |
                Where-Object { $_.Name.VariablePath.UserPath -eq $parameterName }
        )
        Assert-WzhkEqual -Expected 1 -Actual $matchingParameters.Count -Message ("Missing command-line profile parameter: " + $parameterName)
    }
    $benchmarkWizardDefinitions = @($controlCenterAst.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "Invoke-WzhkCloudBenchmarkWizard"
    }, $true))
    Assert-WzhkEqual -Expected 1 -Actual $benchmarkWizardDefinitions.Count -Message "Expected exactly one offline cloud benchmark wizard."
    $benchmarkWizardSource = [string]$benchmarkWizardDefinitions[0].Extent.Text
    Assert-WzhkTest -Condition ($benchmarkWizardSource -match 'Test-WzhkBrevBenchmarkPlanLock') -Message "Offline benchmark preparation does not use the non-billable plan-lock contract."
    Assert-WzhkTest -Condition ($benchmarkWizardSource -notmatch 'Test-WzhkBrevBenchmarkAuthorization') -Message "Offline benchmark preparation invoked the future live billable-authorization helper."
    Assert-WzhkTest -Condition ($benchmarkWizardSource -notmatch 'PROVISION BILLABLE GPU WORKERS') -Message "Offline benchmark preparation rehearsed the reserved final billable confirmation."

    $moduleSpecifications = @(
        [pscustomobject]@{
            Path = Join-Path $moduleRoot "WZHK.UI.psm1"
            Functions = @(
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
                "Read-WzhkYesNo",
                "Show-WzhkMessage",
                "Show-WzhkDoneAnimation"
            )
        },
        [pscustomobject]@{
            Path = Join-Path $moduleRoot "WZHK.Discovery.psm1"
            Functions = @(
                "Get-WzhkHashPrefix",
                "Get-WzhkProfileInfo",
                "Get-WzhkSceneInfo",
                "Get-WzhkPrepCandidates",
                "Find-WzhkApprovedScene",
                "Get-WzhkProfileCandidates",
                "Find-WzhkAuthorizationToken",
                "Get-WzhkOutputStats",
                "Get-WzhkOutputCandidates",
                "New-WzhkOutputPath"
            )
        },
        [pscustomobject]@{
            Path = Join-Path $moduleRoot "WZHK.Profiles.psm1"
            Functions = @(
                "ConvertTo-WzhkSafeProfileName",
                "ConvertTo-WzhkProfileSlug",
                "Get-WzhkFileSha256",
                "Get-WzhkRenderProfileTemplates",
                "New-WzhkRenderProfile",
                "Normalize-WzhkRenderProfile",
                "Test-WzhkRenderProfile",
                "Set-WzhkProfileValue",
                "Save-WzhkRenderProfile",
                "Import-WzhkRenderProfile",
                "Resolve-WzhkRecommendedProfilePointer",
                "Get-WzhkSavedRenderProfiles",
                "Copy-WzhkRenderProfile",
                "Rename-WzhkRenderProfile",
                "Remove-WzhkRenderProfile",
                "New-WzhkProfileAuthorizationRequest",
                "New-WzhkProfileAuthorizationRecord",
                "Test-WzhkProfileAuthorizationRecord",
                "Get-WzhkOutputDirectoryInspection",
                "New-WzhkUniqueRenderSubfolder",
                "Test-WzhkOutputCompatibility",
                "Compare-WzhkRenderProfiles"
            )
        },
        [pscustomobject]@{
            Path = Join-Path $moduleRoot "WZHK.ProfileBuilder.psm1"
            Functions = @(
                "Get-WzhkProfileBuilderStages",
                "Resolve-WzhkQualitySettings",
                "Update-WzhkBuilderDerivedValues",
                "Invoke-WzhkProfileBuilder"
            )
        },
        [pscustomobject]@{
            Path = Join-Path $moduleRoot "WZHK.Execution.psm1"
            Functions = @(
                "Start-WzhkRenderWatcher",
                "Invoke-WzhkRenderMode",
                "Open-WzhkOutput",
                "Request-WzhkStopAfterCurrentChunk",
                "Cancel-WzhkStopAfterCurrentChunk"
            )
        },
        [pscustomobject]@{
            Path = Join-Path $moduleRoot "WZHK.Calibration.psm1"
            Functions = @(
                "Get-WzhkCalibrationStatistics",
                "Get-WzhkAdaptiveChunkPlan",
                "Get-WzhkMachineFingerprint",
                "Get-WzhkRenderSafetyAudit",
                "Test-WzhkCalibrationValidity",
                "Select-WzhkRecommendedCalibrationProfile",
                "New-WzhkCalibratedRenderProfile"
            )
        },
        [pscustomobject]@{
            Path = Join-Path $moduleRoot "WZHK.Performance.psm1"
            Functions = @(
                "Get-WzhkPowerSource",
                "Set-WzhkSleepInhibition",
                "Get-WzhkCompetingGpuProcesses",
                "Get-WzhkNvidiaTelemetry",
                "Set-WzhkBlenderProcessPriority",
                "Start-WzhkExclusivePerformanceMode",
                "Stop-WzhkExclusivePerformanceMode",
                "Test-WzhkThermalSafety"
            )
        },
        [pscustomobject]@{
            Path = Join-Path $moduleRoot "WZHK.Outsource.psm1"
            Functions = @(
                "New-WzhkRemoteChunkDistribution",
                "Get-WzhkOutsourceEstimate",
                "Invoke-WzhkRemoteTool"
            )
        },
        [pscustomobject]@{
            Path = Join-Path $moduleRoot "WZHK.Cloud.psm1"
            Functions = @(
                "Resolve-WzhkCloudPython",
                "Get-WzhkCloudCliReadiness",
                "Invoke-WzhkCloudCli",
                "Get-WzhkCloudDashboardLines"
            )
        },
        [pscustomobject]@{
            Path = Join-Path $moduleRoot "WZHK.Brev.psm1"
            Functions = @(
                "Find-WzhkBrevExecutable",
                "Get-WzhkBrevCandidateGpuNames",
                "New-WzhkBrevBenchmarkAuthorizationToken",
                "Test-WzhkBrevBenchmarkPlanLock",
                "Test-WzhkBrevBenchmarkAuthorization",
                "Get-WzhkBrevReadiness",
                "Get-WzhkBrevSetupGuidance"
            )
        }
    )

    foreach ($moduleSpecification in $moduleSpecifications) {
        $module = Import-Module $moduleSpecification.Path -Force -PassThru -Scope Local -ErrorAction Stop
        foreach ($functionName in $moduleSpecification.Functions) {
            Assert-WzhkTest `
                -Condition $module.ExportedFunctions.ContainsKey($functionName) `
                -Message ((Split-Path -Leaf $moduleSpecification.Path) + " does not export " + $functionName + ".")
        }
    }

    $expectedMainMenuLabels = @(
        "CALIBRATE THIS PC",
        "CREATE / EDIT PROFILE",
        "GENERATE 720P HYPER PROFILE",
        "GENERATE RECOMMENDED PROFILE",
        "LOCAL RENDER",
        "NVIDIA BREV CLOUD RENDER",
        "CLOUD BENCHMARK TOURNAMENT",
        "CLOUD JOB STATUS SNAPSHOT",
        "IMPORT / VERIFY CLOUD OUTPUT",
        "ENCODE / MUX FINAL VIDEO",
        "LOCAL OPERATIONS / SAFETY",
        "OUTSOURCE / REMOTE RENDER",
        "EXIT MISSION CONTROL"
    )
    $mainMenuItems = @(Get-WzhkMissionControlMenuItems)
    Assert-WzhkEqual -Expected 13 -Actual $mainMenuItems.Count -Message "Mission Control must expose exactly thirteen top-level items."
    for ($menuIndex = 0; $menuIndex -lt $expectedMainMenuLabels.Count; $menuIndex += 1) {
        Assert-WzhkEqual -Expected $expectedMainMenuLabels[$menuIndex] -Actual $mainMenuItems[$menuIndex].Label -Message ("Top-level menu label mismatch at position " + ($menuIndex + 1) + ".")
    }
    $firstMenuPage = Get-WzhkMenuPage -Items $mainMenuItems -SelectedIndex 0
    $secondMenuPage = Get-WzhkMenuPage -Items $mainMenuItems -SelectedIndex 9
    Assert-WzhkEqual -Expected 2 -Actual $firstMenuPage.PageCount -Message "A menu with more than nine actions was not paginated."
    Assert-WzhkEqual -Expected 9 -Actual $secondMenuPage.StartIndex -Message "Second menu page did not begin with item ten."
    Assert-WzhkEqual -Expected "ENCODE / MUX FINAL VIDEO" -Actual $secondMenuPage.Items[0].Label -Message "Top-level item ten is not reachable on page two."
    Assert-WzhkEqual -Expected 9 -Actual (Get-WzhkMenuDigitIndex -Items $mainMenuItems -SelectedIndex 9 -Digit 1) -Message "Digit 1 on menu page two did not resolve to global item ten."

    $cloudReadiness = Get-WzhkCloudCliReadiness -RepositoryRoot $repositoryRoot
    Assert-WzhkTest -Condition ([bool]$cloudReadiness.Ready) -Message ("Offline cloud CLI readiness failed: " + $cloudReadiness.Detail)
    Assert-WzhkEqual -Expected $true -Actual ([bool]$cloudReadiness.Offline) -Message "Cloud readiness was not explicitly offline."
    Assert-WzhkEqual -Expected $false -Actual ([bool]$cloudReadiness.ProvisioningEnabled) -Message "Offline readiness enabled provisioning."
    $offlineCloudResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "readiness"
    Assert-WzhkTest -Condition ([bool]$offlineCloudResult.Ok) -Message "Provider-neutral Python readiness did not return its safe JSON envelope."
    Assert-WzhkEqual -Expected "readiness" -Actual ([string]$offlineCloudResult.Command) -Message "Cloud readiness response command was not bound to the request."
    $expectedCloudCommands = @(
        "readiness", "authorization-token", "validate-manifest", "prepare-manifest", "seal-manifest",
        "scheduler-status", "scheduler-init", "scheduler-claim", "scheduler-cancel",
        "tournament-rank", "encode-plan", "mux-plan", "import-return", "mock-worker",
        "brev-readiness", "brev-discover", "brev-list", "brev-provision-benchmark", "brev-teardown"
    )
    $cloudCommandParameter = (Get-Command Invoke-WzhkCloudCli).Parameters["Command"]
    $cloudValidateSet = @($cloudCommandParameter.Attributes | Where-Object { $_ -is [System.Management.Automation.ValidateSetAttribute] })
    Assert-WzhkEqual -Expected 1 -Actual $cloudValidateSet.Count -Message "Cloud CLI wrapper command parameter must have one explicit allowlist."
    foreach ($cloudCommand in $expectedCloudCommands) {
        Assert-WzhkTest -Condition ($cloudCommand -in @($cloudValidateSet[0].ValidValues)) -Message ("PowerShell cloud wrapper does not expose CLI command: " + $cloudCommand)
    }
    Assert-WzhkEqual -Expected $expectedCloudCommands.Count -Actual @($cloudValidateSet[0].ValidValues).Count -Message "PowerShell cloud wrapper command allowlist drifted from the tested CLI contract."
    $localMutationGuardRejected = $false
    try { $null = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "prepare-manifest" }
    catch { $localMutationGuardRejected = ($_.Exception.Message -match 'AllowLocalMutation') }
    Assert-WzhkTest -Condition $localMutationGuardRejected -Message "Cloud package bridge reached Python without the local-mutation gate."
    $statusMutationGuardRejected = $false
    try { $null = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "scheduler-status" }
    catch { $statusMutationGuardRejected = ($_.Exception.Message -match 'AllowLocalMutation') }
    Assert-WzhkTest -Condition $statusMutationGuardRejected -Message "SQLite scheduler status reached Python without the local-metadata mutation gate."
    $destructiveGuardRejected = $false
    try { $null = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "scheduler-cancel" -AllowLocalMutation }
    catch { $destructiveGuardRejected = ($_.Exception.Message -match 'AllowDestructive') }
    Assert-WzhkTest -Condition $destructiveGuardRejected -Message "Scheduler cancellation reached Python without the destructive-action gate."
    $schedulerDashboard = @(Get-WzhkCloudDashboardLines -Status ([pscustomobject]@{
        job_id = "benchmark-job-1"
        state_counts = [pscustomobject]@{ PENDING = 2; LEASED = 1; RENDERING = 1; UPLOADING = 0; VALIDATING = 1; COMPLETE = 5; FAILED = 0; RETRYABLE = 1; QUARANTINED = 0 }
        published_frames = 250
        unresolved_conflicts = 0
        cancelled = $false
        complete = $false
    }))
    Assert-WzhkTest -Condition (@($schedulerDashboard | Where-Object { $_ -eq "FLEET / JOB ID   : benchmark-job-1" }).Count -eq 1) -Message "Cloud dashboard did not display the scheduler job ID."
    Assert-WzhkTest -Condition (@($schedulerDashboard | Where-Object { $_ -eq "PROGRESS         : chunks 5 / published frames 250" }).Count -eq 1) -Message "Cloud dashboard did not map actual scheduler progress fields."
    Assert-WzhkTest -Condition (@($schedulerDashboard | Where-Object { $_ -match "CHUNK STATES.*retryable 1" }).Count -eq 1) -Message "Cloud dashboard did not display the scheduler state counts."
    Assert-WzhkTest -Condition (@($schedulerDashboard | Where-Object { $_ -match '^SNAPSHOT SOURCE.*offline scheduler' }).Count -eq 1) -Message "Cloud dashboard did not disclose its offline scheduler-only telemetry boundary."
    Assert-WzhkTest -Condition (@($schedulerDashboard | Where-Object { $_ -eq "PROVIDER         : not supplied" }).Count -eq 1) -Message "Cloud dashboard manufactured a provider value absent from scheduler status."
    $emptyCloudDashboard = @(Get-WzhkCloudDashboardLines -Status $null)
    Assert-WzhkTest -Condition (@($emptyCloudDashboard | Where-Object { $_ -match '^CHUNK STATES.*pending not supplied' }).Count -eq 1) -Message "Cloud dashboard manufactured zero-valued scheduler state without a snapshot."
    Assert-WzhkTest -Condition (@($emptyCloudDashboard | Where-Object { $_ -eq "PROGRESS         : chunks not supplied / published frames not supplied" }).Count -eq 1) -Message "Cloud dashboard manufactured progress without a scheduler snapshot."

    $packageHashForToken = "A" * 64
    $profileHashForToken = "B" * 64
    $benchmarkToken = New-WzhkBrevBenchmarkAuthorizationToken -PackageSha256 $packageHashForToken -ProfileSha256 $profileHashForToken -MaxBudget 12.5
    Assert-WzhkEqual -Expected "AUTHORIZE BREV BENCHMARK: AAAAAAAAAAAA | BBBBBBBBBBBB | MAX `$12.50" -Actual $benchmarkToken -Message "Brev benchmark token format is not exact or budget-bound."
    $cloudTokenResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "authorization-token" -Arguments @(
        "--scene-sha", ("C" * 64),
        "--profile-sha", $profileHashForToken,
        "--package-sha", $packageHashForToken,
        "--max-budget", "12.50"
    )
    Assert-WzhkTest -Condition ([bool]$cloudTokenResult.Ok) -Message "Python cloud CLI refused a valid offline authorization-token request."
    Assert-WzhkEqual -Expected $benchmarkToken -Actual ([string]$cloudTokenResult.Data.authorizationToken) -Message "PowerShell and Python benchmark token formats diverged."
    $missingConfirmation = Test-WzhkBrevBenchmarkAuthorization -ExpectedToken $benchmarkToken -EnteredToken $benchmarkToken -WorkerCount 1
    Assert-WzhkTest -Condition (-not [bool]$missingConfirmation.Valid) -Message "Brev benchmark authorization passed without both confirmations."
    $offlinePlanLock = Test-WzhkBrevBenchmarkPlanLock -ExpectedToken $benchmarkToken -EnteredToken $benchmarkToken -WorkerCount 1 -PlanLocked
    Assert-WzhkTest -Condition ([bool]$offlinePlanLock.Valid -and -not [bool]$offlinePlanLock.BillableAuthorizationRequested) -Message "Offline benchmark plan lock incorrectly required or requested billable authorization."
    $exactAuthorization = Test-WzhkBrevBenchmarkAuthorization -ExpectedToken $benchmarkToken -EnteredToken $benchmarkToken -WorkerCount 1 -PlanLocked -FinalConfirmed
    Assert-WzhkTest -Condition ([bool]$exactAuthorization.Valid) -Message ("Exact Brev benchmark authorization was rejected: " + ($exactAuthorization.Issues -join " "))
    $wrongAuthorization = Test-WzhkBrevBenchmarkAuthorization -ExpectedToken $benchmarkToken -EnteredToken ($benchmarkToken + " ") -WorkerCount 1 -PlanLocked -FinalConfirmed
    Assert-WzhkTest -Condition (-not [bool]$wrongAuthorization.Valid) -Message "Whitespace-modified Brev benchmark token was accepted."
    $multipleWorkers = Test-WzhkBrevBenchmarkAuthorization -ExpectedToken $benchmarkToken -EnteredToken $benchmarkToken -WorkerCount 4 -PlanLocked -FinalConfirmed
    Assert-WzhkTest -Condition (-not [bool]$multipleWorkers.Valid) -Message "Initial Brev benchmark accepted more than one worker."
    $provisionGuardMessage = ""
    try { $null = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "brev-provision-benchmark" }
    catch { $provisionGuardMessage = $_.Exception.Message }
    Assert-WzhkTest -Condition ($provisionGuardMessage -match 'AllowNetwork') -Message "Billable provisioning did not stop first at the explicit network gate."
    $provisionGuardMessage = ""
    try { $null = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "brev-provision-benchmark" -AllowNetwork }
    catch { $provisionGuardMessage = $_.Exception.Message }
    Assert-WzhkTest -Condition ($provisionGuardMessage -match 'AllowBillable') -Message "Billable provisioning passed the explicit billable-action gate."
    $provisionGuardMessage = ""
    try { $null = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "brev-provision-benchmark" -AllowNetwork -AllowBillable }
    catch { $provisionGuardMessage = $_.Exception.Message }
    Assert-WzhkTest -Condition ($provisionGuardMessage -match 'LOCK CLOUD PLAN') -Message "Billable provisioning passed the exact plan-lock gate."
    $provisionGuardMessage = ""
    try { $null = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "brev-provision-benchmark" -AllowNetwork -AllowBillable -PlanLocked }
    catch { $provisionGuardMessage = $_.Exception.Message }
    Assert-WzhkTest -Condition ($provisionGuardMessage -match 'PROVISION BILLABLE GPU WORKERS') -Message "Billable provisioning passed the reserved final-confirmation gate."
    $provisionGuardMessage = ""
    try {
        $null = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "brev-provision-benchmark" `
            -AllowNetwork -AllowBillable -PlanLocked -FinalConfirmed `
            -ExpectedAuthorizationToken $benchmarkToken -AuthorizationToken ($benchmarkToken + " ")
    }
    catch { $provisionGuardMessage = $_.Exception.Message }
    Assert-WzhkTest -Condition ($provisionGuardMessage -match 'authorization token') -Message "Billable provisioning passed a non-exact authorization token."
    $teardownGuardMessage = ""
    try { $null = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "brev-teardown" }
    catch { $teardownGuardMessage = $_.Exception.Message }
    Assert-WzhkTest -Condition ($teardownGuardMessage -match 'AllowNetwork') -Message "Provider teardown did not stop first at the explicit network gate."
    $teardownGuardMessage = ""
    try { $null = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "brev-teardown" -AllowNetwork }
    catch { $teardownGuardMessage = $_.Exception.Message }
    Assert-WzhkTest -Condition ($teardownGuardMessage -match 'AllowDestructive') -Message "Provider teardown passed the destructive-action gate."
    Assert-WzhkTest -Condition (@(Get-WzhkBrevCandidateGpuNames | Where-Object { $_ -match '^H200.*optional' }).Count -eq 1) -Message "H200 was not marked as an optional measured comparison."

    $builderStages = @(Get-WzhkProfileBuilderStages)
    Assert-WzhkEqual -Expected 13 -Actual $builderStages.Count -Message "Profile builder must expose exactly 13 stages."

    $temporaryBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $fixtureRoot = Join-Path $temporaryBase ("trackprompt-wzhk-mission-control-" + [Guid]::NewGuid().ToString("N"))
    $fixtureRoot = [System.IO.Path]::GetFullPath($fixtureRoot)
    $temporaryPrefix = $temporaryBase.TrimEnd('\') + '\'
    Assert-WzhkTest `
        -Condition $fixtureRoot.StartsWith($temporaryPrefix, [System.StringComparison]::OrdinalIgnoreCase) `
        -Message "The generated test fixture escaped the system temporary directory."

    $cloudFixtureRoot = New-WzhkFixtureDirectory -Path (Join-Path $fixtureRoot "cloud-offline")
    $unsealedCloudManifestPath = Join-Path $cloudFixtureRoot "cloud-package.unsealed.json"
    $sealedCloudManifestPath = Join-Path $cloudFixtureRoot "cloud-package.json"
    $cloudManifestFixture = [pscustomobject][ordered]@{
        schemaVersion = "1.0.0"
        kind = "trackprompt-cloud-render-package"
        identities = [pscustomobject][ordered]@{
            sceneSha256 = "A" * 64
            profileSha256 = "B" * 64
            packageSha256 = "C" * 64
        }
        frameRange = [pscustomobject][ordered]@{ start = 1; end = 2 }
        privateAudioIncluded = $false
        audioMuxLocation = "LOCAL_ONLY"
        blenderVersion = "5.2.0"
        resolution = [pscustomobject][ordered]@{ width = 16; height = 16 }
        image = [pscustomobject][ordered]@{ format = "PNG"; bitDepth = 8; colorMode = "RGB"; extension = "png" }
        files = @([pscustomobject][ordered]@{ path = "scene/synthetic.blend"; sha256 = "D" * 64; sizeBytes = 100 })
    }
    Write-WzhkFixtureText -Path $unsealedCloudManifestPath -Value ($cloudManifestFixture | ConvertTo-Json -Depth 20)
    $sealResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "seal-manifest" -Arguments @(
        "--input", $unsealedCloudManifestPath,
        "--output", $sealedCloudManifestPath
    ) -AllowLocalMutation
    Assert-WzhkTest -Condition ([bool]$sealResult.Ok -and (Test-Path -LiteralPath $sealedCloudManifestPath -PathType Leaf)) -Message "PowerShell wrapper did not seal the synthetic offline cloud manifest."
    $validateCloudManifestResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "validate-manifest" -Arguments @("--path", $sealedCloudManifestPath)
    Assert-WzhkTest -Condition ([bool]$validateCloudManifestResult.Ok) -Message "PowerShell wrapper rejected the sealed synthetic cloud manifest."
    Assert-WzhkEqual -Expected 1 -Actual ([int]$validateCloudManifestResult.Data.frameRange.start) -Message "Cloud manifest frame start drifted through the wrapper."
    Assert-WzhkEqual -Expected 2 -Actual ([int]$validateCloudManifestResult.Data.frameRange.end) -Message "Cloud manifest frame end drifted through the wrapper."

    $cloudSchedulerDatabase = Join-Path $cloudFixtureRoot "scheduler.sqlite3"
    $schedulerInitResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "scheduler-init" -Arguments @(
        "--database", $cloudSchedulerDatabase,
        "--job-id", "offline-job-1",
        "--package-manifest", $sealedCloudManifestPath,
        "--frames-per-chunk", "1",
        "--max-attempts", "3"
    ) -AllowLocalMutation
    Assert-WzhkTest -Condition ([bool]$schedulerInitResult.Ok) -Message "PowerShell wrapper could not initialize the offline scheduler from the sealed manifest."
    Assert-WzhkEqual -Expected 2 -Actual ([int]$schedulerInitResult.Data.chunkCount) -Message "Offline scheduler initialized the wrong chunk count."
    $mockStorageRoot = Join-Path $cloudFixtureRoot "mock-storage"
    $mockWorkerResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "mock-worker" -Arguments @(
        "--package-manifest", $sealedCloudManifestPath,
        "--database", $cloudSchedulerDatabase,
        "--storage-root", $mockStorageRoot,
        "--job-id", "offline-job-1",
        "--worker-id", "mock-worker-1",
        "--run-until-idle",
        "--no-work-timeout-seconds", "0"
    ) -AllowLocalMutation
    Assert-WzhkTest -Condition ([bool]$mockWorkerResult.Ok -and [bool]$mockWorkerResult.Data.mock) -Message "PowerShell wrapper did not complete the explicit bounded mock-only worker round trip."
    $offlineStatusResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "scheduler-status" -Arguments @("--database", $cloudSchedulerDatabase, "--job-id", "offline-job-1") -AllowLocalMutation
    Assert-WzhkTest -Condition ([bool]$offlineStatusResult.Ok -and [bool]$offlineStatusResult.Data.complete) -Message "Offline scheduler was not complete after the mock worker drained its queue."
    Assert-WzhkEqual -Expected 2 -Actual ([int]$offlineStatusResult.Data.published_frames) -Message "Mock worker published the wrong synthetic frame count."

    $firstChunkReturn = @(
        Get-ChildItem -LiteralPath $mockStorageRoot -Recurse -Directory -Filter "chunk-000001-000001" |
            Select-Object -First 1
    )
    Assert-WzhkEqual -Expected 1 -Actual $firstChunkReturn.Count -Message "Mock worker did not produce the expected first per-chunk return directory."
    $firstChunkManifest = Join-Path $firstChunkReturn[0].FullName "chunk-output-manifest.json"
    $cloudImportQuarantine = Join-Path $cloudFixtureRoot "return-quarantine"
    $cloudImportedFrames = Join-Path $cloudFixtureRoot "imported-frames"
    $cloudImportResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "import-return" -Arguments @(
        "--returned", $mockStorageRoot,
        "--quarantine-root", $cloudImportQuarantine,
        "--output-frames", $cloudImportedFrames,
        "--manifest", $firstChunkManifest,
        "--package-manifest", $sealedCloudManifestPath,
        "--frame-start", "1",
        "--frame-end", "1"
    ) -AllowLocalMutation -AllowDestructive
    Assert-WzhkTest -Condition ([bool]$cloudImportResult.Ok) -Message ("PowerShell wrapper rejected a valid per-chunk return from a larger package range. " + $cloudImportResult.Raw)
    Assert-WzhkEqual -Expected 1 -Actual @($cloudImportResult.Data.publishedFrames).Count -Message "Per-chunk return import published the wrong frame count."
    Assert-WzhkTest -Condition (Test-Path -LiteralPath (Join-Path $cloudImportedFrames "frame_000001.png") -PathType Leaf) -Message "Per-chunk return import did not publish the validated frame."

    $tournamentInputPath = Join-Path $cloudFixtureRoot "benchmark-results.json"
    $tournamentFixture = [pscustomobject]@{ benchmarks = @([pscustomobject]@{
        provider = "mock"; offerId = "mock-l40s"; gpuName = "L40S"; region = "test"; hourlyPrice = "1.00";
        secondsPerFrame = "10"; p90SecondsPerFrame = "12"; validatedFrames = 2; visualPassed = $true;
        technicalPassed = $true; softwareRendering = $false; stable = $true
    }) }
    Write-WzhkFixtureText -Path $tournamentInputPath -Value ($tournamentFixture | ConvertTo-Json -Depth 20)
    $rankResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "tournament-rank" -Arguments @("--input", $tournamentInputPath)
    Assert-WzhkTest -Condition ([bool]$rankResult.Ok -and @($rankResult.Data.ranked).Count -eq 1) -Message "Offline tournament ranking did not return the one eligible synthetic result."
    $encodePlanResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "encode-plan" -Arguments @(
        "--frame-pattern", (Join-Path $cloudFixtureRoot "frame_%06d.png"),
        "--frame-start", "1", "--frame-end", "2", "--verified-frames", "1,2", "--fps", "30",
        "--output", (Join-Path $cloudFixtureRoot "video-only.mp4")
    )
    Assert-WzhkTest -Condition ([bool]$encodePlanResult.Ok -and -not [bool]$encodePlanResult.Data.audioIncluded) -Message "Offline cloud encode plan included audio or failed through PowerShell."
    $muxPlanResult = Invoke-WzhkCloudCli -RepositoryRoot $repositoryRoot -Command "mux-plan" -Arguments @(
        "--video-only", (Join-Path $cloudFixtureRoot "video-only.mp4"),
        "--private-audio", (Join-Path $cloudFixtureRoot "private.wav"),
        "--output", (Join-Path $cloudFixtureRoot "final.mp4")
    )
    Assert-WzhkTest -Condition ([bool]$muxPlanResult.Ok -and [string]$muxPlanResult.Data.audioLocation -eq "LOCAL_ONLY") -Message "Offline local-audio mux plan did not preserve the privacy boundary."

    $testOutputRoot = New-WzhkFixtureDirectory -Path (Join-Path $fixtureRoot "test-output")
    $olderPrep = New-WzhkFixtureDirectory -Path (Join-Path $testOutputRoot "final-render-prep-20000101-000000")
    $prepPath = New-WzhkFixtureDirectory -Path (Join-Path $testOutputRoot "final-render-prep-20000102-000000")
    [System.IO.Directory]::SetLastWriteTimeUtc($olderPrep, [DateTime]::UtcNow.AddMinutes(-10))
    [System.IO.Directory]::SetLastWriteTimeUtc($prepPath, [DateTime]::UtcNow)

    $prepCandidates = @(Get-WzhkPrepCandidates -TestOutputRoot $testOutputRoot)
    Assert-WzhkEqual -Expected 2 -Actual $prepCandidates.Count -Message "Preparation-package discovery returned the wrong count."
    Assert-WzhkEqual -Expected $prepPath -Actual $prepCandidates[0].FullName -Message "Preparation packages were not sorted newest first."

    $approvedDirectory = New-WzhkFixtureDirectory -Path (Join-Path $prepPath "approved-candidate")
    $outsideScene = Join-Path $prepPath "outside-candidate.blend"
    Write-WzhkFixtureText -Path $outsideScene -Value "outside"
    $scenePath = Join-Path $approvedDirectory "trip-final-candidate.blend"
    [System.IO.File]::WriteAllBytes($scenePath, [System.Text.Encoding]::ASCII.GetBytes("abc"))

    $manifestPath = Join-Path $approvedDirectory "trip-final-candidate.manifest.json"
    $manifestJson = @'
{
  "scene": {
    "objectCount": 12,
    "materialCount": 4,
    "collectionCount": 3,
    "fCurveCount": 8,
    "audioBusFCurveCount": 2,
    "frameStart": 1,
    "frameEnd": 4,
    "fps": 30,
    "presetSummary": { "macroStateCount": 5 }
  }
}
'@
    Write-WzhkFixtureText -Path $manifestPath -Value $manifestJson

    $profilePath = Join-Path $prepPath "render-profile.final.json"
    $profileJson = @'
{
  "profileId": "fixture-1440p",
  "render": {
    "width": 2560,
    "height": 1440,
    "fps": 30,
    "chunkSize": 2,
    "imageFormat": "PNG"
  },
  "frameRange": {
    "start": 1,
    "end": 4
  }
}
'@
    Write-WzhkFixtureText -Path $profilePath -Value $profileJson
    $brokenProfilePath = Join-Path $prepPath "render-profile-broken.json"
    Write-WzhkFixtureText -Path $brokenProfilePath -Value "{ broken json"
    $excludedManifestProfile = Join-Path $prepPath "render-profile.manifest.json"
    Write-WzhkFixtureText -Path $excludedManifestProfile -Value $profileJson

    $approvedScene = Find-WzhkApprovedScene -PrepPath $prepPath
    Assert-WzhkEqual -Expected $scenePath -Actual $approvedScene -Message "Approved-scene discovery did not prefer approved-candidate."

    $sceneHash = Get-WzhkHashPrefix -Path $scenePath
    Assert-WzhkEqual -Expected "BA7816BF8F01" -Actual $sceneHash -Message "The scene SHA-256 prefix is incorrect."
    Assert-WzhkEqual `
        -Expected "BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD" `
        -Actual (Get-WzhkHashPrefix -Path $scenePath -Length 64) `
        -Message "The full scene SHA-256 hash is incorrect."
    Assert-WzhkEqual -Expected "MISSING" -Actual (Get-WzhkHashPrefix -Path (Join-Path $fixtureRoot "missing.blend")) -Message "A missing file did not return the MISSING hash marker."

    $sceneInfo = Get-WzhkSceneInfo -Path $scenePath
    Assert-WzhkEqual -Expected $manifestPath -Actual $sceneInfo.ManifestPath -Message "The exact scene manifest was not selected."
    Assert-WzhkEqual -Expected 12 -Actual $sceneInfo.ObjectCount -Message "Scene object-count parsing failed."
    Assert-WzhkEqual -Expected 5 -Actual $sceneInfo.MacroStateCount -Message "Scene macro-state parsing failed."
    Assert-WzhkEqual -Expected 2 -Actual $sceneInfo.AudioBusCurveCount -Message "Scene audio-bus parsing failed."
    Assert-WzhkEqual -Expected 4 -Actual $sceneInfo.FrameEnd -Message "Scene frame-range parsing failed."

    $profileInfo = Get-WzhkProfileInfo -Path $profilePath
    Assert-WzhkTest -Condition ([bool]$profileInfo.ValidJson) -Message "The synthetic render profile was not valid JSON."
    Assert-WzhkEqual -Expected "fixture-1440p" -Actual $profileInfo.Name -Message "Profile-name parsing failed."
    Assert-WzhkEqual -Expected 2560 -Actual $profileInfo.Width -Message "Profile-width parsing failed."
    Assert-WzhkEqual -Expected 1440 -Actual $profileInfo.Height -Message "Profile-height parsing failed."
    Assert-WzhkEqual -Expected 30 -Actual $profileInfo.Fps -Message "Profile-fps parsing failed."
    Assert-WzhkEqual -Expected 4 -Actual $profileInfo.FrameEnd -Message "Profile frame-range parsing failed."
    Assert-WzhkEqual -Expected 2 -Actual $profileInfo.ChunkSize -Message "Profile chunk-size parsing failed."
    Assert-WzhkEqual -Expected "PNG" -Actual $profileInfo.Format -Message "Profile-format parsing failed."
    Assert-WzhkEqual -Expected "1440p-30-sdr" -Actual $profileInfo.Slug -Message "Profile-slug classification failed."

    $brokenProfileInfo = Get-WzhkProfileInfo -Path $brokenProfilePath
    Assert-WzhkTest -Condition (-not [bool]$brokenProfileInfo.ValidJson) -Message "Malformed profile JSON was accepted."

    $profileCandidates = @(Get-WzhkProfileCandidates -PrepPath $prepPath)
    Assert-WzhkEqual -Expected 2 -Actual $profileCandidates.Count -Message "Profile discovery did not include exactly the valid and malformed non-manifest profiles."
    Assert-WzhkEqual -Expected $profilePath -Actual $profileCandidates[0].Path -Message "render-profile.final.json was not prioritized."

    $profileHash = Get-WzhkHashPrefix -Path $profilePath
    $authorizationToken = (
        "AUTHORIZE FULL RENDER: TRIP TO ANDROMEDA | SPACE-JOURNEY | FIXTURE | SCENE " +
        $sceneHash + " | PROFILE " + $profileHash
    )
    Write-WzhkFixtureText -Path (Join-Path $prepPath "authorization-request.txt") -Value $authorizationToken

    $foundToken = Find-WzhkAuthorizationToken `
        -PrepPath $prepPath `
        -ScenePath $scenePath `
        -ProfilePath $profilePath
    Assert-WzhkEqual -Expected $authorizationToken -Actual $foundToken -Message "The matching authorization token was not found."

    $mismatchedToken = Find-WzhkAuthorizationToken `
        -PrepPath $prepPath `
        -ScenePath $scenePath `
        -ProfilePath $brokenProfilePath
    Assert-WzhkEqual -Expected "" -Actual $mismatchedToken -Message "Authorization matching accepted the wrong profile hash."

    $finalOutputRoot = New-WzhkFixtureDirectory -Path (Join-Path $fixtureRoot "final-output")
    $outputPath = New-WzhkFixtureDirectory -Path (Join-Path $finalOutputRoot "trip-to-andromeda-space-journey-1440p-30-sdr-fixture")
    $publishedA = New-WzhkFixtureDirectory -Path (Join-Path $outputPath "frames\chunk-a")
    $publishedB = New-WzhkFixtureDirectory -Path (Join-Path $outputPath "frames\chunk-b")
    $inflightFrames = New-WzhkFixtureDirectory -Path (Join-Path $outputPath "checkpoints\.inflight-fixture\frames")
    New-WzhkFixtureFile -Path (Join-Path $publishedA "frame_000001.png")
    New-WzhkFixtureFile -Path (Join-Path $publishedA "frame_000002.png")
    New-WzhkFixtureFile -Path (Join-Path $publishedB "frame_000002.exr")
    New-WzhkFixtureFile -Path (Join-Path $inflightFrames "frame_000002.png")
    New-WzhkFixtureFile -Path (Join-Path $inflightFrames "frame_000003.png")
    New-WzhkFixtureFile -Path (Join-Path $publishedB "not-a-frame.txt")

    $outputStats = Get-WzhkOutputStats -OutputPath $outputPath -TotalFrames 4
    Assert-WzhkEqual -Expected 2 -Actual $outputStats.Published -Message "Published-frame progress did not de-duplicate frame numbers."
    Assert-WzhkEqual -Expected 2 -Actual $outputStats.Inflight -Message "In-flight progress inspection returned the wrong count."
    Assert-WzhkEqual -Expected 3 -Actual $outputStats.LatestFrame -Message "Latest-frame progress inspection returned the wrong frame."
    Assert-WzhkEqual -Expected 50.0 -Actual ([double]$outputStats.Percent) -Message "Published progress percentage is incorrect."

    $missingOutputStats = Get-WzhkOutputStats -OutputPath (Join-Path $fixtureRoot "missing-output") -TotalFrames 4
    Assert-WzhkEqual -Expected 0 -Actual $missingOutputStats.Published -Message "A missing output reported published frames."
    Assert-WzhkEqual -Expected 0 -Actual $missingOutputStats.Inflight -Message "A missing output reported in-flight frames."
    Assert-WzhkEqual -Expected 0 -Actual $missingOutputStats.LatestFrame -Message "A missing output reported a latest frame."

    $otherOutput = New-WzhkFixtureDirectory -Path (Join-Path $finalOutputRoot "unrelated-output")
    $null = $otherOutput
    $outputCandidates = @(Get-WzhkOutputCandidates -FinalOutputRoot $finalOutputRoot -ProfileSlug "1440p-30-sdr")
    Assert-WzhkEqual -Expected 1 -Actual $outputCandidates.Count -Message "Profile-specific output discovery returned unrelated directories."
    Assert-WzhkEqual -Expected $outputPath -Actual $outputCandidates[0].FullName -Message "Profile-specific output discovery selected the wrong directory."

    $newOutputPath = New-WzhkOutputPath -FinalOutputRoot $finalOutputRoot -ProfileSlug "1440p-30-sdr"
    Assert-WzhkTest `
        -Condition ([System.IO.Path]::GetFullPath($newOutputPath).StartsWith(($finalOutputRoot.TrimEnd('\') + '\'), [System.StringComparison]::OrdinalIgnoreCase)) `
        -Message "The generated output path escaped the fixture final-output directory."
    Assert-WzhkTest `
        -Condition ((Split-Path -Leaf $newOutputPath) -match '^trip-to-andromeda-space-journey-1440p-30-sdr-\d{8}-\d{6}$') `
        -Message "The generated output path did not use the expected timestamped name."
    Assert-WzhkTest -Condition (-not (Test-Path -LiteralPath $newOutputPath)) -Message "New-WzhkOutputPath unexpectedly created an output directory."
    $customPatternOutput = New-WzhkOutputPath `
        -FinalOutputRoot $finalOutputRoot `
        -ProfileSlug "ULTRA-ID" `
        -ProjectSlug "My Project" `
        -PresetSlug "Space Journey" `
        -ResolutionSlug "4k-30-sdr" `
        -DirectoryPattern "{project}.{profile}-{resolution}-{timestamp}"
    Assert-WzhkTest `
        -Condition ((Split-Path -Leaf $customPatternOutput) -match '^my-project\.ultra-id-4k-30-sdr-\d{8}-\d{6}$') `
        -Message "Custom output directory tokens did not expand deterministically."
    foreach ($unsafePattern in @("_-{timestamp}", "{unknown}-{timestamp}", "{project}/{timestamp}")) {
        $patternRejected = $false
        try { $null = New-WzhkOutputPath -FinalOutputRoot $finalOutputRoot -ProfileSlug "fixture" -DirectoryPattern $unsafePattern }
        catch { $patternRejected = $true }
        Assert-WzhkTest -Condition $patternRejected -Message ("Unsafe output directory pattern was accepted: " + $unsafePattern)
    }

    # Reusable render-profile domain tests. All fixtures are tiny text files; no Blender,
    # browser, renderer, or production output process is invoked by this suite.
    $templateIds = @(
        "FULL-HD-FAST",
        "1440P-BALANCED",
        "4K-BALANCED",
        "4K-HIGH",
        "4K-ULTRA",
        "CUSTOM"
    )
    $templates = @(Get-WzhkRenderProfileTemplates)
    Assert-WzhkEqual -Expected 6 -Actual $templates.Count -Message "The builder must expose six starting templates."
    foreach ($templateId in $templateIds) {
        Assert-WzhkEqual `
            -Expected 1 `
            -Actual @($templates | Where-Object { $_.Id -eq $templateId }).Count `
            -Message ("Missing profile template: " + $templateId)
    }

    $profileStore = New-WzhkFixtureDirectory -Path (Join-Path $fixtureRoot "render-profiles\fixture-project")
    $profileScenePath = Join-Path $fixtureRoot "profile-approved-scene.blend"
    [System.IO.File]::WriteAllBytes($profileScenePath, [System.Text.Encoding]::ASCII.GetBytes("approved-scene-v1"))
    $profileManifestPath = Join-Path $fixtureRoot "profile-approved-scene.manifest.json"
    Write-WzhkFixtureText -Path $profileManifestPath -Value '{"scene":{"frameStart":1,"frameEnd":12,"fps":30}}'
    $profileSceneHash = Get-WzhkFileSha256 -Path $profileScenePath
    $profileManifestHash = Get-WzhkFileSha256 -Path $profileManifestPath

    $templateProfiles = @{}
    foreach ($templateId in $templateIds) {
        $candidate = New-WzhkRenderProfile `
            -TemplateId $templateId `
            -DisplayName ("Fixture " + $templateId) `
            -ProfileId ("fixture-" + $templateId.ToLowerInvariant()) `
            -Project "fixture-project" `
            -Preset "space-journey" `
            -ApprovedScenePath $profileScenePath `
            -ApprovedSceneSha256 $profileSceneHash `
            -SceneManifestPath $profileManifestPath `
            -SceneManifestSha256 $profileManifestHash `
            -FrameStart 1 `
            -FrameEnd 12 `
            -Fps 30
        $candidateValidation = Test-WzhkRenderProfile -Profile $candidate -VerifyFiles
        Assert-WzhkTest -Condition ([bool]$candidateValidation.Valid) -Message ("Template profile was invalid: " + $templateId + " " + ($candidateValidation.Errors -join " "))
        $templateProfiles[$templateId] = $candidate
    }
    Assert-WzhkEqual -Expected 1920 -Actual $templateProfiles["FULL-HD-FAST"].resolution.width -Message "FULL HD template width is wrong."
    Assert-WzhkEqual -Expected 2560 -Actual $templateProfiles["1440P-BALANCED"].resolution.width -Message "1440p template width is wrong."
    Assert-WzhkEqual -Expected 3840 -Actual $templateProfiles["4K-BALANCED"].resolution.width -Message "4K template is not native UHD."
    Assert-WzhkEqual -Expected "PNG" -Actual $templateProfiles["4K-ULTRA"].imageSequence.format -Message "4K Ultra must be immediately renderer-valid."
    Assert-WzhkTest -Condition (-not [bool]$templateProfiles["4K-ULTRA"].render.highQualityNormals) -Message "Unsupported Blender 5.2 high-quality normals were enabled."
    $preSaveSummaryLines = @(Get-WzhkProfileSummaryLines -Profile $templateProfiles["1440P-BALANCED"])
    Assert-WzhkTest `
        -Condition (@($preSaveSummaryLines | Where-Object { $_ -match 'SAVED-FILE SHA-12:\s+CALCULATED ON SAVE' }).Count -eq 1) `
        -Message "The pre-save review left the saved-file hash field blank or misleading."
    Assert-WzhkTest `
        -Condition (@($preSaveSummaryLines | Where-Object { $_ -match 'ENCODING\s+: master disabled.*delivery disabled.*audio disabled' }).Count -eq 1) `
        -Message "The review advertised an audio codec while both encode outputs were disabled."
    foreach ($requiredSummaryLabel in @("SHADOWS", "RAY TRACING", "VOLUMETRICS", "IMAGE CONTRACT", "COLOR DETAILS", "FOG GLOW", "OUTPUT POLICY", "DELIVERY DETAILS", "DASHBOARD METRICS", "SAFETY CONTRACT")) {
        Assert-WzhkTest -Condition (@($preSaveSummaryLines | Where-Object { $_ -match ('^' + [regex]::Escape($requiredSummaryLabel)) }).Count -eq 1) -Message ("Final review omitted resolved setting group: " + $requiredSummaryLabel)
    }

    $compareRight = Set-WzhkProfileValue -Profile $templateProfiles["1440P-BALANCED"] -PropertyPath "description" -Value "Comparison-specific description"
    $compareRight = Set-WzhkProfileValue -Profile $compareRight -PropertyPath "render.qualityMode" -Value "HIGH"
    $comparisonPaths = @(Compare-WzhkRenderProfiles -Left $templateProfiles["1440P-BALANCED"] -Right $compareRight | ForEach-Object { $_.Path })
    Assert-WzhkTest -Condition ($comparisonPaths -contains "description") -Message "Profile comparison omitted description."
    Assert-WzhkTest -Condition ($comparisonPaths -contains "render.qualityMode") -Message "Profile comparison omitted render.qualityMode."

    $custom = $templateProfiles["CUSTOM"]
    $custom = Set-WzhkProfileValue -Profile $custom -PropertyPath "resolution.width" -Value 2000
    $custom = Set-WzhkProfileValue -Profile $custom -PropertyPath "resolution.height" -Value 1000
    $custom = Set-WzhkProfileValue -Profile $custom -PropertyPath "resolution.displayAspect" -Value "2:1"
    $custom = Set-WzhkProfileValue -Profile $custom -PropertyPath "aspect.display" -Value "2:1"
    $customValidation = Test-WzhkRenderProfile -Profile $custom
    Assert-WzhkTest -Condition ([bool]$customValidation.Valid) -Message ("A valid custom resolution was rejected: " + ($customValidation.Errors -join " "))
    Assert-WzhkTest -Condition (@($customValidation.Warnings).Count -gt 0) -Message "A non-16:9 custom resolution produced no warning."

    $anamorphicSixteenNine = Set-WzhkProfileValue -Profile $custom -PropertyPath "resolution.width" -Value 1440
    $anamorphicSixteenNine = Set-WzhkProfileValue -Profile $anamorphicSixteenNine -PropertyPath "resolution.height" -Value 1080
    $anamorphicSixteenNine = Set-WzhkProfileValue -Profile $anamorphicSixteenNine -PropertyPath "resolution.pixelAspectX" -Value 4.0
    $anamorphicSixteenNine = Set-WzhkProfileValue -Profile $anamorphicSixteenNine -PropertyPath "resolution.pixelAspectY" -Value 3.0
    $anamorphicSixteenNine = Update-WzhkBuilderDerivedValues -Profile $anamorphicSixteenNine
    $anamorphicValidation = Test-WzhkRenderProfile -Profile $anamorphicSixteenNine
    Assert-WzhkEqual -Expected "16:9" -Actual $anamorphicSixteenNine.resolution.displayAspect -Message "Anamorphic dimensions did not resolve their display aspect with pixel aspect."
    Assert-WzhkEqual -Expected 0 -Actual @($anamorphicValidation.Warnings | Where-Object { $_ -match "aspect ratio" }).Count -Message "An anamorphic 16:9 display aspect produced a false warning."

    $nonSquareWide = Set-WzhkProfileValue -Profile $templateProfiles["FULL-HD-FAST"] -PropertyPath "resolution.pixelAspectX" -Value 1.2
    $nonSquareWide = Update-WzhkBuilderDerivedValues -Profile $nonSquareWide
    $nonSquareWideValidation = Test-WzhkRenderProfile -Profile $nonSquareWide
    Assert-WzhkTest -Condition ($nonSquareWide.resolution.displayAspect -ne "16:9") -Message "Non-square pixels were ignored in the builder display-aspect label."
    Assert-WzhkTest -Condition (@($nonSquareWideValidation.Warnings | Where-Object { $_ -match "pixel aspect" }).Count -eq 1) -Message "Non-square pixels that change display aspect produced no warning."

    $invalidWidth = Set-WzhkProfileValue -Profile $custom -PropertyPath "resolution.width" -Value 0
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $invalidWidth).Valid) -Message "Invalid width was accepted."
    $oddWidth = Set-WzhkProfileValue -Profile $custom -PropertyPath "resolution.width" -Value 2559
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $oddWidth).Valid) -Message "Odd output width was accepted despite the encoder contract."
    $invalidHeight = Set-WzhkProfileValue -Profile $custom -PropertyPath "resolution.height" -Value 0
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $invalidHeight).Valid) -Message "Invalid height was accepted."
    $invalidFps = Set-WzhkProfileValue -Profile $custom -PropertyPath "timeline.fps" -Value 0
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $invalidFps).Valid) -Message "Invalid FPS was accepted."
    $invalidRange = Set-WzhkProfileValue -Profile $custom -PropertyPath "timeline.frameStart" -Value 20
    $invalidRange = Set-WzhkProfileValue -Profile $invalidRange -PropertyPath "timeline.frameEnd" -Value 10
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $invalidRange).Valid) -Message "Invalid frame range was accepted."
    $invalidChunk = Set-WzhkProfileValue -Profile $custom -PropertyPath "chunking.framesPerChunk" -Value 0
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $invalidChunk).Valid) -Message "Invalid chunk size was accepted."
    $invalidFormat = Set-WzhkProfileValue -Profile $custom -PropertyPath "imageSequence.format" -Value "JPEG"
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $invalidFormat).Valid) -Message "Unsupported image format was accepted."
    $invalidNormals = Set-WzhkProfileValue -Profile $custom -PropertyPath "render.highQualityNormals" -Value $true
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $invalidNormals).Valid) -Message "Unsupported Blender 5.2 setting was accepted."
    foreach ($invalidResolvedSetting in @(
        @("render.shadowRayCount", "not-an-integer"),
        @("render.shadowResolutionScale", 1.1),
        @("render.rayTracing", "yes"),
        @("render.rayTracingMethod", "BROKEN"),
        @("render.volumetricTileSize", "3"),
        @("render.volumetricSamples", 0),
        @("render.volumetricShadowSamples", 129),
        @("render.volumetricRayDepth", 17),
        @("render.volumetricShadows", "yes"),
        @("render.filmTransparent", $true),
        @("compositor.fogGlowQuality", "BROKEN"),
        @("compositor.fogGlowThreshold", 101.0),
        @("compositor.fogGlowStrength", 1.1),
        @("compositor.fogGlowSize", -0.1),
        @("compositor.fogGlowIterations", 6)
    )) {
        $invalidResolvedProfile = Set-WzhkProfileValue -Profile $custom -PropertyPath ([string]$invalidResolvedSetting[0]) -Value $invalidResolvedSetting[1]
        Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $invalidResolvedProfile).Valid) -Message ("Renderer-rejected setting passed profile validation: " + [string]$invalidResolvedSetting[0])
    }
    $disabledCompositorGlow = Set-WzhkProfileValue -Profile $custom -PropertyPath "compositor.enabled" -Value $false
    $disabledCompositorGlow = Set-WzhkProfileValue -Profile $disabledCompositorGlow -PropertyPath "render.useCompositing" -Value $false
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $disabledCompositorGlow).Valid) -Message "Fog Glow remained enabled while the compositor was disabled."
    $invalidDirectoryPattern = Set-WzhkProfileValue -Profile $custom -PropertyPath "output.directoryPattern" -Value "_-{timestamp}"
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $invalidDirectoryPattern).Valid) -Message "Profile validator accepted a directory pattern the generator rejects."

    $legacyProfile = $templateProfiles["1440P-BALANCED"] | ConvertTo-Json -Depth 100 | ConvertFrom-Json
    $legacyProfile.schemaVersion = "1.0.0"
    foreach ($legacyOptionalRenderField in @("rayTracingMethod", "volumetricTileSize", "volumetricSamples", "volumetricShadowSamples", "volumetricRayDepth", "volumetricShadows")) {
        $legacyProfile.render.PSObject.Properties.Remove($legacyOptionalRenderField)
    }
    $legacyProfile.PSObject.Properties.Remove("compositor")
    $legacyProfile.profileSha256 = ""
    $legacyProfile.integrity.profileSha256 = ""
    $legacyValidation = Test-WzhkRenderProfile -Profile $legacyProfile
    Assert-WzhkTest -Condition ([bool]$legacyValidation.Valid) -Message ("A structurally valid schema 1.0 profile was rejected by schema 1.1-only requirements: " + (@($legacyValidation.Errors) -join " "))
    foreach ($mandatoryResumeField in @("resumeEnabled", "verifyExistingFrames", "atomicChunkCommit", "stopOnValidationFailure")) {
        $unsafeResumeProfile = Set-WzhkProfileValue -Profile $custom -PropertyPath ("production." + $mandatoryResumeField) -Value $false
        Assert-WzhkTest `
            -Condition (-not [bool](Test-WzhkRenderProfile -Profile $unsafeResumeProfile).Valid) `
            -Message ("Unsafe production." + $mandatoryResumeField + "=false was accepted.")
    }
    $invalidColor = Set-WzhkProfileValue -Profile $custom -PropertyPath "colorManagement.viewTransform" -Value "Standard"
    $invalidColor = Set-WzhkProfileValue -Profile $invalidColor -PropertyPath "colorManagement.look" -Value "AgX - Medium High Contrast"
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $invalidColor).Valid) -Message "An unavailable Blender 5.2 view/look combination was accepted."

    Assert-WzhkTest `
        -Condition (-not [bool]$templateProfiles["1440P-BALANCED"].encoding.master.enabled -and -not [bool]$templateProfiles["1440P-BALANCED"].encoding.delivery.enabled) `
        -Message "An unbound profile unexpectedly enabled final encoding."
    $unboundEncoding = Set-WzhkProfileValue -Profile $templateProfiles["1440P-BALANCED"] -PropertyPath "encoding.master.enabled" -Value $true
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $unboundEncoding).Valid) -Message "Encoding was enabled without exact approved audio identity."

    $openExrNoEncode = Set-WzhkProfileValue -Profile $custom -PropertyPath "imageSequence.format" -Value "OPEN_EXR"
    $openExrNoEncode = Set-WzhkProfileValue -Profile $openExrNoEncode -PropertyPath "imageSequence.extension" -Value "exr"
    $openExrNoEncode = Set-WzhkProfileValue -Profile $openExrNoEncode -PropertyPath "imageSequence.bitDepth" -Value 16
    $openExrNoEncode = Set-WzhkProfileValue -Profile $openExrNoEncode -PropertyPath "imageSequence.compression" -Value "ZIP"
    $openExrNoEncode = Set-WzhkProfileValue -Profile $openExrNoEncode -PropertyPath "imageSequence.filenamePattern" -Value "frame_%06d.exr"
    $openExrNoEncode = Set-WzhkProfileValue -Profile $openExrNoEncode -PropertyPath "imageSequence.colorManagement.displayTransformBaked" -Value $false
    $openExrNoEncode = Set-WzhkProfileValue -Profile $openExrNoEncode -PropertyPath "encoding.master.enabled" -Value $false
    $openExrNoEncode = Set-WzhkProfileValue -Profile $openExrNoEncode -PropertyPath "encoding.delivery.enabled" -Value $false
    Assert-WzhkTest -Condition ([bool](Test-WzhkRenderProfile -Profile $openExrNoEncode).Valid) -Message "A valid scene-linear OpenEXR profile with encoding disabled was rejected."

    $safeName = ConvertTo-WzhkSafeProfileName -Name '  Bad<>:"/\|?* Profile...  '
    Assert-WzhkEqual -Expected "Bad Profile" -Actual $safeName -Message "Profile display-name sanitization failed."
    $safeSlug = ConvertTo-WzhkProfileSlug -Name "Trip to Andromeda - 4K Balanced"
    Assert-WzhkEqual -Expected "trip-to-andromeda-4k-balanced" -Actual $safeSlug -Message "Profile slug sanitization failed."

    $savedProfilePath = Join-Path $profileStore "1440p-balanced.json"
    $saveOne = Save-WzhkRenderProfile -Profile $templateProfiles["1440P-BALANCED"] -Path $savedProfilePath
    Assert-WzhkTest -Condition (Test-Path -LiteralPath $saveOne.Path -PathType Leaf) -Message "Atomic profile save did not produce JSON."
    Assert-WzhkTest -Condition (Test-Path -LiteralPath $saveOne.SummaryPath -PathType Leaf) -Message "Profile save did not produce the sibling summary."
    Assert-WzhkEqual -Expected 0 -Actual @(Get-ChildItem -LiteralPath $profileStore -File -Filter "*.tmp" -Force).Count -Message "Atomic save left a temporary file."

    $duplicatePathRejected = $false
    try { $null = Save-WzhkRenderProfile -Profile $templateProfiles["1440P-BALANCED"] -Path $savedProfilePath }
    catch { $duplicatePathRejected = $true }
    Assert-WzhkTest -Condition $duplicatePathRejected -Message "Duplicate profile path overwrote without explicit -Force."
    $duplicateIdRejected = $false
    try {
        $null = Save-WzhkRenderProfile `
            -Profile $templateProfiles["1440P-BALANCED"] `
            -Path (Join-Path $profileStore "same-id-different-file.json")
    }
    catch { $duplicateIdRejected = $true }
    Assert-WzhkTest -Condition $duplicateIdRejected -Message "Duplicate stable/profile ID was accepted under another filename."

    $rawHashBefore = Get-WzhkFileSha256 -Path $savedProfilePath
    $unchangedSave = Save-WzhkRenderProfile -Profile $saveOne.Profile -Path $savedProfilePath -Force
    Assert-WzhkEqual -Expected $rawHashBefore -Actual $unchangedSave.FileSha256 -Message "Unchanged atomic save changed the exact saved-file hash."
    Assert-WzhkEqual -Expected $saveOne.ContentSha256 -Actual $unchangedSave.ContentSha256 -Message "Informational profile hash was not stable."
    $loadedProfile = Import-WzhkRenderProfile -Path $savedProfilePath -VerifyFiles
    Assert-WzhkEqual -Expected $saveOne.Profile.id -Actual $loadedProfile.id -Message "Loaded profile lost its stable ID."

    $recommendedPointerPath = Join-Path $profileStore "recommended-profile.json"
    $null = Save-WzhkRecommendedProfilePointer `
        -ProfilePath $savedProfilePath `
        -SceneSha256 $profileSceneHash `
        -CalibrationId "cal-fixture" `
        -RecommendationReason "Synthetic pointer regression." `
        -Path $recommendedPointerPath
    $resolvedPointer = Resolve-WzhkRecommendedProfilePointer -Path $recommendedPointerPath
    Assert-WzhkTest -Condition ([bool]$resolvedPointer.Valid) -Message ("Recommended-profile pointer did not resolve: " + ($resolvedPointer.Issues -join " "))
    Assert-WzhkEqual -Expected ([IO.Path]::GetFullPath($savedProfilePath)) -Actual $resolvedPointer.ProfilePath -Message "Recommended-profile pointer resolved another file."
    $profilesWithPointer = @(Get-WzhkSavedRenderProfiles -Directory (Split-Path -Parent $profileStore) -Recurse)
    Assert-WzhkEqual -Expected 1 -Actual $profilesWithPointer.Count -Message "Recommended-profile pointer was misclassified as a saved render profile."
    Assert-WzhkTest -Condition (@($profilesWithPointer | Where-Object { $_.Path -ieq $recommendedPointerPath }).Count -eq 0) -Message "Recommended-profile pointer appeared in render-profile selection."

    $copiedProfile = Copy-WzhkRenderProfile -Profile $loadedProfile -NewDisplayName "Fixture 1440p Copy"
    Assert-WzhkTest -Condition ($copiedProfile.id -ne $loadedProfile.id) -Message "Duplicated profile reused the stable ID."
    Assert-WzhkTest -Condition ($copiedProfile.profileId -ne $loadedProfile.profileId) -Message "Duplicated profile reused the renderer-facing ID."
    $copyPath = Join-Path $profileStore "1440p-copy.json"
    $copySave = Save-WzhkRenderProfile -Profile $copiedProfile -Path $copyPath
    Assert-WzhkTest -Condition (Test-Path -LiteralPath $copySave.Path -PathType Leaf) -Message "Duplicated profile did not save."

    $requestPath = Join-Path $profileStore "1440p-balanced.authorization-request.json"
    $authorizationRequest = New-WzhkProfileAuthorizationRequest -ProfilePath $savedProfilePath -ScenePath $profileScenePath -Path $requestPath
    $requestText = Get-Content -LiteralPath $requestPath -Raw
    Assert-WzhkTest -Condition (-not $requestText.Contains($authorizationRequest.AuthorizationToken)) -Message "Pending request persisted the plaintext authorization token."
    $oneConfirmationRejected = $false
    try {
        $null = New-WzhkProfileAuthorizationRecord -ProfilePath $savedProfilePath -ScenePath $profileScenePath -SettingsAndHashesReviewed
    }
    catch { $oneConfirmationRejected = $true }
    Assert-WzhkTest -Condition $oneConfirmationRejected -Message "Authorization succeeded with only one confirmation."

    $recordPath = Join-Path $profileStore "1440p-balanced.authorization.json"
    $authorizationRecord = New-WzhkProfileAuthorizationRecord `
        -ProfilePath $savedProfilePath `
        -ScenePath $profileScenePath `
        -SettingsAndHashesReviewed `
        -ProductionRenderAuthorized `
        -Path $recordPath
    $recordValidation = Test-WzhkProfileAuthorizationRecord -ProfilePath $savedProfilePath -ScenePath $profileScenePath -RecordPath $recordPath
    Assert-WzhkTest -Condition ([bool]$recordValidation.Valid) -Message ("Two-confirmation authorization record was invalid: " + ($recordValidation.Issues -join " "))

    $editedProfile = Set-WzhkProfileValue -Profile $loadedProfile -PropertyPath "render.samples" -Value 96
    $editedSave = Save-WzhkRenderProfile -Profile $editedProfile -Path $savedProfilePath -Force
    Assert-WzhkTest -Condition ($editedSave.FileSha256 -ne $rawHashBefore) -Message "Settings edit did not change the exact saved-file hash."
    Assert-WzhkTest -Condition ([bool]$editedSave.AuthorizationInvalidated) -Message "Settings edit did not report authorization invalidation."
    $staleRecord = Test-WzhkProfileAuthorizationRecord -ProfilePath $savedProfilePath -ScenePath $profileScenePath -RecordPath $recordPath
    Assert-WzhkTest -Condition (-not [bool]$staleRecord.Valid) -Message "Authorization survived a saved-profile edit."

    $renamed = Rename-WzhkRenderProfile -Profile $editedSave.Profile -NewDisplayName "Renamed Fixture Profile"
    Assert-WzhkEqual -Expected "Renamed Fixture Profile" -Actual $renamed.displayName -Message "Profile rename failed."
    Assert-WzhkEqual -Expected "pending-operator-approval" -Actual $renamed.authorization.status -Message "Rename did not invalidate authorization."

    $sceneMismatch = Set-WzhkProfileValue -Profile $editedSave.Profile -PropertyPath "approvedSceneSha256" -Value ("F" * 64)
    $sceneMismatch = Set-WzhkProfileValue -Profile $sceneMismatch -PropertyPath "approvedScene.sha256" -Value ("F" * 64)
    Assert-WzhkTest -Condition (-not [bool](Test-WzhkRenderProfile -Profile $sceneMismatch -VerifyFiles).Valid) -Message "Scene hash mismatch passed file verification."

    $emptyOutput = New-WzhkFixtureDirectory -Path (Join-Path $fixtureRoot "truly-empty-output")
    $emptyInspection = Get-WzhkOutputDirectoryInspection -OutputPath $emptyOutput
    Assert-WzhkEqual -Expected "truly-empty-directory" -Actual $emptyInspection.Classification -Message "A truly empty output directory was misclassified."
    $emptyCompatibility = Test-WzhkOutputCompatibility -ProfilePath $savedProfilePath -ScenePath $profileScenePath -OutputPath $emptyOutput
    Assert-WzhkTest -Condition ([bool]$emptyCompatibility.Compatible) -Message "A truly empty output directory was rejected."
    Assert-WzhkEqual -Expected "empty-output" -Actual $emptyCompatibility.Status -Message "A truly empty output did not report empty-output status."

    $hiddenOutput = New-WzhkFixtureDirectory -Path (Join-Path $fixtureRoot "hidden-conflict-output")
    $hiddenConflict = Join-Path $hiddenOutput ".operator-note.txt"
    Write-WzhkFixtureText -Path $hiddenConflict -Value "synthetic hidden conflict"
    [IO.File]::SetAttributes($hiddenConflict, [IO.FileAttributes]::Hidden)
    $hiddenInspection = Get-WzhkOutputDirectoryInspection -OutputPath $hiddenOutput
    Assert-WzhkEqual -Expected "directory-with-unrelated-entries" -Actual $hiddenInspection.Classification -Message "Hidden output entries were not inspected with -Force."
    Assert-WzhkTest -Condition (@($hiddenInspection.ConflictingEntries | Where-Object { $_ -match '\.operator-note\.txt \[file, hidden\]' }).Count -eq 1) -Message "Hidden conflict diagnostics omitted the exact entry and attributes."
    $hiddenCompatibility = Test-WzhkOutputCompatibility -ProfilePath $savedProfilePath -ScenePath $profileScenePath -OutputPath $hiddenOutput
    Assert-WzhkTest -Condition (-not [bool]$hiddenCompatibility.Compatible) -Message "An output containing a hidden unrelated file was accepted."
    Assert-WzhkTest -Condition (@($hiddenCompatibility.Issues | Where-Object { $_.Message -match 'CREATE A NEW UNIQUE RENDER SUBFOLDER HERE' -and $_.Message -match '\.operator-note\.txt' }).Count -eq 1) -Message "Output rejection did not provide the exact conflict and unique-subfolder action."

    $parentOutput = New-WzhkFixtureDirectory -Path (Join-Path $fixtureRoot "render-parent")
    $null = New-WzhkFixtureDirectory -Path (Join-Path $parentOutput "older-render")
    $parentInspection = Get-WzhkOutputDirectoryInspection -OutputPath $parentOutput
    Assert-WzhkEqual -Expected "parent-directory-with-child-folders" -Actual $parentInspection.Classification -Message "A render parent directory was misclassified."
    $uniqueOne = New-WzhkUniqueRenderSubfolder -ParentDirectory $parentOutput -BaseName "Trip to Andromeda 720p"
    $null = New-WzhkFixtureDirectory -Path $uniqueOne
    $uniqueTwo = New-WzhkUniqueRenderSubfolder -ParentDirectory $parentOutput -BaseName "Trip to Andromeda 720p"
    Assert-WzhkTest -Condition ($uniqueOne -ne $uniqueTwo) -Message "Unique render subfolder generation collided."
    Assert-WzhkTest -Condition (-not (Test-Path -LiteralPath $uniqueTwo)) -Message "Unique output allocation unexpectedly mutated the parent directory."

    $matchingOutput = New-WzhkFixtureDirectory -Path (Join-Path $fixtureRoot "matching-profile-output")
    $matchingManifestDirectory = New-WzhkFixtureDirectory -Path (Join-Path $matchingOutput "manifests")
    $currentSavedProfile = Import-WzhkRenderProfile -Path $savedProfilePath
    $currentProfileHash = Get-WzhkFileSha256 -Path $savedProfilePath
    $matchingManifest = [ordered]@{
        schemaVersion = "1.0.0"
        kind = "trackprompt-final-render-manifest"
        scene = [ordered]@{ fileName = [IO.Path]::GetFileName($profileScenePath); sha256 = $profileSceneHash }
        renderProfile = [ordered]@{ fileName = [IO.Path]::GetFileName($savedProfilePath); sha256 = $currentProfileHash }
        outputDirectory = $matchingOutput
        frameContract = [ordered]@{
            frameStart = [int]$currentSavedProfile.timeline.frameStart
            frameEnd = [int]$currentSavedProfile.timeline.frameEnd
            frameCount = [int]$currentSavedProfile.timeline.frameCount
            fps = [double]$currentSavedProfile.timeline.fps
            width = [int]$currentSavedProfile.resolution.width
            height = [int]$currentSavedProfile.resolution.height
            pixelAspectX = [double]$currentSavedProfile.resolution.pixelAspectX
            pixelAspectY = [double]$currentSavedProfile.resolution.pixelAspectY
            filenamePattern = [string]$currentSavedProfile.imageSequence.filenamePattern
            format = [string]$currentSavedProfile.imageSequence.format
            bitDepth = [int]$currentSavedProfile.imageSequence.bitDepth
            colorMode = [string]$currentSavedProfile.imageSequence.colorMode
            framesSubdirectory = [string]$currentSavedProfile.output.framesSubdirectory
        }
    }
    Write-WzhkFixtureText -Path (Join-Path $matchingManifestDirectory "render-manifest.json") -Value ($matchingManifest | ConvertTo-Json -Depth 20)
    $matchingCompatibility = Test-WzhkOutputCompatibility -ProfilePath $savedProfilePath -ScenePath $profileScenePath -OutputPath $matchingOutput
    Assert-WzhkTest -Condition ([bool]$matchingCompatibility.Compatible) -Message ("Matching resume output was rejected: " + (@($matchingCompatibility.Issues | ForEach-Object { $_.Code }) -join ", "))
    Assert-WzhkEqual -Expected "resume-compatible" -Actual $matchingCompatibility.Status -Message "Matching output did not report resume compatibility."

    $customContractProfile = Copy-WzhkRenderProfile -Profile $currentSavedProfile -NewDisplayName "Fixture Custom Frame Contract"
    $customContractProfile = Set-WzhkProfileValue -Profile $customContractProfile -PropertyPath "resolution.pixelAspectX" -Value 2.0
    $customContractProfile = Set-WzhkProfileValue -Profile $customContractProfile -PropertyPath "resolution.pixelAspectY" -Value 1.0
    $customContractProfile = Set-WzhkProfileValue -Profile $customContractProfile -PropertyPath "output.framesSubdirectory" -Value "published-frames"
    $customContractPath = Join-Path $profileStore "custom-frame-contract.json"
    $customContractSave = Save-WzhkRenderProfile -Profile $customContractProfile -Path $customContractPath
    $customContractOutput = New-WzhkFixtureDirectory -Path (Join-Path $fixtureRoot "custom-frame-contract-output")
    $customContractManifestDirectory = New-WzhkFixtureDirectory -Path (Join-Path $customContractOutput "manifests")
    $customContractManifest = $matchingManifest | ConvertTo-Json -Depth 20 | ConvertFrom-Json
    $customContractManifest.renderProfile.fileName = [IO.Path]::GetFileName($customContractPath)
    $customContractManifest.renderProfile.sha256 = $customContractSave.FileSha256
    $customContractManifest.outputDirectory = $customContractOutput
    $customContractManifest.frameContract.pixelAspectX = 2.0
    $customContractManifest.frameContract.pixelAspectY = 1.0
    $customContractManifest.frameContract.framesSubdirectory = "published-frames"
    $customContractManifestPath = Join-Path $customContractManifestDirectory "render-manifest.json"
    Write-WzhkFixtureText -Path $customContractManifestPath -Value ($customContractManifest | ConvertTo-Json -Depth 20)
    $customContractCompatibility = Test-WzhkOutputCompatibility -ProfilePath $customContractPath -ScenePath $profileScenePath -OutputPath $customContractOutput
    Assert-WzhkTest -Condition ([bool]$customContractCompatibility.Compatible) -Message "Custom pixel aspect and published-frame subdirectory were not bound into resume compatibility."
    $customContractManifest.frameContract.framesSubdirectory = "frames"
    Write-WzhkFixtureText -Path $customContractManifestPath -Value ($customContractManifest | ConvertTo-Json -Depth 20)
    $wrongFramesDirectoryCompatibility = Test-WzhkOutputCompatibility -ProfilePath $customContractPath -ScenePath $profileScenePath -OutputPath $customContractOutput
    Assert-WzhkTest `
        -Condition (@($wrongFramesDirectoryCompatibility.Issues | Where-Object { $_.Code -eq "format-mismatch" }).Count -gt 0) `
        -Message "A mismatched published-frame subdirectory was accepted for resume."

    $fourKPath = Join-Path $profileStore "4k-balanced.json"
    $fourKSave = Save-WzhkRenderProfile -Profile $templateProfiles["4K-BALANCED"] -Path $fourKPath
    $fourKCompatibility = Test-WzhkOutputCompatibility -ProfilePath $fourKPath -ScenePath $profileScenePath -OutputPath $matchingOutput
    Assert-WzhkTest -Condition (-not [bool]$fourKCompatibility.Compatible) -Message "1440p output was accepted for native 4K."
    Assert-WzhkTest -Condition (@($fourKCompatibility.Issues | Where-Object { $_.Code -eq "resolution-mismatch" }).Count -gt 0) -Message "4K/1440p rejection did not report resolution mismatch."

    $wrongProfileManifest = Get-Content -LiteralPath (Join-Path $matchingManifestDirectory "render-manifest.json") -Raw | ConvertFrom-Json
    $wrongProfileManifest.renderProfile.sha256 = ("E" * 64)
    Write-WzhkFixtureText -Path (Join-Path $matchingManifestDirectory "render-manifest.json") -Value ($wrongProfileManifest | ConvertTo-Json -Depth 20)
    $wrongProfileCompatibility = Test-WzhkOutputCompatibility -ProfilePath $savedProfilePath -ScenePath $profileScenePath -OutputPath $matchingOutput
    Assert-WzhkTest -Condition (@($wrongProfileCompatibility.Issues | Where-Object { $_.Code -eq "profile-mismatch" }).Count -gt 0) -Message "Mismatched profile hash was accepted for resume."

    $wrongSceneManifest = $wrongProfileManifest
    $wrongSceneManifest.renderProfile.sha256 = $currentProfileHash
    $wrongSceneManifest.scene.sha256 = ("D" * 64)
    Write-WzhkFixtureText -Path (Join-Path $matchingManifestDirectory "render-manifest.json") -Value ($wrongSceneManifest | ConvertTo-Json -Depth 20)
    $wrongSceneCompatibility = Test-WzhkOutputCompatibility -ProfilePath $savedProfilePath -ScenePath $profileScenePath -OutputPath $matchingOutput
    Assert-WzhkTest -Condition (@($wrongSceneCompatibility.Issues | Where-Object { $_.Code -eq "scene-mismatch" }).Count -gt 0) -Message "Mismatched scene hash was accepted for resume."

    $listedProfiles = @(Get-WzhkSavedRenderProfiles -Directory (Split-Path -Parent $profileStore) -Recurse)
    Assert-WzhkTest -Condition ($listedProfiles.Count -ge 3) -Message "Recursive saved-profile discovery missed project profiles."
    Assert-WzhkEqual -Expected $true -Actual ([bool](Remove-WzhkRenderProfile -Path $copyPath -ConfirmDeletion)) -Message "Explicitly confirmed profile deletion failed."
    Assert-WzhkTest -Condition (-not (Test-Path -LiteralPath $copyPath)) -Message "Deleted duplicate profile still exists."

    Write-Host "Template, custom, validation, save/load/edit/duplicate, authorization, and resume-compatibility checks passed."

    Write-Host ("Parser files checked: " + $parseFiles.Count)
    Write-Host ("Launcher modules imported: " + $moduleSpecifications.Count)
    Write-Host "Synthetic discovery, profile, hash, authorization, and output-progress checks passed."

    $launcherLines = [System.IO.File]::ReadAllLines($launcherPath)
    $powerShellLaunchLines = @($launcherLines | Where-Object { $_ -match '(?i)powershell\.exe' })
    Assert-WzhkEqual -Expected 1 -Actual $powerShellLaunchLines.Count -Message "The CMD launcher must contain one powershell.exe launch line."
    Assert-WzhkTest -Condition $powerShellLaunchLines[0].Contains('%*') -Message "The CMD launcher does not forward all arguments with %*."

    $windowsPowerShell = (Get-Command powershell.exe -CommandType Application -ErrorAction Stop).Source
    $directArguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $controlCenterPath,
        "-ValidateOnly"
    )
    $directArgumentString = ($directArguments | ForEach-Object { ConvertTo-WzhkNativeArgument -Value ([string]$_) }) -join " "
    $directResult = Invoke-WzhkBoundedProcess `
        -FilePath $windowsPowerShell `
        -Arguments $directArgumentString `
        -Label "wzhk-media-control-center.ps1 -ValidateOnly"
    Assert-WzhkProcessSucceeded -Result $directResult -Label "wzhk-media-control-center.ps1 -ValidateOnly"

    $profileValidationArguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $controlCenterPath,
        "-ValidateProfile",
        "-ProfilePath", $savedProfilePath
    )
    $profileValidationArgumentString = ($profileValidationArguments | ForEach-Object { ConvertTo-WzhkNativeArgument -Value ([string]$_) }) -join " "
    $profileValidationResult = Invoke-WzhkBoundedProcess `
        -FilePath $windowsPowerShell `
        -Arguments $profileValidationArgumentString `
        -Label "wzhk-media-control-center.ps1 -ValidateProfile"
    Assert-WzhkProcessSucceeded -Result $profileValidationResult -Label "wzhk-media-control-center.ps1 -ValidateProfile"
    $profileValidationJson = $profileValidationResult.StandardOutput | ConvertFrom-Json -ErrorAction Stop
    Assert-WzhkEqual -Expected $true -Actual ([bool]$profileValidationJson.ok) -Message "Profile CLI validation did not report structured success."
    Assert-WzhkEqual -Expected $true -Actual ([bool]$profileValidationJson.rendererValidation.ok) -Message "Profile CLI validation did not include authoritative renderer validation."

    $missingProfilePathArguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $controlCenterPath,
        "-ValidateProfile"
    )
    $missingProfilePathArgumentString = ($missingProfilePathArguments | ForEach-Object { ConvertTo-WzhkNativeArgument -Value ([string]$_) }) -join " "
    $missingProfilePathResult = Invoke-WzhkBoundedProcess `
        -FilePath $windowsPowerShell `
        -Arguments $missingProfilePathArgumentString `
        -Label "wzhk-media-control-center.ps1 CLI misuse"
    Assert-WzhkEqual -Expected 2 -Actual ([int]$missingProfilePathResult.ExitCode) -Message "Missing -ProfilePath did not use CLI misuse exit code 2."

    $blenderIdsBefore = @(Get-Process -Name "blender" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    $renderProfileArguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $controlCenterPath,
        "-RenderProfile",
        "-ProfilePath", $savedProfilePath
    )
    $renderProfileArgumentString = ($renderProfileArguments | ForEach-Object { ConvertTo-WzhkNativeArgument -Value ([string]$_) }) -join " "
    $renderProfileResult = Invoke-WzhkBoundedProcess `
        -FilePath $windowsPowerShell `
        -Arguments $renderProfileArgumentString `
        -Label "wzhk-media-control-center.ps1 -RenderProfile redirected safety"
    Assert-WzhkEqual -Expected 2 -Actual ([int]$renderProfileResult.ExitCode) -Message "Redirected -RenderProfile did not refuse with exit code 2."
    Assert-WzhkTest `
        -Condition (($renderProfileResult.StandardOutput + $renderProfileResult.StandardError).Contains("requires an interactive console")) `
        -Message "Redirected -RenderProfile did not explain the two-confirmation safety refusal."
    $blenderIdsAfter = @(Get-Process -Name "blender" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    Assert-WzhkEqual -Expected ($blenderIdsBefore -join ",") -Actual ($blenderIdsAfter -join ",") -Message "Noninteractive profile validation/render refusal started Blender."

    $uninitializedOutput = Join-Path $fixtureRoot "watcher-must-not-create-output"
    $watcherArguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $watcherPath,
        "-OutputDirectory", $uninitializedOutput,
        "-TotalFrames", "4",
        "-WaitForOutputSeconds", "1",
        "-NoBrowser"
    )
    $watcherArgumentString = ($watcherArguments | ForEach-Object { ConvertTo-WzhkNativeArgument -Value ([string]$_) }) -join " "
    $watcherResult = Invoke-WzhkBoundedProcess `
        -FilePath $windowsPowerShell `
        -Arguments $watcherArgumentString `
        -Label "watcher renderer-owned-manifest gate" `
        -TimeoutMilliseconds 10000
    Assert-WzhkTest -Condition ([int]$watcherResult.ExitCode -ne 0) -Message "Watcher unexpectedly succeeded without a renderer-owned manifest."
    Assert-WzhkTest -Condition (-not (Test-Path -LiteralPath $uninitializedOutput)) -Message "Watcher contaminated a new output before renderer manifest initialization."

    $commandProcessor = [string]$env:ComSpec
    Assert-WzhkTest -Condition (-not [string]::IsNullOrWhiteSpace($commandProcessor)) -Message "ComSpec is unavailable."
    $cmdArguments = '/d /s /c ""' + $launcherPath.Replace('"', '""') + '" -ValidateOnly"'
    $cmdResult = Invoke-WzhkBoundedProcess `
        -FilePath $commandProcessor `
        -Arguments $cmdArguments `
        -Label "WZHK-Media-Launcher.cmd -ValidateOnly"
    Assert-WzhkProcessSucceeded -Result $cmdResult -Label "WZHK-Media-Launcher.cmd -ValidateOnly"

    Write-Host "Direct and CMD-forwarded noninteractive validation checks passed."
}
catch {
    [Console]::Error.WriteLine("WZHK Mission Control validation failed: " + $_.Exception.Message)
    exit 1
}
finally {
    if ($null -ne $fixtureRoot -and (Test-Path -LiteralPath $fixtureRoot -PathType Container)) {
        $resolvedFixture = [System.IO.Path]::GetFullPath($fixtureRoot)
        $resolvedTemporaryBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if (-not $resolvedFixture.StartsWith($resolvedTemporaryBase, [System.StringComparison]::OrdinalIgnoreCase)) {
            [Console]::Error.WriteLine("Refusing to remove fixture outside the temporary directory: " + $resolvedFixture)
            exit 1
        }
        Remove-Item -LiteralPath $resolvedFixture -Recurse -Force
    }
}

Write-Host "WZHK Mission Control validation passed."
