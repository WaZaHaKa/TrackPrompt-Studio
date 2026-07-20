Set-StrictMode -Version Latest

$script:CalibrationSchemaVersion = "1.0.0"
$script:CalibrationKind = "trackprompt-render-calibration"

function Get-WzhkCalibrationPercentile {
    param(
        [Parameter(Mandatory = $true)][double[]]$Values,
        [ValidateRange(0.0, 100.0)][double]$Percentile
    )

    $finite = @($Values | Where-Object { -not [double]::IsNaN($_) -and -not [double]::IsInfinity($_) } | Sort-Object)
    if ($finite.Count -eq 0) { throw "At least one finite calibration value is required." }
    if ($finite.Count -eq 1) { return [double]$finite[0] }
    $position = ($Percentile / 100.0) * ($finite.Count - 1)
    $lower = [int][Math]::Floor($position)
    $upper = [int][Math]::Ceiling($position)
    if ($lower -eq $upper) { return [double]$finite[$lower] }
    $fraction = $position - $lower
    return [double]$finite[$lower] + (([double]$finite[$upper] - [double]$finite[$lower]) * $fraction)
}

function Get-WzhkCalibrationStatistics {
    param(
        [Parameter(Mandatory = $true)][double[]]$Values,
        [int]$ColdValueCount = 1
    )

    $finite = @($Values | Where-Object { $_ -gt 0.0 -and -not [double]::IsNaN($_) -and -not [double]::IsInfinity($_) })
    if ($finite.Count -eq 0) { throw "Calibration timings must contain at least one positive finite value." }
    if ($ColdValueCount -lt 0 -or $ColdValueCount -ge $finite.Count) { $ColdValueCount = 0 }
    $warm = if ($ColdValueCount -gt 0) { @($finite | Select-Object -Skip $ColdValueCount) } else { @($finite) }
    $mean = ($warm | Measure-Object -Average).Average
    $median = Get-WzhkCalibrationPercentile -Values $warm -Percentile 50
    $p90 = Get-WzhkCalibrationPercentile -Values $warm -Percentile 90
    $confidence = if ($warm.Count -ge 60) { "HIGH" } elseif ($warm.Count -ge 20) { "MEDIUM" } else { "LOW" }
    return [pscustomobject][ordered]@{
        Count = $finite.Count
        WarmCount = $warm.Count
        ColdStartSeconds = $(if ($ColdValueCount -gt 0) { [double](($finite | Select-Object -First $ColdValueCount | Measure-Object -Sum).Sum) } else { 0.0 })
        WarmMeanSeconds = [Math]::Round([double]$mean, 6)
        WarmMedianSeconds = [Math]::Round([double]$median, 6)
        P90Seconds = [Math]::Round([double]$p90, 6)
        WorstSeconds = [Math]::Round([double](($warm | Measure-Object -Maximum).Maximum), 6)
        FramesPerHour = [Math]::Round(3600.0 / [double]$median, 3)
        Confidence = $confidence
    }
}

