[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ApprovedScenePath,
    [Parameter(Mandatory = $true)][string]$RenderProfilePath,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$AuthorizationToken = "",
    [switch]$DryRun,
    [switch]$Preflight,
    [int]$ChunkSize = 0,
    [string]$ChunkSizeRationale = "",
    [ValidateRange(1, 32)][int]$FrameScanWorkers = 4,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')][string]$MissionControlJobId = "standalone",
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

if ($DryRun -and $Preflight) {
    throw "-DryRun and -Preflight are mutually exclusive."
}
if ($ChunkSize -lt 0) {
    throw "-ChunkSize cannot be negative."
}
if ($ChunkSize -gt 0 -and [string]::IsNullOrWhiteSpace($ChunkSizeRationale)) {
    throw "A non-default -ChunkSize requires -ChunkSizeRationale."
}

$repositoryRoot = $PSScriptRoot
$toolPath = Join-Path $repositoryRoot "tools\final_render_tooling.py"
$chunkRunnerPath = Join-Path $repositoryRoot "blender\render_final_chunk.py"

function Resolve-RequiredFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not [IO.Path]::IsPathRooted($Path)) {
        throw "$Label must be an absolute path."
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label does not exist: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
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

function Invoke-JsonTool {
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
    $jsonLine = @($lines | Where-Object { $_.TrimStart().StartsWith("{") } | Select-Object -Last 1)
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

function Add-ChunkOverrideArguments {
    param([Parameter(Mandatory = $true)][Collections.Generic.List[string]]$Arguments)

    if ($ChunkSize -gt 0) {
        $Arguments.Add("--chunk-size")
        $Arguments.Add([string]$ChunkSize)
        $Arguments.Add("--chunk-rationale")
        $Arguments.Add($ChunkSizeRationale)
    }
}

function Get-OutputAvailableBytes {
    $driveRoot = [IO.Path]::GetPathRoot($script:ResolvedOutput)
    if ([string]::IsNullOrWhiteSpace($driveRoot)) {
        throw "Could not determine the production output drive root."
    }
    try {
        $drive = [IO.DriveInfo]::new($driveRoot)
        if (-not $drive.IsReady) {
            throw "Output drive '$driveRoot' is not ready."
        }
        return [int64]$drive.AvailableFreeSpace
    }
    catch {
        throw "Could not read free space for output drive '$driveRoot': $($_.Exception.Message)"
    }
}

function Assert-AvailableStorage {
    param(
        [Parameter(Mandatory = $true)][int64]$RequiredBytes,
        [Parameter(Mandatory = $true)][string]$Context
    )

    $availableBytes = Get-OutputAvailableBytes
    if ($availableBytes -lt $RequiredBytes) {
        $requiredGiB = [Math]::Round($RequiredBytes / 1GB, 3)
        $availableGiB = [Math]::Round($availableBytes / 1GB, 3)
        throw "$Context requires at least $RequiredBytes bytes ($requiredGiB GiB) free; only $availableBytes bytes ($availableGiB GiB) are available. No chunk was started."
    }
    return $availableBytes
}

function Get-StopRequestPath {
    return Join-Path $script:ResolvedOutput "control\stop-after-current-chunk.request.json"
}

function Test-StopAfterCurrentChunkRequest {
    $path = Get-StopRequestPath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
    try { $request = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "Stop-after-current-chunk request is invalid JSON: $path" }
    if ([string]$request.kind -ne "trackprompt-stop-after-current-chunk-request" -or [string]$request.status -ne "requested") {
        throw "Stop-after-current-chunk marker has the wrong kind or status: $path"
    }
    $profileHash = (Get-FileHash -LiteralPath $script:ResolvedProfile -Algorithm SHA256).Hash.ToUpperInvariant()
    $sceneHash = (Get-FileHash -LiteralPath $script:ResolvedScene -Algorithm SHA256).Hash.ToUpperInvariant()
    if ([string]$request.profileSha256 -ne $profileHash -or [string]$request.sceneSha256 -ne $sceneHash) {
        throw "Stop-after-current-chunk marker does not match the exact active scene and profile."
    }
    if ([IO.Path]::GetFullPath([string]$request.outputDirectory).TrimEnd('\') -ine $script:ResolvedOutput.TrimEnd('\')) {
        throw "Stop-after-current-chunk marker belongs to another output directory."
    }
    return $true
}

function Record-StopAfterCurrentChunk {
    param(
        [AllowNull()][object]$CompletedStart = $null,
        [AllowNull()][object]$CompletedEnd = $null
    )
    $arguments = [Collections.Generic.List[string]]::new()
    foreach ($value in @(
        "record-operator-stop",
        "--profile", $script:ResolvedProfile,
        "--scene", $script:ResolvedScene,
        "--output", $script:ResolvedOutput
    )) { $arguments.Add([string]$value) }
    if ($null -ne $CompletedStart -and $null -ne $CompletedEnd) {
        $arguments.Add("--completed-start")
        $arguments.Add([string]$CompletedStart)
        $arguments.Add("--completed-end")
        $arguments.Add([string]$CompletedEnd)
    }
    return Invoke-JsonTool -ArgumentList $arguments.ToArray() -Description "Operator stop checkpoint"
}

$script:ResolvedPython = Resolve-Python
$script:ToolPath = Resolve-RequiredFile -Path $toolPath -Label "Final render tooling"
$resolvedChunkRunner = Resolve-RequiredFile -Path $chunkRunnerPath -Label "Blender chunk runner"
$resolvedScene = Resolve-RequiredFile -Path $ApprovedScenePath -Label "Approved scene"
$resolvedProfile = Resolve-RequiredFile -Path $RenderProfilePath -Label "Render profile"
$script:ResolvedScene = $resolvedScene
$script:ResolvedProfile = $resolvedProfile

if (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
    throw "Output directory must be an absolute path."
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
$script:ResolvedOutput = $resolvedOutput

if (-not [string]::IsNullOrWhiteSpace($AuthorizationToken)) {
    $null = Invoke-JsonTool `
        -ArgumentList @(
            "validate-token",
            "--profile", $resolvedProfile,
            "--scene", $resolvedScene,
            "--authorization-token", $AuthorizationToken
        ) `
        -Description "Authorization-token validation"
}

if ($DryRun -or $Preflight) {
    $arguments = [Collections.Generic.List[string]]::new()
    foreach ($value in @(
        "render-plan",
        "--profile", $resolvedProfile,
        "--scene", $resolvedScene,
        "--output", $resolvedOutput,
        "--workers", [string]$FrameScanWorkers
    )) {
        $arguments.Add($value)
    }
    Add-ChunkOverrideArguments -Arguments $arguments
    $plan = Invoke-JsonTool -ArgumentList $arguments.ToArray() -Description "Final render inspection"
    if ($Preflight) {
        $resolvedBlender = Resolve-RequiredFile -Path $BlenderExecutable -Label "Blender executable"
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $versionOutput = @(& $resolvedBlender --version 2>&1)
            $versionExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($versionExitCode -ne 0) {
            throw "Blender preflight failed with exit code $versionExitCode."
        }
        $expectedVersionLine = "Blender $($plan.expectedBlenderVersion)"
        if ([string]$versionOutput[0] -ne $expectedVersionLine) {
            throw "Blender preflight version mismatch. Expected '$expectedVersionLine'; found '$([string]$versionOutput[0])'."
        }
        $availableBytes = Get-OutputAvailableBytes
        $manifestPath = Join-Path $resolvedOutput "manifests\render-manifest.json"
        $requiredBytes = [int64]$plan.storage.currentRequirement.requiredFreeBytes
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            $requiredBytes = [Math]::Max($requiredBytes, [int64]$plan.storage.policy.minimumLaunchFreeBytes)
        }
        $null = Assert-AvailableStorage -RequiredBytes $requiredBytes -Context "Final render preflight"
        $plan | Add-Member -NotePropertyName blenderVersion -NotePropertyValue ([string]$versionOutput[0])
        $plan | Add-Member -NotePropertyName availableBytes -NotePropertyValue $availableBytes
        $plan | Add-Member -NotePropertyName requiredBytes -NotePropertyValue $requiredBytes
        $plan | Add-Member -NotePropertyName storageReady -NotePropertyValue $true
        $plan | Add-Member -NotePropertyName preflight -NotePropertyValue $true
    }
    $plan | ConvertTo-Json -Depth 30
    exit 0
}

if ([string]::IsNullOrWhiteSpace($AuthorizationToken)) {
    throw "The exact scene-specific -AuthorizationToken is required for a production render."
}
$resolvedBlender = Resolve-RequiredFile -Path $BlenderExecutable -Label "Blender executable"

$mutex = [Threading.Mutex]::new($false, "Local\TrackPromptFinalRenderGpu")
$ownsMutex = $false
try {
    try {
        $ownsMutex = $mutex.WaitOne(0)
    }
    catch [Threading.AbandonedMutexException] {
        $ownsMutex = $true
        Write-Warning "Recovered an abandoned TrackPrompt final-render mutex after a prior process ended."
    }
    if (-not $ownsMutex) {
        throw "Another TrackPrompt final-render process currently owns the one-GPU render mutex."
    }

    $storageInspectionArguments = [Collections.Generic.List[string]]::new()
    foreach ($value in @(
        "render-plan",
        "--profile", $resolvedProfile,
        "--scene", $resolvedScene,
        "--output", $resolvedOutput,
        "--workers", [string]$FrameScanWorkers,
        "--report-blocked"
    )) {
        $storageInspectionArguments.Add($value)
    }
    Add-ChunkOverrideArguments -Arguments $storageInspectionArguments
    $storageInspection = Invoke-JsonTool `
        -ArgumentList $storageInspectionArguments.ToArray() `
        -Description "Pre-mutation storage inspection"
    $renderManifest = Join-Path $resolvedOutput "manifests\render-manifest.json"
    $preMutationRequiredBytes = [int64]$storageInspection.storage.currentRequirement.requiredFreeBytes
    if (-not (Test-Path -LiteralPath $renderManifest -PathType Leaf)) {
        $preMutationRequiredBytes = [Math]::Max(
            $preMutationRequiredBytes,
            [int64]$storageInspection.storage.policy.minimumLaunchFreeBytes
        )
    }
    $null = Assert-AvailableStorage `
        -RequiredBytes $preMutationRequiredBytes `
        -Context "Authorized render pre-mutation check"

    $quarantine = Invoke-JsonTool `
        -ArgumentList @(
            "quarantine-invalid-frames",
            "--profile", $resolvedProfile,
            "--scene", $resolvedScene,
            "--output", $resolvedOutput,
            "--authorization-token", $AuthorizationToken,
            "--workers", [string]$FrameScanWorkers
        ) `
        -Description "Authorized invalid-frame quarantine"
    if ([int]$quarantine.quarantinedFrameCount -gt 0) {
        Write-Host (
            "Quarantined {0} invalid canonical frame(s) for recoverable re-render: {1}" -f `
                $quarantine.quarantinedFrameCount,
                $quarantine.quarantineDirectory
        ) -ForegroundColor Yellow
    }

    $inspectionArguments = [Collections.Generic.List[string]]::new()
    foreach ($value in @(
        "render-plan",
        "--profile", $resolvedProfile,
        "--scene", $resolvedScene,
        "--output", $resolvedOutput,
        "--workers", [string]$FrameScanWorkers
    )) {
        $inspectionArguments.Add($value)
    }
    Add-ChunkOverrideArguments -Arguments $inspectionArguments
    $initialInspection = Invoke-JsonTool `
        -ArgumentList $inspectionArguments.ToArray() `
        -Description "Pre-initialization render inspection"
    if ($initialInspection.complete -and $initialInspection.authorizationAccepted) {
        Write-Host "All expected frames already validate; nothing will be rendered." -ForegroundColor Green
        $initialInspection | ConvertTo-Json -Depth 30
        exit 0
    }
    $initialRequiredBytes = [int64]$initialInspection.storage.currentRequirement.requiredFreeBytes
    if (-not (Test-Path -LiteralPath $renderManifest -PathType Leaf)) {
        $initialRequiredBytes = [Math]::Max(
            $initialRequiredBytes,
            [int64]$initialInspection.storage.policy.minimumLaunchFreeBytes
        )
    }
    $null = Assert-AvailableStorage `
        -RequiredBytes $initialRequiredBytes `
        -Context "Authorized render initialization"

    $arguments = [Collections.Generic.List[string]]::new()
    foreach ($value in @(
        "render-plan",
        "--profile", $resolvedProfile,
        "--scene", $resolvedScene,
        "--output", $resolvedOutput,
        "--workers", [string]$FrameScanWorkers,
        "--initialize",
        "--require-authorization",
        "--authorization-token", $AuthorizationToken
    )) {
        $arguments.Add($value)
    }
    Add-ChunkOverrideArguments -Arguments $arguments
    $plan = Invoke-JsonTool -ArgumentList $arguments.ToArray() -Description "Authorized render initialization"
    if ($plan.complete) {
        Write-Host "All expected frames already validate; nothing will be rendered." -ForegroundColor Green
        $plan | ConvertTo-Json -Depth 30
        exit 0
    }

    $checkpointsDirectory = Join-Path $resolvedOutput "checkpoints"
    $logsDirectory = Join-Path $resolvedOutput "logs"
    $chunkNumber = 0
    foreach ($chunk in @($plan.chunks)) {
        $chunkNumber += 1
        if (Test-StopAfterCurrentChunkRequest) {
            $stop = Record-StopAfterCurrentChunk
            Write-Host "Stop request honored before another chunk was started. No frame was deleted or overwritten." -ForegroundColor Yellow
            $stop | ConvertTo-Json -Depth 20
            exit 0
        }
        $startFrame = [int]$chunk.startFrame
        $endFrame = [int]$chunk.endFrame
        $chunkRequirement = @($plan.storage.chunkLaunchRequirements)[$chunkNumber - 1]
        if (
            [int]$chunkRequirement.startFrame -ne $startFrame -or
            [int]$chunkRequirement.endFrame -ne $endFrame
        ) {
            throw "Storage requirement plan does not match chunk $startFrame-$endFrame."
        }
        $null = Assert-AvailableStorage `
            -RequiredBytes ([int64]$chunkRequirement.requiredFreeBytes) `
            -Context "Chunk $startFrame-$endFrame"
        $inflightRoot = Join-Path $checkpointsDirectory (
            ".inflight-{0:D6}-{1:D6}-{2}" -f $startFrame, $endFrame, [Guid]::NewGuid().ToString("N")
        )
        $temporaryFrames = Join-Path $inflightRoot "frames"
        $null = New-Item -ItemType Directory -Path $temporaryFrames -ErrorAction Stop
        $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        $stdoutLog = Join-Path $logsDirectory ("chunk_{0:D6}_{1:D6}_{2}.stdout.log" -f $startFrame, $endFrame, $timestamp)
        $stderrLog = Join-Path $logsDirectory ("chunk_{0:D6}_{1:D6}_{2}.stderr.log" -f $startFrame, $endFrame, $timestamp)
        Write-Host (
            "Rendering chunk {0}/{1}: frames {2}-{3} ({4} frames)" -f `
                $chunkNumber, @($plan.chunks).Count, $startFrame, $endFrame, ($endFrame - $startFrame + 1)
        ) -ForegroundColor Cyan
        $blenderArguments = @(
            "--background", $resolvedScene,
            "--python-exit-code", "1",
            "--python", $resolvedChunkRunner,
            "--",
            "--profile", $resolvedProfile,
            "--render-manifest", $renderManifest,
            "--output", $temporaryFrames,
            "--start", [string]$startFrame,
            "--end", [string]$endFrame,
            "--job-id", $MissionControlJobId,
            "--worker-id", ("local-{0}" -f $PID)
        )
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $resolvedBlender @blenderArguments 2> $stderrLog | Tee-Object -FilePath $stdoutLog
            $blenderExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($blenderExitCode -ne 0) {
            try {
                $null = Invoke-JsonTool `
                    -ArgumentList @(
                        "record-chunk-failure",
                        "--profile", $resolvedProfile,
                        "--scene", $resolvedScene,
                        "--output", $resolvedOutput,
                        "--start", [string]$startFrame,
                        "--end", [string]$endFrame,
                        "--exit-code", [string]$blenderExitCode,
                        "--stdout-log", $stdoutLog,
                        "--stderr-log", $stderrLog
                    ) `
                    -Description "Chunk-failure checkpoint"
            }
            catch {
                Write-Warning $_.Exception.Message
            }
            throw "Blender chunk $startFrame-$endFrame failed with exit code $blenderExitCode. Logs were preserved."
        }
        $commit = Invoke-JsonTool `
            -ArgumentList @(
                "commit-chunk",
                "--profile", $resolvedProfile,
                "--scene", $resolvedScene,
                "--output", $resolvedOutput,
                "--temporary-frames", $temporaryFrames,
                "--start", [string]$startFrame,
                "--end", [string]$endFrame,
                "--stdout-log", $stdoutLog,
                "--stderr-log", $stderrLog,
                "--workers", [string]$FrameScanWorkers
            ) `
            -Description "Atomic chunk validation and publication"
        Write-Host (
            "Published frames {0}-{1}; {2} valid of {3}." -f `
                $startFrame,
                $endFrame,
                $commit.frameScan.validFrameCount,
                $commit.frameScan.expectedFrameCount
        ) -ForegroundColor Green
        if (Test-StopAfterCurrentChunkRequest) {
            $stop = Record-StopAfterCurrentChunk -CompletedStart $startFrame -CompletedEnd $endFrame
            Write-Host "Current chunk validated and published. Stopping cleanly at the operator request." -ForegroundColor Yellow
            $stop | ConvertTo-Json -Depth 20
            exit 0
        }
    }

    $finalPlan = Invoke-JsonTool `
        -ArgumentList @(
            "render-plan",
            "--profile", $resolvedProfile,
            "--scene", $resolvedScene,
            "--output", $resolvedOutput,
            "--workers", [string]$FrameScanWorkers
        ) `
        -Description "Final frame-completeness scan"
    if (-not $finalPlan.complete) {
        throw "Rendering ended without a complete validated frame sequence. Resume with the same authorized command."
    }
    $finalPlan | ConvertTo-Json -Depth 30
}
finally {
    if ($ownsMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
