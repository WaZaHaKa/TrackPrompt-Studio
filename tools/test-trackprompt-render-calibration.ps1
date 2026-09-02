[CmdletBinding()]
param()

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
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$moduleRoot = Join-Path $PSScriptRoot "wzhk-launcher"
Import-Module (Join-Path $moduleRoot "WZHK.Profiles.psm1") -Force -DisableNameChecking
Import-Module (Join-Path $moduleRoot "WZHK.Calibration.psm1") -Force -DisableNameChecking
Import-Module (Join-Path $moduleRoot "WZHK.Performance.psm1") -Force -DisableNameChecking
Import-Module (Join-Path $moduleRoot "WZHK.Outsource.psm1") -Force -DisableNameChecking
Import-Module (Join-Path $moduleRoot "WZHK.Execution.psm1") -Force -DisableNameChecking

function Assert-Calibration {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$stats = Get-WzhkCalibrationStatistics -Values @(10.0, 2.0, 3.0, 4.0, 5.0) -ColdValueCount 1
Assert-Calibration ([Math]::Abs($stats.WarmMedianSeconds - 3.5) -lt 0.0001) "Warm median is incorrect."
Assert-Calibration ($stats.P90Seconds -gt $stats.WarmMedianSeconds) "P90 is incorrect."
$chunk = Get-WzhkAdaptiveChunkPlan -MeasuredWarmSecondsPerFrame 6.0 -TargetChunkMinutes 15
Assert-Calibration ($chunk.FramesPerChunk -eq 150) "Adaptive chunk sizing is incorrect."
$slowChunk = Get-WzhkAdaptiveChunkPlan -MeasuredWarmSecondsPerFrame 154.0 -TargetChunkMinutes 15
Assert-Calibration ($slowChunk.FramesPerChunk -lt 15) "Slow renders did not reduce recovery exposure."

$candidates = @(
    [pscustomobject]@{ Id = "fast-caveat"; QualityResult = "PASS WITH DOCUMENTED CAVEAT"; WarmMedianSeconds = 2.0; ProjectedStorageBytes = 100 },
    [pscustomobject]@{ Id = "quality"; QualityResult = "PASS"; WarmMedianSeconds = 2.08; ProjectedStorageBytes = 120 },
    [pscustomobject]@{ Id = "failed"; QualityResult = "FAIL"; WarmMedianSeconds = 1.0; ProjectedStorageBytes = 50 }
)
$selected = Select-WzhkRecommendedCalibrationProfile -Candidates $candidates
Assert-Calibration ($selected.Id -eq "quality") "Within-five-percent quality preference failed."

$invalid = Test-WzhkCalibrationValidity -Calibration ([pscustomobject]@{ sceneSha256 = "A"; gpuModel = "GPU" }) -CurrentIdentity ([pscustomobject]@{ sceneSha256 = "B"; gpuModel = "GPU" })
Assert-Calibration (-not $invalid.Valid) "Scene drift did not invalidate calibration."
$thermal = Test-WzhkThermalSafety -TemperatureC 90
Assert-Calibration ($thermal.RequestStopAfterChunk) "Thermal fail-safe did not request a safe stop."
$machineOne = Get-WzhkMachineFingerprint
$machineTwo = Get-WzhkMachineFingerprint
Assert-Calibration ($machineOne.MachineFingerprint -match '^[A-F0-9]{64}$') "Machine fingerprint format is invalid."
Assert-Calibration ($machineOne.MachineFingerprint -eq $machineTwo.MachineFingerprint) "Machine fingerprint is not stable on the unchanged machine."
$powerSource = Get-WzhkPowerSource
Assert-Calibration ($null -ne $powerSource.PSObject.Properties["OnAcPower"]) "Power-source inspection returned no AC state."
$unconfirmedPerformanceRejected = $false
try { $null = Start-WzhkExclusivePerformanceMode -StatePath (Join-Path $env:TEMP "unconfirmed-wzhk-performance.json") }
catch { $unconfirmedPerformanceRejected = $true }
Assert-Calibration $unconfirmedPerformanceRejected "Exclusive Performance Mode started without explicit confirmation."

$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ("trackprompt-render-calibration-test-" + [Guid]::NewGuid().ToString("N"))
$fixtureFull = [IO.Path]::GetFullPath($fixtureRoot)
$temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
Assert-Calibration ($fixtureFull.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) "Calibration fixture escaped the system temporary directory."
try {
    $null = New-Item -ItemType Directory -Path $fixtureFull
    $scenePath = Join-Path $fixtureFull "synthetic-approved.blend"
    $profilePath = Join-Path $fixtureFull "synthetic-profile.json"
    [IO.File]::WriteAllText($scenePath, "synthetic scene", (New-Object Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText($profilePath, "{}`n", (New-Object Text.UTF8Encoding($false)))
    $sceneFileHash = (Get-FileHash -LiteralPath $scenePath -Algorithm SHA256).Hash.ToUpperInvariant()
    $profileFileHash = (Get-FileHash -LiteralPath $profilePath -Algorithm SHA256).Hash.ToUpperInvariant()
    $outputPath = Join-Path $fixtureFull "managed-output"
    $manifestDirectory = Join-Path $outputPath "manifests"
    $null = New-Item -ItemType Directory -Path $manifestDirectory
    $manifest = [pscustomobject]@{
        kind = "trackprompt-final-render-manifest"
        scene = [pscustomobject]@{ sha256 = $sceneFileHash }
        renderProfile = [pscustomobject]@{ sha256 = $profileFileHash }
    }
    [IO.File]::WriteAllText((Join-Path $manifestDirectory "render-manifest.json"), (($manifest | ConvertTo-Json -Depth 10) + "`n"), (New-Object Text.UTF8Encoding($false)))
    $request = Request-WzhkStopAfterCurrentChunk -OutputDirectory $outputPath -ProfilePath $profilePath -ScenePath $scenePath
    Assert-Calibration (Test-Path -LiteralPath $request.Path -PathType Leaf) "Stop-after-current-chunk marker was not written."
    $marker = Get-Content -LiteralPath $request.Path -Raw | ConvertFrom-Json
    Assert-Calibration ([string]$marker.profileSha256 -eq $profileFileHash -and [string]$marker.sceneSha256 -eq $sceneFileHash) "Stop marker was not bound to the exact scene/profile identity."
    $unconfirmedCancelRejected = $false
    try { $null = Cancel-WzhkStopAfterCurrentChunk -OutputDirectory $outputPath }
    catch { $unconfirmedCancelRejected = $true }
    Assert-Calibration $unconfirmedCancelRejected "Stop request cancellation succeeded without explicit confirmation."
    $null = Cancel-WzhkStopAfterCurrentChunk -OutputDirectory $outputPath -OperatorConfirmed
    Assert-Calibration (-not (Test-Path -LiteralPath $request.Path)) "Confirmed stop request cancellation left the marker behind."

    $safetyRepository = Join-Path $fixtureFull "safety-repository"
    $safetyOutput = Join-Path $safetyRepository "final-output\synthetic-production"
    $safetyInflight = Join-Path $safetyOutput "checkpoints\.inflight-000001-000003-synthetic"
    $safetyFrames = Join-Path $safetyOutput "frames"
    $safetyManifests = Join-Path $safetyOutput "manifests"
    $null = New-Item -ItemType Directory -Path $safetyInflight,$safetyFrames,$safetyManifests
    1..3 | ForEach-Object { [IO.File]::WriteAllBytes((Join-Path $safetyFrames ("frame_" + $_.ToString("D6") + ".png")), [byte[]](1, 2, 3)) }
    $safetyManifest = [pscustomobject]@{
        status = "incomplete"
        scene = [pscustomobject]@{ sha256 = $sceneFileHash }
        renderProfile = [pscustomobject]@{ sha256 = $profileFileHash }
        frameSet = [pscustomobject]@{ validFrameCount = 3 }
    }
    [IO.File]::WriteAllText((Join-Path $safetyManifests "render-manifest.json"), (($safetyManifest | ConvertTo-Json -Depth 10) + "`n"), (New-Object Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText((Join-Path $safetyInflight "heartbeat.txt"), "recent", (New-Object Text.UTF8Encoding($false)))
    $recentSafety = Get-WzhkRenderSafetyAudit -RepositoryRoot $safetyRepository -InflightActivityWindowMinutes 30
    Assert-Calibration (-not $recentSafety.SafeForGpuCalibration) "A recent production in-flight checkpoint did not block calibration."
    Assert-Calibration (@($recentSafety.ActiveInflightDirectories).Count -eq 1) "Recent production in-flight state was not classified as active."
    Assert-Calibration ([int]$recentSafety.PublishedFrameCount -eq 3) "Published-frame discovery did not use the managed manifest."
    Assert-Calibration ([string]$recentSafety.ActiveSceneSha256[0] -eq $sceneFileHash -and [string]$recentSafety.ActiveProfileSha256[0] -eq $profileFileHash) "Active render identities were not surfaced."

    $oldTimestamp = (Get-Date).ToUniversalTime().AddHours(-2)
    Get-ChildItem -LiteralPath $safetyInflight -Recurse -Force | ForEach-Object { $_.LastWriteTimeUtc = $oldTimestamp }
    (Get-Item -LiteralPath $safetyInflight).LastWriteTimeUtc = $oldTimestamp
    $staleSafety = Get-WzhkRenderSafetyAudit -RepositoryRoot $safetyRepository -InflightActivityWindowMinutes 30
    Assert-Calibration (@($staleSafety.ActiveInflightDirectories).Count -eq 0 -and @($staleSafety.StaleOrTestInflightDirectories).Count -eq 1) "Stale in-flight state was not preserved as a non-active operator finding."

    $base = New-WzhkRenderProfile -TemplateId "FULL-HD-FAST" -DisplayName "Synthetic calibration base" -ApprovedScenePath $scenePath -FrameStart 1 -FrameEnd 13029 -Fps 30
    $calibrated720 = New-WzhkCalibratedRenderProfile -BaseProfile $base -DisplayName "Trip to Andromeda - 720p Hyper Optimized" -ProfileId "trip-to-andromeda-720p-hyper-optimized" -Width 1280 -Height 720 -Samples 16 -BitDepth 8 -Compression 5 -ShadowPoolSize "512" -WarmMedianSeconds 2.0 -P90Seconds 2.5 -ProjectedStorageBytes 5000000000 -CalibrationId "cal-synthetic" -CalibrationEvidencePath $fixtureFull -QualityResult "PASS" -RecommendationReason "Synthetic unit-test winner." -Confidence "HIGH"
    Assert-Calibration ([string]$calibrated720.render.shadowPoolSize -eq "512") "Calibrated shadow pool was not preserved."
    Assert-Calibration ([int]$calibrated720.resolution.width -eq 1280 -and [int]$calibrated720.resolution.height -eq 720) "Calibrated 720p profile is not native 1280x720."
    Assert-Calibration ([int]$calibrated720.timeline.frameStart -eq 1 -and [int]$calibrated720.timeline.frameEnd -eq 13029 -and [double]$calibrated720.timeline.fps -eq 30) "Calibrated 720p profile changed the timeline contract."
    Assert-Calibration ([int]$calibrated720.chunking.framesPerChunk -eq 450) "Calibrated 720p profile did not use adaptive chunk sizing."
    Assert-Calibration ([string]$calibrated720.dashboard.resolutionLabel -eq "HD - 1280x720") "Calibrated profile left a stale dashboard resolution label."
    Assert-Calibration ([double]$calibrated720.estimates.minimumLaunchFreeGiB -eq [double]$calibrated720.storage.minimumLaunchFreeGiB) "Calibrated profile left a stale minimum-free-space estimate."
    Assert-Calibration ([string]$calibrated720.estimates.totalStorage -eq ([string]::Format("{0:N0} GiB minimum free", $calibrated720.storage.minimumLaunchFreeGiB))) "Calibrated profile left a stale total-storage label."
    Assert-Calibration ([string]$calibrated720.authorization.status -eq "pending-operator-approval") "Calibrated profile inherited authorization."
    $savedCalibrationPath = Join-Path $fixtureFull "calibrated.json"
    $savedCalibration = Save-WzhkRenderProfile -Profile $calibrated720 -Path $savedCalibrationPath
    $pointerPath = Join-Path $fixtureFull "recommended-profile.json"
    $null = Save-WzhkRecommendedProfilePointer -ProfilePath $savedCalibration.Path -SceneSha256 $sceneFileHash -CalibrationId "cal-synthetic" -RecommendationReason "Synthetic unit-test winner." -Path $pointerPath
    $null = Save-WzhkRenderProfile -Profile $calibrated720 -Path $savedCalibrationPath -Force
    Assert-Calibration (Test-Path -LiteralPath $pointerPath -PathType Leaf) "Recommended-profile pointer blocked a safe profile refresh."
}
finally {
    if (Test-Path -LiteralPath $fixtureFull -PathType Container) { Remove-Item -LiteralPath $fixtureFull -Recurse -Force }
}

$sceneHash = "A" * 64
$profileHash = "B" * 64
$assignments = @(New-WzhkRemoteChunkDistribution -FrameStart 1 -FrameEnd 100 -FramesPerChunk 15 -RemoteWorkers 2 -IncludeLocalWorker -SceneSha256 $sceneHash -ProfileSha256 $profileHash)
$frames = @{}
foreach ($assignment in $assignments) {
    foreach ($frame in $assignment.startFrame..$assignment.endFrame) {
        Assert-Calibration (-not $frames.ContainsKey($frame)) "Distributed chunk assignments overlap."
        $frames[$frame] = $true
    }
}
Assert-Calibration ($frames.Count -eq 100) "Distributed chunk plan has gaps."

$parseFiles = @(
    (Join-Path $moduleRoot "WZHK.Calibration.psm1"),
    (Join-Path $moduleRoot "WZHK.Performance.psm1"),
    (Join-Path $moduleRoot "WZHK.Outsource.psm1"),
    (Join-Path $repositoryRoot "render-trackprompt-final.ps1"),
    (Join-Path $PSScriptRoot "calibrate-trackprompt-render.ps1")
)
foreach ($parseFile in $parseFiles) {
    $tokens = $null
    $errors = $null
    [Management.Automation.Language.Parser]::ParseFile($parseFile, [ref]$tokens, [ref]$errors) | Out-Null
    Assert-Calibration (@($errors).Count -eq 0) ("PowerShell 5.1 parser error in " + $parseFile + ": " + (@($errors | ForEach-Object { $_.Message }) -join "; "))
}

$blenderBefore = @(Get-Process -Name blender -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$audit = Get-WzhkRenderSafetyAudit -RepositoryRoot $repositoryRoot
$blenderAfter = @(Get-Process -Name blender -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
Assert-Calibration (($blenderBefore -join ",") -eq ($blenderAfter -join ",")) "Safety audit changed Blender processes."

Write-Host "TrackPrompt render calibration, performance, stop, and distribution tests passed." -ForegroundColor Green
Write-Host ("  Safety state: " + $audit.Reason)
Write-Host ("  Adaptive chunk: " + $chunk.FramesPerChunk)
Write-Host ("  Remote chunks: " + $assignments.Count)