function Get-WzhkAdaptiveChunkPlan {
    param(
        [Parameter(Mandatory = $true)][ValidateRange(0.001, 3600.0)][double]$MeasuredWarmSecondsPerFrame,
        [ValidateRange(1, 120)][int]$TargetChunkMinutes = 15,
        [ValidateRange(1, 600)][int]$MinimumFrames = 15,
        [ValidateRange(1, 600)][int]$MaximumFrames = 600,
        [ValidateSet(1, 5, 10)][int]$Alignment = 5,
        [int]$FrameCount = 13029
    )

    if ($MaximumFrames -lt $MinimumFrames) { throw "MaximumFrames must be at least MinimumFrames." }
    $targetSeconds = $TargetChunkMinutes * 60.0
    $unclamped = [int][Math]::Round($targetSeconds / $MeasuredWarmSecondsPerFrame)
    $effectiveMinimum = $MinimumFrames
    if ($MeasuredWarmSecondsPerFrame * $MinimumFrames -gt ($targetSeconds * 2.0)) {
        $effectiveMinimum = [Math]::Max(1, [int][Math]::Floor($targetSeconds / $MeasuredWarmSecondsPerFrame))
    }
    $clamped = [Math]::Max($effectiveMinimum, [Math]::Min($MaximumFrames, $unclamped))
    $aligned = [int]([Math]::Round($clamped / [double]$Alignment) * $Alignment)
    $aligned = [Math]::Max($effectiveMinimum, [Math]::Min($MaximumFrames, $aligned))
    $chunkCount = if ($FrameCount -gt 0) { [int][Math]::Ceiling($FrameCount / [double]$aligned) } else { 0 }
    return [pscustomobject][ordered]@{
        FramesPerChunk = $aligned
        TargetChunkMinutes = $TargetChunkMinutes
        PredictedChunkSeconds = [Math]::Round($aligned * $MeasuredWarmSecondsPerFrame, 3)
        PredictedChunkMinutes = [Math]::Round(($aligned * $MeasuredWarmSecondsPerFrame) / 60.0, 3)
        ChunkCount = $chunkCount
        MaximumUnpublishedFrames = $aligned
        Rationale = [string]::Format(
            "Calibrated to approximately {0} minutes from a {1:N3} s/frame warm median; clamped to {2}-{3} and aligned to {4}.",
            $TargetChunkMinutes,
            $MeasuredWarmSecondsPerFrame,
            $effectiveMinimum,
            $MaximumFrames,
            $Alignment
        )
    }
}

function Get-WzhkMachineFingerprint {
    param([string]$BlenderExecutable = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")

    $cpuName = [string]$env:PROCESSOR_IDENTIFIER
    try {
        $registryCpu = Get-ItemProperty -LiteralPath "HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor\0" -ErrorAction Stop
        if (-not [string]::IsNullOrWhiteSpace([string]$registryCpu.ProcessorNameString)) { $cpuName = [string]$registryCpu.ProcessorNameString.Trim() }
    }
    catch { }
    $logical = [Environment]::ProcessorCount
    $physical = $logical
    try {
        $cpuInfo = @(Get-CimInstance Win32_Processor -ErrorAction Stop)
        if ($cpuInfo.Count -gt 0) { $physical = [int](($cpuInfo | Measure-Object NumberOfCores -Sum).Sum) }
    }
    catch {
        $coreMatch = [regex]::Match($cpuName, '(?i)(\d+)\s*-?\s*core')
        if ($coreMatch.Success) { $physical = [int]$coreMatch.Groups[1].Value }
    }
    $ramBytes = 0L
    try {
        $computer = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
        $ramBytes = [int64]$computer.TotalPhysicalMemory
    }
    catch {
        try {
            Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
            $ramBytes = [int64](New-Object Microsoft.VisualBasic.Devices.ComputerInfo).TotalPhysicalMemory
        }
        catch { }
    }
    $gpuName = "unknown"
    $driver = "unknown"
    $vramMiB = 0
    $nvidia = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($null -ne $nvidia) {
        try {
            $gpuLine = @(& $nvidia.Source --query-gpu=name,driver_version,memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
            if ($gpuLine.Count -eq 1) {
                $parts = @([string]$gpuLine[0] -split ',' | ForEach-Object { $_.Trim() })
                if ($parts.Count -ge 3) { $gpuName = $parts[0]; $driver = $parts[1]; $vramMiB = [int][double]$parts[2] }
            }
        }
        catch { }
    }
    $blenderVersion = "unknown"
    if (Test-Path -LiteralPath $BlenderExecutable -PathType Leaf) {
        try {
            $productVersion = [string](Get-Item -LiteralPath $BlenderExecutable -ErrorAction Stop).VersionInfo.ProductVersion
            if (-not [string]::IsNullOrWhiteSpace($productVersion)) { $blenderVersion = "Blender " + $productVersion.Trim() }
        }
        catch { }
    }
    $identity = [ordered]@{
        computerName = [Environment]::MachineName
        os = [Environment]::OSVersion.VersionString
        cpuModel = $cpuName
        physicalCores = $physical
        logicalProcessors = $logical
        ramBytes = $ramBytes
        gpuModel = $gpuName
        vramMiB = $vramMiB
        gpuDriver = $driver
        blenderVersion = $blenderVersion
    }
    $json = ($identity | ConvertTo-Json -Compress)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $fingerprint = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($json)))).Replace("-", "").ToUpperInvariant() }
    finally { $sha.Dispose() }
    return [pscustomobject][ordered]@{
        MachineFingerprint = $fingerprint
        MachineId = $fingerprint.Substring(0, 12).ToLowerInvariant()
        ComputerName = $identity.computerName
        OperatingSystem = $identity.os
        CpuModel = $identity.cpuModel
        PhysicalCores = $identity.physicalCores
        LogicalProcessors = $identity.logicalProcessors
        RamBytes = $identity.ramBytes
        GpuModel = $identity.gpuModel
        VramMiB = $identity.vramMiB
        GpuDriver = $identity.gpuDriver
        BlenderVersion = $identity.blenderVersion
    }
}

