[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputDirectory,

    [ValidateRange(1, 1000000)]
    [int]$TotalFrames = 13029,

    [ValidateRange(1, 3600)]
    [int]$RefreshSeconds = 6,

    [ValidateRange(1, 240)]
    [double]$Fps = 30.0,

    [ValidateRange(1, 1000000)]
    [int]$FrameStart = 1,

    [string]$ProfilePath = "",

    [switch]$NoBrowser,

    [switch]$OpenOutputWhenComplete,

    [ValidateRange(1, 600)]
    [int]$WaitForOutputSeconds = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath($OutputDirectory)
$renderManifestPath = Join-Path $root "manifests\render-manifest.json"
$waitDeadline = [DateTime]::UtcNow.AddSeconds($WaitForOutputSeconds)
while (-not (Test-Path -LiteralPath $renderManifestPath -PathType Leaf)) {
    if ([DateTime]::UtcNow -ge $waitDeadline) {
        throw "A renderer-owned render manifest was not initialized within $WaitForOutputSeconds seconds: $renderManifestPath"
    }
    Start-Sleep -Seconds 1
}

$checkpointsRoot = Join-Path $root "checkpoints"
$logsRoot = Join-Path $root "logs"
$dashboardPath = Join-Path $root "_wzhk-render-progress.html"
$performanceStatePath = Join-Path ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))) "test-output\performance\exclusive-performance-state.json"
$openedDashboard = $false

function Get-JsonMemberValue {
    param(
        [AllowNull()][object]$Root,
        [string[]]$Path,
        [AllowNull()][object]$Fallback = $null
    )

    $current = $Root
    foreach ($segment in $Path) {
        if ($null -eq $current) {
            return $Fallback
        }
        $property = $current.PSObject.Properties[$segment]
        if ($null -eq $property) {
            return $Fallback
        }
        $current = $property.Value
    }
    if ($null -eq $current) {
        return $Fallback
    }
    return $current
}

function Get-FileSha256 {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $algorithm = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace("-", "")
        }
        finally {
            $algorithm.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Get-DirectorySizeBytes {
    param([string[]]$Directories)

    [int64]$total = 0
    foreach ($directory in $Directories) {
        if (Test-Path -LiteralPath $directory -PathType Container) {
            foreach ($file in Get-ChildItem -LiteralPath $directory -File -Recurse -ErrorAction SilentlyContinue) {
                $total += [int64]$file.Length
            }
        }
    }
    return $total
}

function Get-NumberStatistics {
    param([double[]]$Values)
    $ordered = @($Values | Where-Object { $_ -ge 0 -and -not [double]::IsNaN($_) -and -not [double]::IsInfinity($_) } | Sort-Object)
    if ($ordered.Count -eq 0) { return [pscustomobject]@{ Count = 0; Mean = 0.0; Median = 0.0; P90 = 0.0; Maximum = 0.0 } }
    $mean = ($ordered | Measure-Object -Average).Average
    function Get-InterpolatedValue([double]$Percentile) {
        if ($ordered.Count -eq 1) { return [double]$ordered[0] }
        $position = $Percentile * ($ordered.Count - 1)
        $lower = [int][Math]::Floor($position)
        $upper = [int][Math]::Ceiling($position)
        return [double]$ordered[$lower] + (([double]$ordered[$upper] - [double]$ordered[$lower]) * ($position - $lower))
    }
    return [pscustomobject]@{ Count = $ordered.Count; Mean = [double]$mean; Median = (Get-InterpolatedValue 0.5); P90 = (Get-InterpolatedValue 0.9); Maximum = [double]$ordered[-1] }
}

function Get-SystemTelemetry {
    $result = [ordered]@{ GpuUtilization = "unknown"; Vram = "unknown"; GpuTemperature = "unknown"; GpuTemperatureC = $null; CpuUtilization = "unknown"; Ram = "unknown" }
    $nvidia = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($null -ne $nvidia) {
        try {
            $line = [string](@(& $nvidia.Source --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>$null | Select-Object -First 1))
            $parts = @($line -split ',' | ForEach-Object { $_.Trim() })
            if ($parts.Count -ge 4) {
                $result.GpuUtilization = $parts[0] + "%"
                $result.Vram = $parts[1] + " / " + $parts[2] + " MiB"
                $result.GpuTemperature = $parts[3] + " C"
                $result.GpuTemperatureC = [double]$parts[3]
            }
        }
        catch { }
    }
    try {
        $counter = Get-Counter @('\Processor(_Total)\% Processor Time', '\Memory\% Committed Bytes In Use') -ErrorAction Stop
        foreach ($sample in $counter.CounterSamples) {
            if ($sample.Path -match 'Processor') { $result.CpuUtilization = [string]::Format("{0:N1}%", $sample.CookedValue) }
            if ($sample.Path -match 'Memory') { $result.Ram = [string]::Format("{0:N1}% committed", $sample.CookedValue) }
        }
    }
    catch { }
    return [pscustomobject]$result
}

function Request-WzhkThermalStopIfRequired {
    param([AllowNull()][object]$TemperatureC, [AllowNull()][object]$Manifest)
    if ($null -eq $TemperatureC -or [double]$TemperatureC -lt 88.0) { return "NORMAL" }
    if (-not (Test-Path -LiteralPath $performanceStatePath -PathType Leaf)) { return "CRITICAL / PERFORMANCE MODE NOT ACTIVE" }
    try { $performanceState = Get-Content -LiteralPath $performanceStatePath -Raw | ConvertFrom-Json }
    catch { return "CRITICAL / PERFORMANCE STATE INVALID" }
    if (-not [bool]$performanceState.restoreRequired) { return "CRITICAL / PERFORMANCE MODE NOT ACTIVE" }
    $requestPath = Join-Path $root "control\stop-after-current-chunk.request.json"
    if (Test-Path -LiteralPath $requestPath -PathType Leaf) { return "CRITICAL / SAFE STOP ALREADY REQUESTED" }
    $sceneHash = [string](Get-JsonMemberValue -Root $Manifest -Path @("scene", "sha256") -Fallback "")
    $profileHash = [string](Get-JsonMemberValue -Root $Manifest -Path @("renderProfile", "sha256") -Fallback "")
    if ($sceneHash -notmatch '^[A-Fa-f0-9]{64}$' -or $profileHash -notmatch '^[A-Fa-f0-9]{64}$') { return "CRITICAL / MANIFEST IDENTITY UNAVAILABLE" }
    $directory = [IO.Path]::GetDirectoryName($requestPath)
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { $null = New-Item -ItemType Directory -Path $directory }
    $request = [pscustomobject][ordered]@{
        schemaVersion = "1.0.0"
        kind = "trackprompt-stop-after-current-chunk-request"
        status = "requested"
        requestedAt = (Get-Date).ToUniversalTime().ToString("o")
        requestedBy = "exclusive-performance-thermal-fail-safe"
        outputDirectory = $root
        profileSha256 = $profileHash.ToUpperInvariant()
        sceneSha256 = $sceneHash.ToUpperInvariant()
        measuredTemperatureC = [double]$TemperatureC
        behavior = "validate and publish the current chunk, then exit before starting the next chunk"
    }
    $temporary = Join-Path $directory (".thermal-stop." + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [IO.File]::WriteAllText($temporary, (($request | ConvertTo-Json -Depth 20) + "`n"), (New-Object Text.UTF8Encoding($false)))
        try { [IO.File]::Move($temporary, $requestPath) }
        catch {
            if (-not (Test-Path -LiteralPath $requestPath -PathType Leaf)) { throw }
        }
    }
    finally { if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue } }
    return "CRITICAL / SAFE STOP REQUESTED"
}