function Get-WzhkRenderSafetyAudit {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [ValidateRange(1, 1440)][int]$InflightActivityWindowMinutes = 30
    )

    $repositoryFull = [IO.Path]::GetFullPath($RepositoryRoot)
    $blender = @(Get-Process -Name blender -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime, CPU, WorkingSet64)
    $rendererProcesses = New-Object System.Collections.Generic.List[object]
    $rendererInspectionAvailable = $true
    try {
        foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.CommandLine -match 'render-trackprompt-final\.ps1' })) {
            $rendererProcesses.Add([pscustomobject]@{ ProcessId = $process.ProcessId; Name = $process.Name; CommandLine = $process.CommandLine })
        }
    }
    catch { $rendererInspectionAvailable = $false }
    $mutexExists = $false
    try {
        $mutex = [Threading.Mutex]::OpenExisting("Local\TrackPromptFinalRenderGpu")
        $mutexExists = $true
        $mutex.Dispose()
    }
    catch [Threading.WaitHandleCannotBeOpenedException] { $mutexExists = $false }

    $nowUtc = (Get-Date).ToUniversalTime()
    $activityCutoffUtc = $nowUtc.AddMinutes(-1 * $InflightActivityWindowMinutes)
    $inflight = New-Object System.Collections.Generic.List[object]
    $productionOutputs = @{}
    foreach ($rootDefinition in @(
        [pscustomobject]@{ Path = (Join-Path $repositoryFull "final-output"); Kind = "production" },
        [pscustomobject]@{ Path = (Join-Path $repositoryFull "test-output"); Kind = "test" }
    )) {
        $root = [string]$rootDefinition.Path
        if (Test-Path -LiteralPath $root -PathType Container) {
            foreach ($directory in @(Get-ChildItem -LiteralPath $root -Directory -Recurse -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -like ".inflight-*" })) {
                $outputDirectory = $directory.Parent
                if ($null -ne $outputDirectory -and $outputDirectory.Name -ieq "checkpoints") { $outputDirectory = $outputDirectory.Parent }
                if ($null -eq $outputDirectory) { continue }

                $lastActivityUtc = $directory.LastWriteTimeUtc
                foreach ($entry in @(Get-ChildItem -LiteralPath $directory.FullName -Recurse -Force -ErrorAction SilentlyContinue)) {
                    if ($entry.LastWriteTimeUtc -gt $lastActivityUtc) { $lastActivityUtc = $entry.LastWriteTimeUtc }
                }

                $manifestPath = Join-Path $outputDirectory.FullName "manifests\render-manifest.json"
                $manifestStatus = "missing"
                $sceneSha256 = ""
                $profileSha256 = ""
                $publishedFrameCount = 0
                if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
                    try {
                        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
                        $manifestStatus = [string]$manifest.status
                        if ([string]::IsNullOrWhiteSpace($manifestStatus)) { $manifestStatus = "unknown" }
                        if ($null -ne $manifest.PSObject.Properties["scene"] -and $null -ne $manifest.scene.PSObject.Properties["sha256"]) { $sceneSha256 = [string]$manifest.scene.sha256 }
                        if ($null -ne $manifest.PSObject.Properties["renderProfile"] -and $null -ne $manifest.renderProfile.PSObject.Properties["sha256"]) { $profileSha256 = [string]$manifest.renderProfile.sha256 }
                        if ($null -ne $manifest.PSObject.Properties["frameSet"] -and $null -ne $manifest.frameSet.PSObject.Properties["validFrameCount"]) { $publishedFrameCount = [int]$manifest.frameSet.validFrameCount }
                    }
                    catch { $manifestStatus = "invalid" }
                }
                if ($publishedFrameCount -eq 0) {
                    $framesPath = Join-Path $outputDirectory.FullName "frames"
                    if (Test-Path -LiteralPath $framesPath -PathType Container) {
                        $publishedFrameCount = @(Get-ChildItem -LiteralPath $framesPath -File -Force -Filter "frame_*.png" -ErrorAction SilentlyContinue).Count
                    }
                }

                $recent = $lastActivityUtc -ge $activityCutoffUtc
                $isProduction = [string]$rootDefinition.Kind -eq "production"
                $record = [pscustomobject][ordered]@{
                    FullName = $directory.FullName
                    OutputDirectory = $outputDirectory.FullName
                    RootKind = [string]$rootDefinition.Kind
                    CreationTimeUtc = $directory.CreationTimeUtc
                    LastActivityUtc = $lastActivityUtc
                    AgeMinutes = [Math]::Round(($nowUtc - $lastActivityUtc).TotalMinutes, 2)
                    Recent = $recent
                    BlocksCalibration = ($isProduction -and $recent)
                    ManifestPath = $manifestPath
                    ManifestStatus = $manifestStatus
                    SceneSha256 = $sceneSha256
                    ProfileSha256 = $profileSha256
                    PublishedFrameCount = $publishedFrameCount
                }
                $inflight.Add($record)
                if ($isProduction) { $productionOutputs[$outputDirectory.FullName.ToLowerInvariant()] = $record }
            }
        }
    }

    $activeInflight = @($inflight.ToArray() | Where-Object { $_.BlocksCalibration })
    $staleInflight = @($inflight.ToArray() | Where-Object { -not $_.BlocksCalibration })
    $safe = (
        $blender.Count -eq 0 -and
        $rendererProcesses.Count -eq 0 -and
        -not $mutexExists -and
        $activeInflight.Count -eq 0
    )
    $reason = if ($blender.Count -gt 0 -or $rendererProcesses.Count -gt 0 -or $mutexExists) {
        "GPU calibration is blocked while Blender, the final-render launcher, or the production mutex is active."
    }
    elseif ($activeInflight.Count -gt 0) {
        "GPU calibration is blocked because a production in-flight checkpoint changed within the last $InflightActivityWindowMinutes minutes."
    }
    elseif ($staleInflight.Count -gt 0) {
        "No active GPU render was detected; stale or test in-flight checkpoints were reported for operator review."
    }
    else {
        "No active Blender process, final-render launcher, production mutex, or recent production in-flight checkpoint was detected."
    }
    return [pscustomobject][ordered]@{
        SafeForGpuCalibration = $safe
        BlenderProcesses = $blender
        RendererProcesses = $rendererProcesses.ToArray()
        RendererProcessInspectionAvailable = $rendererInspectionAvailable
        ProductionMutexExists = $mutexExists
        InflightActivityWindowMinutes = $InflightActivityWindowMinutes
        InflightDirectories = $inflight.ToArray()
        ActiveInflightDirectories = $activeInflight
        StaleOrTestInflightDirectories = $staleInflight
        ProductionOutputs = @($productionOutputs.Values)
        ActiveSceneSha256 = @($activeInflight | ForEach-Object { $_.SceneSha256 } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)
        ActiveProfileSha256 = @($activeInflight | ForEach-Object { $_.ProfileSha256 } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)
        PublishedFrameCount = [int](($productionOutputs.Values | Measure-Object PublishedFrameCount -Sum).Sum)
        Reason = $reason
    }
}

function Test-WzhkCalibrationValidity {
    param(
        [Parameter(Mandatory = $true)][object]$Calibration,
        [Parameter(Mandatory = $true)][object]$CurrentIdentity
    )

    $issues = New-Object System.Collections.Generic.List[object]
    $checks = @(
        @("machineFingerprint", "machine-fingerprint-changed", $true),
        @("sceneSha256", "scene-changed", $true),
        @("sceneManifestSha256", "scene-manifest-changed", $true),
        @("blenderVersion", "blender-version-changed", $true),
        @("gpuModel", "gpu-changed", $true),
        @("gpuDriver", "gpu-driver-changed", $false),
        @("outputDrive", "output-drive-changed", $false),
        @("filesystem", "filesystem-changed", $false),
        @("profileSha256", "profile-changed", $true),
        @("resolution", "resolution-changed", $true),
        @("imageFormat", "output-format-changed", $true),
        @("renderSettingsSha256", "render-settings-changed", $true),
        @("compositorSha256", "compositor-changed", $true)
    )
    foreach ($check in $checks) {
        $name = [string]$check[0]
        $calibrationProperty = $Calibration.PSObject.Properties[$name]
        $currentProperty = $CurrentIdentity.PSObject.Properties[$name]
        if ($null -eq $calibrationProperty -or $null -eq $currentProperty) { continue }
        if ([string]$calibrationProperty.Value -cne [string]$currentProperty.Value) {
            $issues.Add([pscustomobject]@{ Code = [string]$check[1]; Critical = [bool]$check[2]; Expected = $calibrationProperty.Value; Actual = $currentProperty.Value })
        }
    }
    return [pscustomobject][ordered]@{
        Valid = (@($issues | Where-Object { $_.Critical }).Count -eq 0)
        Warning = ($issues.Count -gt 0)
        Issues = $issues.ToArray()
    }
}