$profileData = $null
$resolvedProfilePath = ""
if (-not [string]::IsNullOrWhiteSpace($ProfilePath)) {
    $candidateProfile = [System.IO.Path]::GetFullPath($ProfilePath)
    if (-not (Test-Path -LiteralPath $candidateProfile -PathType Leaf)) {
        throw "Render profile does not exist: $candidateProfile"
    }
    try {
        $profileData = Get-Content -LiteralPath $candidateProfile -Raw | ConvertFrom-Json
        $resolvedProfilePath = $candidateProfile
    }
    catch {
        throw "Render profile is invalid JSON: $candidateProfile"
    }
}

$renderManifest = $null
if (Test-Path -LiteralPath $renderManifestPath -PathType Leaf) {
    try {
        $renderManifest = Get-Content -LiteralPath $renderManifestPath -Raw | ConvertFrom-Json
    }
    catch {
        $renderManifest = $null
    }
}

$profileDisplayName = [string](Get-JsonMemberValue -Root $profileData -Path @("displayName") -Fallback "Legacy render profile")
$profileId = [string](Get-JsonMemberValue -Root $profileData -Path @("profileId") -Fallback "legacy")
$profileWidth = [int](Get-JsonMemberValue -Root $profileData -Path @("resolution", "width") -Fallback (Get-JsonMemberValue -Root $renderManifest -Path @("frameContract", "width") -Fallback 0))
$profileHeight = [int](Get-JsonMemberValue -Root $profileData -Path @("resolution", "height") -Fallback (Get-JsonMemberValue -Root $renderManifest -Path @("frameContract", "height") -Fallback 0))
$profileFps = [double](Get-JsonMemberValue -Root $profileData -Path @("timeline", "fps") -Fallback (Get-JsonMemberValue -Root $profileData -Path @("fps") -Fallback $Fps))
$profileFormat = [string](Get-JsonMemberValue -Root $profileData -Path @("imageSequence", "format") -Fallback (Get-JsonMemberValue -Root $renderManifest -Path @("frameContract", "format") -Fallback "unknown"))
$profileChunkSize = [int](Get-JsonMemberValue -Root $profileData -Path @("production", "framesPerChunk") -Fallback (Get-JsonMemberValue -Root $profileData -Path @("chunking", "framesPerChunk") -Fallback 0))
$sceneSha256 = [string](Get-JsonMemberValue -Root $profileData -Path @("approvedScene", "sha256") -Fallback (Get-JsonMemberValue -Root $profileData -Path @("approvedSceneSha256") -Fallback (Get-JsonMemberValue -Root $renderManifest -Path @("scene", "sha256") -Fallback "")))
$profileSha256 = if (-not [string]::IsNullOrWhiteSpace($resolvedProfilePath)) {
    Get-FileSha256 -Path $resolvedProfilePath
}
else {
    [string](Get-JsonMemberValue -Root $renderManifest -Path @("renderProfile", "sha256") -Fallback "")
}
$estimatedFrameGiB = [double](Get-JsonMemberValue -Root $profileData -Path @("storage", "plannedFrameSequenceGiB") -Fallback 0.0)
$estimatedMasterGiB = [double](Get-JsonMemberValue -Root $profileData -Path @("storage", "projectedMasterGiB") -Fallback 0.0)
$estimatedDeliveryGiB = [double](Get-JsonMemberValue -Root $profileData -Path @("storage", "projectedDeliveryGiB") -Fallback 0.0)
$supportReserveGiB = [double](Get-JsonMemberValue -Root $profileData -Path @("storage", "supportReserveGiB") -Fallback 0.0)
$minimumLaunchFreeGiB = [double](Get-JsonMemberValue -Root $profileData -Path @("storage", "minimumLaunchFreeGiB") -Fallback 0.0)
$profileSamples = [int](Get-JsonMemberValue -Root $profileData -Path @("render", "samples") -Fallback 0)
$profileBitDepth = [int](Get-JsonMemberValue -Root $profileData -Path @("imageSequence", "bitDepth") -Fallback 0)
$profileCompression = Get-JsonMemberValue -Root $profileData -Path @("imageSequence", "compression") -Fallback "unknown"
$shadowStrategy = [string](Get-JsonMemberValue -Root $profileData -Path @("calibration", "shadowStrategy") -Fallback "profile-resolved")
$fogGlowStrategy = [string](Get-JsonMemberValue -Root $profileData -Path @("calibration", "fogGlowStrategy") -Fallback "Blender Fog Glow")
$machineCalibrationId = [string](Get-JsonMemberValue -Root $profileData -Path @("calibration", "calibrationId") -Fallback "not calibrated")
$recommendedForMachine = [bool](Get-JsonMemberValue -Root $profileData -Path @("calibration", "recommendedForMachine") -Fallback $false)
$scratchDrive = [string](Get-JsonMemberValue -Root $profileData -Path @("calibration", "scratchPath") -Fallback $root)
$publicationDrive = [string](Get-JsonMemberValue -Root $profileData -Path @("calibration", "publicationPath") -Fallback $root)
$framesSubdirectory = [string](Get-JsonMemberValue -Root $profileData -Path @("output", "framesSubdirectory") -Fallback (Get-JsonMemberValue -Root $renderManifest -Path @("frameContract", "framesSubdirectory") -Fallback "frames"))
if ($framesSubdirectory -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' -or $framesSubdirectory -in @(".", "..")) {
    throw "output.framesSubdirectory must be one safe directory name."
}
$publishedRoot = Join-Path $root $framesSubdirectory
$profileRefreshSeconds = [int](Get-JsonMemberValue -Root $profileData -Path @("dashboard", "refreshSeconds") -Fallback $RefreshSeconds)
if ($profileRefreshSeconds -ge 1 -and $profileRefreshSeconds -le 3600) { $RefreshSeconds = $profileRefreshSeconds }
$showLatestFrame = [bool](Get-JsonMemberValue -Root $profileData -Path @("dashboard", "showLatestFrame") -Fallback $true)
$showInflightFrames = [bool](Get-JsonMemberValue -Root $profileData -Path @("dashboard", "showInflightFrames") -Fallback $true)
$showPublishedFrames = [bool](Get-JsonMemberValue -Root $profileData -Path @("dashboard", "showPublishedFrames") -Fallback $true)
$showEta = [bool](Get-JsonMemberValue -Root $profileData -Path @("dashboard", "showEta") -Fallback $true)
$showRollingSecondsPerFrame = [bool](Get-JsonMemberValue -Root $profileData -Path @("dashboard", "showRollingSecondsPerFrame") -Fallback $true)
$showStorageGrowth = [bool](Get-JsonMemberValue -Root $profileData -Path @("dashboard", "showStorageGrowth") -Fallback $true)
if ($profileFps -gt 0) {
    $Fps = $profileFps
}
$native4kLabel = if ($profileWidth -eq 3840 -and $profileHeight -eq 2160) {
    "NATIVE 4K — 3840×2160"
}
elseif ($profileWidth -gt 0 -and $profileHeight -gt 0) {
    [string]::Format("{0}×{1}", $profileWidth, $profileHeight)
}
else {
    "RESOLUTION UNKNOWN"
}