function Select-WzhkRecommendedCalibrationProfile {
    param([Parameter(Mandatory = $true)][object[]]$Candidates)

    $passing = @($Candidates | Where-Object { [string]$_.QualityResult -in @("PASS", "PASS WITH DOCUMENTED CAVEAT") -and [double]$_.WarmMedianSeconds -gt 0.0 })
    if ($passing.Count -eq 0) { throw "No calibrated profile passed the visual and technical quality gates." }
    $fastest = @($passing | Sort-Object @{ Expression = { [double]$_.WarmMedianSeconds }; Ascending = $true }, @{ Expression = { [double]$_.ProjectedStorageBytes }; Ascending = $true } | Select-Object -First 1)[0]
    $threshold = [double]$fastest.WarmMedianSeconds * 1.05
    $near = @($passing | Where-Object { [double]$_.WarmMedianSeconds -le $threshold })
    $qualityRank = @{ "PASS" = 0; "PASS WITH DOCUMENTED CAVEAT" = 1 }
    $winner = @($near | Sort-Object @{ Expression = { $qualityRank[[string]$_.QualityResult] }; Ascending = $true }, @{ Expression = { [double]$_.ProjectedStorageBytes }; Ascending = $true }, @{ Expression = { [double]$_.WarmMedianSeconds }; Ascending = $true } | Select-Object -First 1)[0]
    $winner | Add-Member -NotePropertyName RecommendationReason -NotePropertyValue ([string]::Format(
        "Fastest passing candidate under the measured warm-median rule; candidates within 5 percent were resolved by visual quality, storage, then recovery simplicity. Selected {0:N3} s/frame ({1:N1} frames/hour).",
        [double]$winner.WarmMedianSeconds,
        3600.0 / [double]$winner.WarmMedianSeconds
    )) -Force
    return $winner
}

function Get-WzhkCalibrationEvidencePath {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$MachineId,
        [Parameter(Mandatory = $true)][string]$SceneSha256,
        [Parameter(Mandatory = $true)][string]$CalibrationId
    )
    if ($MachineId -notmatch '^[A-Za-z0-9._-]{3,64}$' -or $CalibrationId -notmatch '^[A-Za-z0-9._-]{3,96}$' -or $SceneSha256 -notmatch '^[A-Fa-f0-9]{64}$') { throw "Calibration path identity is unsafe." }
    return Join-Path $RepositoryRoot ("test-output\render-calibration\{0}\{1}\{2}" -f $MachineId, $SceneSha256.Substring(0, 12).ToLowerInvariant(), $CalibrationId)
}