function Get-FrameNumber {
    param([System.IO.FileInfo]$File)

    if ($File.BaseName -match '^frame_(\d{6})$') {
        return [int]$Matches[1]
    }

    return $null
}

function Get-FrameMap {
    param([string]$Directory)

    $map = @{}
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return $map
    }

    $files = @(
        Get-ChildItem -LiteralPath $Directory -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Extension.ToLowerInvariant() -in @('.png', '.exr', '.jpg', '.jpeg') -and
                $_.BaseName -match '^frame_\d{6}$'
            }
    )

    foreach ($file in $files) {
        $number = Get-FrameNumber -File $file
        $lastExpectedFrame = $FrameStart + $TotalFrames - 1
        if ($null -eq $number -or $number -lt $FrameStart -or $number -gt $lastExpectedFrame) {
            continue
        }

        if (-not $map.ContainsKey($number) -or
            $file.LastWriteTimeUtc -gt $map[$number].LastWriteTimeUtc) {
            $map[$number] = $file
        }
    }

    return $map
}

function Format-Duration {
    param([TimeSpan]$Value)

    if ($Value.TotalDays -ge 1) {
        return [string]::Format(
            "{0}d {1:00}h {2:00}m",
            [math]::Floor($Value.TotalDays),
            $Value.Hours,
            $Value.Minutes
        )
    }

    if ($Value.TotalHours -ge 1) {
        return [string]::Format(
            "{0:00}h {1:00}m {2:00}s",
            [math]::Floor($Value.TotalHours),
            $Value.Minutes,
            $Value.Seconds
        )
    }

    return [string]::Format(
        "{0:00}m {1:00}s",
        [math]::Floor($Value.TotalMinutes),
        $Value.Seconds
    )
}

function Convert-ToHtmlText {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) {
        return ""
    }

    return [System.Net.WebUtility]::HtmlEncode([string]$Value)
}

$previousStorageBytes = 0L
$previousStorageTime = [DateTime]::UtcNow

while ($true) {
    $now = Get-Date
    try { $renderManifest = Get-Content -LiteralPath $renderManifestPath -Raw | ConvertFrom-Json } catch { }

    $published = Get-FrameMap -Directory $publishedRoot

    $activeInflight = $null
    if (Test-Path -LiteralPath $checkpointsRoot -PathType Container) {
        $activeInflight = Get-ChildItem -LiteralPath $checkpointsRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like ".inflight-*" } |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First 1
    }

    $inflight = @{}
    if ($null -ne $activeInflight) {
        $inflight = Get-FrameMap -Directory (Join-Path $activeInflight.FullName "frames")
    }

    $combined = @{}
    foreach ($number in $published.Keys) {
        $combined[[int]$number] = $published[$number]
    }
    foreach ($number in $inflight.Keys) {
        $combined[[int]$number] = $inflight[$number]
    }

    $publishedNumbers = @($published.Keys | Sort-Object)
    $inflightNumbers = @($inflight.Keys | Sort-Object)
    $combinedNumbers = @($combined.Keys | Sort-Object)

    $publishedCount = $publishedNumbers.Count
    $inflightCount = $inflightNumbers.Count
    $renderedCount = $combinedNumbers.Count
    $currentFrame = if ($combinedNumbers.Count -gt 0) { [int]$combinedNumbers[-1] } else { 0 }

    $publishedPercent = [math]::Min(100.0, 100.0 * $publishedCount / $TotalFrames)
    $renderedPercent = [math]::Min(100.0, 100.0 * $renderedCount / $TotalFrames)

    $publishedPercentText = [string]::Format("{0:N2}%", $publishedPercent)
    $renderedPercentText = [string]::Format("{0:N2}%", $renderedPercent)
    $publishedPercentCss = $publishedPercent.ToString("0.00", [System.Globalization.CultureInfo]::InvariantCulture) + "%"
    $renderedPercentCss = $renderedPercent.ToString("0.00", [System.Globalization.CultureInfo]::InvariantCulture) + "%"

    $orderedFiles = @(
        $combinedNumbers |
            ForEach-Object { $combined[[int]$_] } |
            Sort-Object LastWriteTimeUtc
    )

    $etaText = "CALIBRATING"
    $conservativeEtaText = "CALIBRATING"
    $rateText = "CALIBRATING"
    $speedConfidence = "CALIBRATING"
    $currentFrameTimeText = "unknown"
    $coldStartText = "unknown"
    $timingStatistics = [pscustomobject]@{ Count = 0; Mean = 0.0; Median = 0.0; P90 = 0.0; Maximum = 0.0 }
    if ($orderedFiles.Count -ge 3) {
        $sampleSize = [math]::Min(120, $orderedFiles.Count)
        $sample = @($orderedFiles | Select-Object -Last $sampleSize)
        $deltas = New-Object System.Collections.Generic.List[double]
        for ($index = 1; $index -lt $sample.Count; $index++) {
            $delta = ($sample[$index].LastWriteTimeUtc - $sample[$index - 1].LastWriteTimeUtc).TotalSeconds
            if ($delta -gt 0.05 -and $delta -lt 600.0) { $deltas.Add($delta) }
        }
        $timingStatistics = Get-NumberStatistics -Values $deltas.ToArray()
        if ($timingStatistics.Count -gt 0 -and $timingStatistics.Median -gt 0) {
            $remainingFrames = [math]::Max(0, $TotalFrames - $renderedCount)
            $eta = [TimeSpan]::FromSeconds($remainingFrames * $timingStatistics.Median)
            $conservativeEta = [TimeSpan]::FromSeconds($remainingFrames * $timingStatistics.P90)
            $etaText = Format-Duration -Value $eta
            $conservativeEtaText = Format-Duration -Value $conservativeEta
            $rateText = [string]::Format("median {0:N2} / mean {1:N2} / p90 {2:N2} s", $timingStatistics.Median, $timingStatistics.Mean, $timingStatistics.P90)
            $currentFrameTimeText = [string]::Format("{0:N2} s", $deltas[-1])
            $speedConfidence = if ($timingStatistics.Count -ge 60) { "HIGH" } elseif ($timingStatistics.Count -ge 20) { "MEDIUM" } else { "LOW" }
        }
    }

    $latestFile = $null
    $stableCutoff = $now.ToUniversalTime().AddSeconds(-2)
    $stableFiles = @($orderedFiles | Where-Object { $_.LastWriteTimeUtc -lt $stableCutoff })
    if ($stableFiles.Count -gt 0) {
        $latestFile = $stableFiles[-1]
    }
    elseif ($orderedFiles.Count -gt 0) {
        $latestFile = $orderedFiles[-1]
    }

    $latestPathMarkup = "No completed frame is available yet."
    $imageMarkup = '<div class="empty">Waiting for the first completed frame…</div>'
    if ($null -ne $latestFile) {
        $latestPathMarkup = Convert-ToHtmlText -Value $latestFile.FullName
        $extension = $latestFile.Extension.ToLowerInvariant()

        if ($extension -in @('.png', '.jpg', '.jpeg')) {
            $imageUri = (New-Object System.Uri($latestFile.FullName)).AbsoluteUri
            $imageUri = $imageUri + "?v=" + $latestFile.LastWriteTimeUtc.Ticks
            $imageMarkup = '<img src="' + $imageUri + '" alt="Latest completed render frame">'
        }
        else {
            $imageMarkup = @"
<div class="empty">
The sequence is OpenEXR, which most browsers cannot display directly.<br>
The frame count and logs still update below.
</div>
"@
        }
    }

    $chunkText = "No active chunk"
    if ($null -ne $activeInflight) {
        $chunkText = $activeInflight.Name -replace '^\.inflight-', ''
    }

    $elapsedFrames = if ($currentFrame -ge $FrameStart) { $currentFrame - $FrameStart + 1 } else { 0 }
    $movieTime = [TimeSpan]::FromSeconds($elapsedFrames / $Fps)
    $movieTimeText = [string]::Format(
        "{0:00}:{1:00}:{2:00}.{3:0}",
        [math]::Floor($movieTime.TotalHours),
        $movieTime.Minutes,
        $movieTime.Seconds,
        [math]::Floor($movieTime.Milliseconds / 100)
    )

    $latestLogText = "No stdout log is available yet."
    if (Test-Path -LiteralPath $logsRoot -PathType Container) {
        $latestLog = Get-ChildItem -LiteralPath $logsRoot -File -Filter "chunk_*.stdout.log" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First 1

        if ($null -ne $latestLog) {
            if ($orderedFiles.Count -gt 0) { $coldStartText = Format-Duration -Value ($orderedFiles[0].LastWriteTimeUtc - $latestLog.CreationTimeUtc) }
            try {
                $latestLogText = (Get-Content -LiteralPath $latestLog.FullName -Tail 14 -ErrorAction Stop) -join [Environment]::NewLine
            }
            catch {
                $latestLogText = "The latest log is being initialized."
            }
        }
    }

    $latestLogMarkup = Convert-ToHtmlText -Value $latestLogText
    $updated = $now.ToString("yyyy-MM-dd HH:mm:ss")
    $publishedFrameText = [string]::Format("{0:N0} / {1:N0}", $publishedCount, $TotalFrames)
    $renderedFrameText = [string]::Format("{0:N0} / {1:N0}", $renderedCount, $TotalFrames)
    $inflightWarning = if ($inflightCount -gt 0) {
        "$inflightCount current-chunk frames are rendered but not yet atomically published. They become validated after the chunk completes."
    }
    else {
        "No unpublished current-chunk frames are present."
    }
    $publishedBytes = [int64](($published.Values | Measure-Object Length -Sum).Sum)
    $inflightBytes = [int64](($inflight.Values | Measure-Object Length -Sum).Sum)
    $checkpointsBytes = Get-DirectorySizeBytes -Directories @($checkpointsRoot)
    $checkpointOverheadBytes = [Math]::Max(0L, $checkpointsBytes - $inflightBytes)
    $supportBytes = Get-DirectorySizeBytes -Directories @($logsRoot, (Join-Path $root "manifests"), (Join-Path $root "qa"), (Join-Path $root "master"), (Join-Path $root "delivery"), (Join-Path $root "control"))
    $storageBytes = $publishedBytes + $inflightBytes + $checkpointOverheadBytes + $supportBytes
    $storageGiB = $storageBytes / 1GB
    $storageText = [string]::Format("{0:N2} GiB", $storageGiB)
    $sizeStatistics = Get-NumberStatistics -Values @($combined.Values | ForEach-Object { [double]$_.Length })
    $storageConfidence = if ($sizeStatistics.Count -ge 60) { "HIGH" } elseif ($sizeStatistics.Count -ge 20) { "MEDIUM" } elseif ($sizeStatistics.Count -gt 0) { "LOW" } else { "CALIBRATING" }
    $liveProjectedBytes = if ($sizeStatistics.Count -ge 20) { [int64][Math]::Ceiling([Math]::Max($sizeStatistics.Mean, $sizeStatistics.P90) * $TotalFrames) } else { 0L }
    $estimatedStorageText = if ($liveProjectedBytes -gt 0) { [string]::Format("{0:N2} GiB live conservative", $liveProjectedBytes / 1GB) } elseif ($estimatedFrameGiB -gt 0) { [string]::Format("{0:N2} GiB profile fallback", $estimatedFrameGiB) } else { "CALIBRATING" }
    $driveRoot = [IO.Path]::GetPathRoot($root)
    $freeStorageText = "unknown"
    try { $freeStorageText = [string]::Format("{0:N2} GiB", (New-Object IO.DriveInfo($driveRoot)).AvailableFreeSpace / 1GB) } catch { }
    $storageElapsed = ([DateTime]::UtcNow - $previousStorageTime).TotalSeconds
    $writeRateText = if ($storageElapsed -gt 0 -and $previousStorageBytes -gt 0) { [string]::Format("{0:N2} MiB/s", [Math]::Max(0, $storageBytes - $previousStorageBytes) / 1MB / $storageElapsed) } else { "CALIBRATING" }
    $previousStorageBytes = $storageBytes
    $previousStorageTime = [DateTime]::UtcNow
    $chunkDurationText = if ($null -ne $activeInflight) { Format-Duration -Value ($now.ToUniversalTime() - $activeInflight.CreationTimeUtc) } else { "idle" }
    $telemetry = Get-SystemTelemetry
    $thermalSafetyState = Request-WzhkThermalStopIfRequired -TemperatureC $telemetry.GpuTemperatureC -Manifest $renderManifest
    $stopRequestPath = Join-Path $root "control\stop-after-current-chunk.request.json"
    $stopState = if (Test-Path -LiteralPath $stopRequestPath -PathType Leaf) { "REQUESTED" } else { [string](Get-JsonMemberValue -Root $renderManifest -Path @("runState", "status") -Fallback "NOT REQUESTED") }
    $profileHashShort = if ($profileSha256.Length -ge 12) { $profileSha256.Substring(0, 12) } else { $profileSha256 }
    $sceneHashShort = if ($sceneSha256.Length -ge 12) { $sceneSha256.Substring(0, 12) } else { $sceneSha256 }
    $profilePathMarkup = Convert-ToHtmlText -Value $resolvedProfilePath
    $outputPathMarkup = Convert-ToHtmlText -Value $root
    $profileNameMarkup = Convert-ToHtmlText -Value $profileDisplayName
    $profileIdMarkup = Convert-ToHtmlText -Value $profileId
    $profileFormatMarkup = Convert-ToHtmlText -Value $profileFormat
    $resolutionMarkup = Convert-ToHtmlText -Value $native4kLabel

    $progressMarkup = '<div class="bar"><div class="fill-rendered"></div></div><div class="bar-label"><span>Rendered frames</span><strong>' + $renderedPercentText + '</strong></div>'
    if ($showPublishedFrames) {
        $progressMarkup += '<div class="bar"><div class="fill-published"></div></div><div class="bar-label"><span>Validated and atomically published</span><strong>' + $publishedPercentText + '</strong></div>'
    }
    $dynamicCards = New-Object System.Collections.Generic.List[string]
    $dynamicCards.Add('<div class="card"><div class="label">Rendered frames</div><div class="value">' + $renderedFrameText + '</div></div>')
    if ($showPublishedFrames) { $dynamicCards.Add('<div class="card"><div class="label">Published frames</div><div class="value">' + $publishedFrameText + '</div></div>') }
    if ($showInflightFrames) { $dynamicCards.Add('<div class="card"><div class="label">In-flight frames</div><div class="value">' + $inflightCount + '</div></div>') }
    if ($showLatestFrame) {
        $dynamicCards.Add('<div class="card"><div class="label">Latest frame</div><div class="value">' + $currentFrame + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Movie position</div><div class="value">' + $movieTimeText + '</div></div>')
    }
    $chunkMarkup = Convert-ToHtmlText -Value $chunkText
    $dynamicCards.Add('<div class="card"><div class="label">Active chunk</div><div class="value truncate" title="' + $chunkMarkup + '">' + $chunkMarkup + '</div><button class="copy" type="button" data-copy="' + $chunkMarkup + '">Copy</button></div>')
    $dynamicCards.Add('<div class="card"><div class="label">Current chunk duration</div><div class="value">' + $chunkDurationText + '</div></div>')
    $dynamicCards.Add('<div class="card"><div class="label">Stop after chunk</div><div class="value">' + (Convert-ToHtmlText -Value $stopState) + '</div></div>')
    if ($showRollingSecondsPerFrame) { $dynamicCards.Add('<div class="card"><div class="label">Rolling speed</div><div class="value">' + $rateText + '</div></div>') }
    if ($showRollingSecondsPerFrame) { $dynamicCards.Add('<div class="card"><div class="label">Current / cold</div><div class="value">' + $currentFrameTimeText + ' / ' + $coldStartText + '</div></div>') }
    if ($showEta) {
        $dynamicCards.Add('<div class="card"><div class="label">Expected remaining</div><div class="value">' + $etaText + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Conservative remaining</div><div class="value">' + $conservativeEtaText + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">ETA confidence</div><div class="value">' + $speedConfidence + '</div></div>')
    }
    if ($showStorageGrowth) {
        $dynamicCards.Add('<div class="card"><div class="label">Published logical</div><div class="value">' + ([string]::Format("{0:N2} GiB", $publishedBytes / 1GB)) + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">In-flight logical</div><div class="value">' + ([string]::Format("{0:N2} GiB", $inflightBytes / 1GB)) + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Checkpoint overhead</div><div class="value">' + ([string]::Format("{0:N2} GiB", $checkpointOverheadBytes / 1GB)) + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Actual logical output</div><div class="value">' + $storageText + ' (allocated bytes unavailable)</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Free storage</div><div class="value">' + $freeStorageText + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Frame bytes m/m/p90</div><div class="value">' + ([string]::Format("{0:N0} / {1:N0} / {2:N0}", $sizeStatistics.Median, $sizeStatistics.Mean, $sizeStatistics.P90)) + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Projected sequence</div><div class="value">' + $estimatedStorageText + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Projected master / delivery</div><div class="value">' + ([string]::Format("{0:N2} / {1:N2} GiB", $estimatedMasterGiB, $estimatedDeliveryGiB)) + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Reserve / minimum launch free</div><div class="value">' + ([string]::Format("{0:N2} / {1:N2} GiB", $supportReserveGiB, $minimumLaunchFreeGiB)) + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Storage confidence</div><div class="value">' + $storageConfidence + '</div></div>')
        $dynamicCards.Add('<div class="card"><div class="label">Drive write rate</div><div class="value">' + $writeRateText + '</div></div>')
    }
    $dynamicCards.Add('<div class="card"><div class="label">GPU / VRAM / TEMP</div><div class="value">' + $telemetry.GpuUtilization + ' / ' + $telemetry.Vram + ' / ' + $telemetry.GpuTemperature + '</div></div>')
    $dynamicCards.Add('<div class="card"><div class="label">Thermal safety</div><div class="value">' + (Convert-ToHtmlText -Value $thermalSafetyState) + '</div></div>')
    $dynamicCards.Add('<div class="card"><div class="label">CPU / RAM</div><div class="value">' + $telemetry.CpuUtilization + ' / ' + $telemetry.Ram + '</div></div>')
    $dynamicCardsMarkup = $dynamicCards -join [Environment]::NewLine
    $inflightMarkup = if ($showInflightFrames) { '<div class="notice">' + (Convert-ToHtmlText -Value $inflightWarning) + '</div>' } else { '' }
    $latestFrameMarkup = if ($showLatestFrame) {
        '<section class="viewer">' + $imageMarkup + '</section><div class="path">' + $latestPathMarkup + '</div>'
    }
    else { '' }

    $html = @"
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="$RefreshSeconds">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WZHK Media // TrackPrompt Render Progress</title>
<style>
:root { color-scheme: dark; }
body { margin: 0; background: radial-gradient(circle at top, #17102c 0, #080a10 44%); color: #eef3ff; font-family: "Segoe UI", Arial, sans-serif; }
main { width: min(1500px, calc(100vw - 36px)); margin: 18px auto 36px; }
.logo { font: 900 clamp(34px, 7vw, 82px)/.9 "Arial Black", Impact, sans-serif; letter-spacing: .08em; text-align: center; background: linear-gradient(90deg,#35e6ff,#bb62ff,#ff4fc7,#35e6ff); background-size: 220% 100%; color: transparent; -webkit-background-clip: text; background-clip: text; filter: drop-shadow(0 0 18px rgba(80,220,255,.32)); }
.logo span { display: block; font: 700 14px/1.3 "Segoe UI", sans-serif; letter-spacing: .55em; color: #fff; margin-top: 12px; }
.frame { border: 2px solid #31dcef; box-shadow: 0 0 0 2px #6a42ff inset, 0 0 26px rgba(72,210,255,.16); border-radius: 18px; padding: 18px; background: rgba(8,11,20,.92); }
h1 { margin: 0 0 4px; font-size: 26px; }
.resolution-banner { margin: 10px 0 16px; padding: 12px; border: 1px solid #35e6ff; border-radius: 10px; text-align: center; font-weight: 800; letter-spacing: .09em; color: #70edff; background: rgba(25,71,92,.35); }
.subtle { color: #aab5ce; margin-bottom: 14px; }
.bar { height: 18px; border-radius: 999px; overflow: hidden; background: #202638; border: 1px solid #3a4563; margin: 7px 0 5px; }
.fill-rendered { width: $renderedPercentCss; height: 100%; background: linear-gradient(90deg, #664cff, #34d9ff); }
.fill-published { width: $publishedPercentCss; height: 100%; background: linear-gradient(90deg, #36a26b, #5ee7a2); }
.bar-label { display: flex; justify-content: space-between; color: #b6bfd4; font-size: 12px; margin-bottom: 10px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin: 15px 0; }
.card { background: #141824; border: 1px solid #2d354b; border-radius: 12px; padding: 12px 14px; }
.label { color: #9faaca; font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }
.value { margin-top: 5px; font-size: 19px; font-weight: 650; overflow-wrap: anywhere; }
.truncate { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.copy { margin-top: 8px; border: 1px solid #4a5a82; border-radius: 7px; background: #20283d; color: #dce7ff; padding: 4px 9px; cursor: pointer; }
.notice { margin: 12px 0; padding: 11px 13px; background: #171c29; border-left: 4px solid #6f5eff; color: #c7d0e5; border-radius: 7px; }
.viewer { background: #04060a; border: 1px solid #2d354b; border-radius: 14px; overflow: hidden; min-height: 360px; display: grid; place-items: center; }
.viewer img { display: block; width: 100%; height: auto; max-height: 72vh; object-fit: contain; }
.empty { padding: 44px; color: #aab5ce; text-align: center; line-height: 1.6; }
.path { margin-top: 9px; color: #96a1bc; font-family: Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; }
.log { margin-top: 16px; background: #0e1119; border: 1px solid #2d354b; border-radius: 12px; padding: 13px; }
.log pre { white-space: pre-wrap; margin: 8px 0 0; color: #c8d2e8; font: 12px/1.45 Consolas, monospace; }
</style>
</head>
<body>
<main>
<div class="logo">WZHK<span>MEDIA</span></div>
<div class="frame">
<h1>TrackPrompt final-render progress</h1>
<div class="subtle">Refreshes every $RefreshSeconds seconds · updated $updated</div>
<div class="resolution-banner">$resolutionMarkup</div>

<section class="stats">
<div class="card"><div class="label">Profile</div><div class="value">$profileNameMarkup</div></div>
<div class="card"><div class="label">Profile ID</div><div class="value">$profileIdMarkup</div></div>
<div class="card"><div class="label">FPS / format</div><div class="value">$Fps fps · $profileFormatMarkup</div></div>
<div class="card"><div class="label">Chunk size</div><div class="value">$profileChunkSize frames</div></div>
<div class="card"><div class="label">Samples / output</div><div class="value">$profileSamples / $profileBitDepth-bit c$profileCompression</div></div>
<div class="card"><div class="label">Shadow strategy</div><div class="value">$(Convert-ToHtmlText -Value $shadowStrategy)</div></div>
<div class="card"><div class="label">Fog Glow strategy</div><div class="value">$(Convert-ToHtmlText -Value $fogGlowStrategy)</div></div>
<div class="card"><div class="label">Machine calibration</div><div class="value">$(Convert-ToHtmlText -Value $machineCalibrationId)</div></div>
<div class="card"><div class="label">Calibrated profile</div><div class="value">$(if ($recommendedForMachine) { 'RECOMMENDED FOR THIS PC' } else { 'not machine-recommended' })</div></div>
<div class="card"><div class="label">Scratch / publication</div><div class="value truncate" title="$(Convert-ToHtmlText -Value ($scratchDrive + ' / ' + $publicationDrive))">$(Convert-ToHtmlText -Value ($scratchDrive + ' / ' + $publicationDrive))</div></div>
<div class="card"><div class="label">Scene SHA-12</div><div class="value">$(Convert-ToHtmlText -Value $sceneHashShort)</div></div>
<div class="card"><div class="label">Profile SHA-12</div><div class="value">$(Convert-ToHtmlText -Value $profileHashShort)</div></div>
</section>

$progressMarkup

<section class="stats">
$dynamicCardsMarkup
</section>

$inflightMarkup

$latestFrameMarkup
<div class="path">Output: $outputPathMarkup</div>
<div class="path">Profile: $profilePathMarkup</div>

<section class="log">
<div class="label">Latest Blender stdout</div>
<pre>$latestLogMarkup</pre>
</section>
</div>
</main>
<script>document.querySelectorAll('[data-copy]').forEach(function(button){button.addEventListener('click',function(){navigator.clipboard.writeText(button.getAttribute('data-copy')||'');button.textContent='Copied';});});</script>
</body>
</html>
"@

    Set-Content -LiteralPath $dashboardPath -Value $html -Encoding UTF8

    if (-not $openedDashboard -and -not $NoBrowser) {
        Start-Process $dashboardPath
        $openedDashboard = $true
    }

    Write-Progress `
        -Activity "WZHK Media final render" `
        -Status "$renderedFrameText rendered; $publishedFrameText published; ETA $etaText" `
        -PercentComplete $renderedPercent

    if ($publishedCount -ge $TotalFrames) {
        Write-Host "All $TotalFrames frames are validated and published. Dashboard: $dashboardPath"
        if ($OpenOutputWhenComplete -and -not $NoBrowser) {
            Start-Process explorer.exe -ArgumentList @("`"$root`"")
        }
        break
    }

    Start-Sleep -Seconds $RefreshSeconds
}