function Save-WzhkCalibrationJson {
    param(
        [Parameter(Mandatory = $true)][object]$Calibration,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $full = [IO.Path]::GetFullPath($Path)
    $directory = [IO.Path]::GetDirectoryName($full)
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { $null = New-Item -ItemType Directory -Path $directory }
    $temporary = Join-Path $directory ("." + [IO.Path]::GetFileName($full) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [IO.File]::WriteAllText($temporary, (($Calibration | ConvertTo-Json -Depth 100) + "`n"), (New-Object Text.UTF8Encoding($false)))
        $null = Get-Content -LiteralPath $temporary -Raw | ConvertFrom-Json
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $backup = $temporary + ".bak"
            try { [IO.File]::Replace($temporary, $full, $backup, $true) }
            finally { if (Test-Path -LiteralPath $backup -PathType Leaf) { Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue } }
        }
        else { [IO.File]::Move($temporary, $full) }
    }
    finally { if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue } }
    return $full
}

function Save-WzhkRecommendedProfilePointer {
    param(
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$SceneSha256,
        [Parameter(Mandatory = $true)][string]$CalibrationId,
        [Parameter(Mandatory = $true)][string]$RecommendationReason,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $profileFull = [IO.Path]::GetFullPath($ProfilePath)
    $profile = Get-Content -LiteralPath $profileFull -Raw | ConvertFrom-Json
    $pointer = [pscustomobject][ordered]@{
        schemaVersion = $script:CalibrationSchemaVersion
        kind = "trackprompt-recommended-render-profile-pointer"
        profilePath = $profileFull
        profileId = [string]$profile.profileId
        profileSha256 = (Get-FileHash -LiteralPath $profileFull -Algorithm SHA256).Hash.ToUpperInvariant()
        sceneSha256 = $SceneSha256.ToUpperInvariant()
        calibrationId = $CalibrationId
        selectedTimestamp = (Get-Date).ToUniversalTime().ToString("o")
        recommendationReason = $RecommendationReason
    }
    return Save-WzhkCalibrationJson -Calibration $pointer -Path $Path
}

function New-WzhkCalibratedRenderProfile {
    param(
        [Parameter(Mandatory = $true)][object]$BaseProfile,
        [Parameter(Mandatory = $true)][string]$DisplayName,
        [Parameter(Mandatory = $true)][string]$ProfileId,
        [Parameter(Mandatory = $true)][ValidateRange(16, 16384)][int]$Width,
        [Parameter(Mandatory = $true)][ValidateRange(16, 16384)][int]$Height,
        [Parameter(Mandatory = $true)][ValidateRange(1, 4096)][int]$Samples,
        [ValidateSet(8, 16)][int]$BitDepth = 8,
        [ValidateRange(0, 100)][int]$Compression = 15,
        [ValidateSet("128", "256", "512", "1024", "2048")][string]$ShadowPoolSize = "256",
        [Parameter(Mandatory = $true)][ValidateRange(0.001, 3600.0)][double]$WarmMedianSeconds,
        [Parameter(Mandatory = $true)][ValidateRange(0.001, 3600.0)][double]$P90Seconds,
        [Parameter(Mandatory = $true)][long]$ProjectedStorageBytes,
        [Parameter(Mandatory = $true)][string]$CalibrationId,
        [Parameter(Mandatory = $true)][string]$CalibrationEvidencePath,
        [Parameter(Mandatory = $true)][ValidateSet("PASS", "PASS WITH DOCUMENTED CAVEAT")][string]$QualityResult,
        [Parameter(Mandatory = $true)][string]$RecommendationReason,
        [string]$Confidence = "MEDIUM",
        [switch]$RecommendedForMachine,
        [string]$ShadowStrategy = "calibrated-essential-only",
        [string]$FogGlowStrategy = "calibrated-blender-fog-glow",
        [ValidateRange(1, 120)][int]$TargetChunkMinutes = 15
    )
    if (($Width % 2) -ne 0 -or ($Height % 2) -ne 0) { throw "Calibrated output dimensions must be even." }
    if ($ProjectedStorageBytes -lt 1) { throw "Projected storage must be a positive byte count." }
    $profile = Normalize-WzhkRenderProfile -Profile $BaseProfile
    $profile.id = ([Guid]::NewGuid().ToString("D")).ToUpperInvariant()
    $profile.profileId = (ConvertTo-WzhkProfileSlug -Name $ProfileId).ToUpperInvariant()
    $profile.displayName = ConvertTo-WzhkSafeProfileName -Name $DisplayName
    $profile.templateId = "CALIBRATED-CUSTOM"
    $profile.resolution.width = $Width
    $profile.resolution.height = $Height
    $profile.resolution.label = [string]::Format("NATIVE {0}x{1}", $Width, $Height)
    if ($null -ne $profile.PSObject.Properties["dashboard"] -and $null -ne $profile.dashboard.PSObject.Properties["resolutionLabel"]) {
        $dashboardPrefix = switch ([string]::Format("{0}x{1}", $Width, $Height)) {
            "1280x720" { "HD" }
            "1920x1080" { "FULL HD" }
            "2560x1440" { "QHD" }
            "3840x2160" { "4K UHD" }
            default { "NATIVE" }
        }
        $profile.dashboard.resolutionLabel = [string]::Format("{0} - {1}x{2}", $dashboardPrefix, $Width, $Height)
    }
    $profile.aspect.display = "16:9"
    $profile.render.samples = $Samples
    $profile.render.shadowPoolSize = $ShadowPoolSize
    $profile.render.motionBlur = $false
    $profile.render.rayTracing = $false
    $profile.render.highQualityNormals = $false
    $profile.imageSequence.format = "PNG"
    $profile.imageSequence.extension = "png"
    $profile.imageSequence.bitDepth = $BitDepth
    $profile.imageSequence.colorMode = "RGB"
    $profile.imageSequence.compression = $Compression
    $profile.imageSequence.filenamePattern = "frame_%06d.png"
    $chunk = Get-WzhkAdaptiveChunkPlan -MeasuredWarmSecondsPerFrame $WarmMedianSeconds -TargetChunkMinutes $TargetChunkMinutes -FrameCount ([int]$profile.timeline.frameCount)
    $profile.chunking.framesPerChunk = $chunk.FramesPerChunk
    $profile.chunking.rationale = $chunk.Rationale
    $profile.production.framesPerChunk = $chunk.FramesPerChunk
    if ($null -eq $profile.production.PSObject.Properties["chunkSize"]) { Add-Member -InputObject $profile.production -NotePropertyName chunkSize -NotePropertyValue $chunk.FramesPerChunk }
    else { $profile.production.chunkSize = $chunk.FramesPerChunk }
    $profile.production.maximumFramesPerChunk = 600
    $profile.storage.plannedFrameSequenceGiB = [Math]::Round($ProjectedStorageBytes / 1GB, 3)
    $basePixels = [double]([int]$BaseProfile.resolution.width * [int]$BaseProfile.resolution.height)
    $pixelScale = if ($basePixels -gt 0) { ([double]$Width * [double]$Height) / $basePixels } else { 1.0 }
    $profile.storage.projectedMasterGiB = [Math]::Round([double]$BaseProfile.storage.projectedMasterGiB * $pixelScale, 3)
    $profile.storage.projectedDeliveryGiB = [Math]::Round([double]$BaseProfile.storage.projectedDeliveryGiB * $pixelScale, 3)
    $requiredGiB = (
        [double]$profile.storage.plannedFrameSequenceGiB +
        [double]$profile.storage.projectedMasterGiB +
        [double]$profile.storage.projectedDeliveryGiB +
        [double]$profile.storage.supportReserveGiB
    ) * [double]$profile.storage.contingencyMultiplier
    $profile.storage.minimumLaunchFreeGiB = [Math]::Ceiling($requiredGiB)
    $profile.estimates.plannedFrameSequenceGiB = $profile.storage.plannedFrameSequenceGiB
    if ($null -eq $profile.estimates.PSObject.Properties["minimumLaunchFreeGiB"]) {
        Add-Member -InputObject $profile.estimates -NotePropertyName minimumLaunchFreeGiB -NotePropertyValue $profile.storage.minimumLaunchFreeGiB
    }
    else { $profile.estimates.minimumLaunchFreeGiB = $profile.storage.minimumLaunchFreeGiB }
    $totalStorage = [string]::Format("{0:N0} GiB minimum free", $profile.storage.minimumLaunchFreeGiB)
    if ($null -eq $profile.estimates.PSObject.Properties["totalStorage"]) {
        Add-Member -InputObject $profile.estimates -NotePropertyName totalStorage -NotePropertyValue $totalStorage
    }
    else { $profile.estimates.totalStorage = $totalStorage }
    $frameSequenceSize = [string]::Format("{0:N3} GiB", $profile.storage.plannedFrameSequenceGiB)
    if ($null -eq $profile.estimates.PSObject.Properties["frameSequenceSize"]) { Add-Member -InputObject $profile.estimates -NotePropertyName frameSequenceSize -NotePropertyValue $frameSequenceSize }
    else { $profile.estimates.frameSequenceSize = $frameSequenceSize }
    $frameCount = [int]$profile.timeline.frameCount
    $expectedHours = ($WarmMedianSeconds * $frameCount) / 3600.0
    $conservativeHours = ($P90Seconds * $frameCount) / 3600.0
    $calibration = [pscustomobject][ordered]@{
        schemaVersion = $script:CalibrationSchemaVersion
        calibrationId = $CalibrationId
        evidencePath = [IO.Path]::GetFullPath($CalibrationEvidencePath)
        recommendedForMachine = [bool]$RecommendedForMachine
        recommendationReason = $RecommendationReason
        measuredWarmMedianSeconds = $WarmMedianSeconds
        measuredP90Seconds = $P90Seconds
        framesPerHour = [Math]::Round(3600.0 / $WarmMedianSeconds, 3)
        expectedTotalHours = [Math]::Round($expectedHours, 3)
        conservativeTotalHours = [Math]::Round($conservativeHours, 3)
        projectedStorageBytes = $ProjectedStorageBytes
        qualityGateResult = $QualityResult
        confidence = $Confidence.ToUpperInvariant()
        nativeResolution = [string]::Format("{0}x{1}", $Width, $Height)
        samples = $Samples
        shadowStrategy = $ShadowStrategy
        shadowPoolSize = $ShadowPoolSize
        fogGlowStrategy = $FogGlowStrategy
        bitDepth = $BitDepth
        compression = $Compression
        outputStrategy = "single-drive measured calibration path; hybrid scratch/publish remains disabled until separately benchmarked"
        adaptiveChunk = $chunk
    }
    if ($null -eq $profile.PSObject.Properties["calibration"]) { Add-Member -InputObject $profile -NotePropertyName calibration -NotePropertyValue $calibration }
    else { $profile.calibration = $calibration }
    $profile.authorization.profile = $profile.profileId
    $profile = Set-WzhkProfileAuthorizationPending -Profile $profile -Reason "Calibrated profile requires fresh authorization bound to its saved-file hash."
    return $profile
}

Export-ModuleMember -Function `
    Get-WzhkCalibrationPercentile, `
    Get-WzhkCalibrationStatistics, `
    Get-WzhkAdaptiveChunkPlan, `
    Get-WzhkMachineFingerprint, `
    Get-WzhkRenderSafetyAudit, `
    Test-WzhkCalibrationValidity, `
    Select-WzhkRecommendedCalibrationProfile, `
    Get-WzhkCalibrationEvidencePath, `
    Save-WzhkCalibrationJson, `
    Save-WzhkRecommendedProfilePointer, `
    New-WzhkCalibratedRenderProfile
