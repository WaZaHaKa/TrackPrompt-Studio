[CmdletBinding()]
param(
    [ValidateSet("Plan", "AddShadowPoolCandidates", "RunCandidate", "ReviewCandidate", "Finalize")][string]$Mode = "Plan",
    [Parameter(Mandatory = $true)][string]$ApprovedScenePath,
    [Parameter(Mandatory = $true)][string]$BaseProfilePath,
    [ValidateSet("FASTEST ACCEPTABLE", "RECOMMENDED BALANCED", "HIGHEST PRACTICAL QUALITY", "CUSTOM")][string]$Goal = "RECOMMENDED BALANCED",
    [string]$CalibrationDirectory = "",
    [string]$CandidateId = "",
    [ValidateSet("PASS", "PASS WITH DOCUMENTED CAVEAT", "FAIL", "PENDING HUMAN REVIEW")][string]$QualityResult = "PENDING HUMAN REVIEW",
    [string]$QualityNotes = "",
    [string]$BlenderExecutable = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    [string]$ProfileOutputDirectory = "",
    [ValidateRange(1, 8)][int]$MaximumRanges = 8
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

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$moduleRoot = Join-Path $PSScriptRoot "wzhk-launcher"
Import-Module (Join-Path $moduleRoot "WZHK.Profiles.psm1") -Force -DisableNameChecking
Import-Module (Join-Path $moduleRoot "WZHK.Calibration.psm1") -Force -DisableNameChecking

function Write-CalibrationResult {
    param([Parameter(Mandatory = $true)][object]$Value)
    Write-Output ($Value | ConvertTo-Json -Depth 100)
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Get-CalibrationPlanPath {
    param([Parameter(Mandatory = $true)][string]$Directory)
    return Join-Path ([IO.Path]::GetFullPath($Directory)) "calibration.json"
}

function New-CandidateProfile {
    param(
        [Parameter(Mandatory = $true)][object]$Base,
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $copy = Normalize-WzhkRenderProfile -Profile $Base
    $copy.id = ([Guid]::NewGuid().ToString("D")).ToUpperInvariant()
    $copy.profileId = ([string]$Candidate.id).ToUpperInvariant()
    $copy.displayName = "CALIBRATION " + ([string]$Candidate.id).ToUpperInvariant()
    $copy.templateId = "CALIBRATION-CANDIDATE"
    $copy.resolution.width = [int]$Candidate.width
    $copy.resolution.height = [int]$Candidate.height
    $copy.resolution.label = [string]::Format("NATIVE {0}x{1}", $Candidate.width, $Candidate.height)
    $copy.render.samples = [int]$Candidate.samples
    if ($null -ne $Candidate.PSObject.Properties["shadowPoolSize"]) {
        $copy.render.shadowPoolSize = [string]$Candidate.shadowPoolSize
    }
    $copy.render.motionBlur = $false
    $copy.render.rayTracing = $false
    $copy.render.highQualityNormals = $false
    $copy.imageSequence.format = "PNG"
    $copy.imageSequence.extension = "png"
    $copy.imageSequence.bitDepth = [int]$Candidate.bitDepth
    $copy.imageSequence.colorMode = "RGB"
    $copy.imageSequence.compression = [int]$Candidate.compression
    $copy.imageSequence.filenamePattern = "frame_%06d.png"
    $copy.chunking.framesPerChunk = 60
    $copy.production.framesPerChunk = 60
    if ($null -eq $copy.production.PSObject.Properties["chunkSize"]) { Add-Member -InputObject $copy.production -NotePropertyName chunkSize -NotePropertyValue 60 }
    else { $copy.production.chunkSize = 60 }
    $copy.authorization.profile = $copy.profileId
    if ($null -eq $copy.PSObject.Properties["calibrationCandidate"]) {
        Add-Member -InputObject $copy -NotePropertyName calibrationCandidate -NotePropertyValue ([pscustomobject]$Candidate)
    }
    else { $copy.calibrationCandidate = [pscustomobject]$Candidate }
    $copy = Set-WzhkProfileAuthorizationPending -Profile $copy -Reason "Calibration candidates cannot inherit production authorization."
    $saved = Save-WzhkRenderProfile -Profile $copy -Path $Path -Force
    return $saved
}

function Get-CandidateMatrix {
    $items = New-Object System.Collections.Generic.List[object]
    $definitions = @(
        @("720p", 1280, 720, @(8, 16, 24)),
        @("1080p", 1920, 1080, @(8, 16, 24, 32)),
        @("1440p", 2560, 1440, @(16, 24, 32)),
        @("4k", 3840, 2160, @(16, 24, 32))
    )
    foreach ($definition in $definitions) {
        foreach ($samples in @($definition[3])) {
            foreach ($format in @(@(8, 0), @(8, 5), @(8, 15), @(16, 0), @(16, 15))) {
                $id = [string]::Format("cal-{0}-s{1}-png{2}-c{3}", $definition[0], $samples, $format[0], $format[1])
                $items.Add([pscustomobject][ordered]@{
                    id = $id
                    resolution = [string]$definition[0]
                    width = [int]$definition[1]
                    height = [int]$definition[2]
                    samples = [int]$samples
                    bitDepth = [int]$format[0]
                    compression = [int]$format[1]
                    compositor = "current Blender Fog Glow"
                    shadowStrategy = "original frozen scene; profile-resolved EEVEE shadows"
                    status = "planned"
                })
            }
        }
    }
    $shadowDefinitions = @(
        @("1080p", 1920, 1080, 16, 5, "512"),
        @("1440p", 2560, 1440, 16, 5, "512"),
        @("4k", 3840, 2160, 16, 5, "512"),
        @("1080p", 1920, 1080, 8, 15, "512"),
        @("1080p", 1920, 1080, 16, 15, "512"),
        @("1440p", 2560, 1440, 16, 15, "512"),
        @("4k", 3840, 2160, 16, 15, "512"),
        @("4k", 3840, 2160, 16, 15, "1024")
    )
    foreach ($definition in $shadowDefinitions) {
        $items.Add([pscustomobject][ordered]@{
            id = [string]::Format("cal-{0}-s{1}-png8-c{2}-sp{3}", $definition[0], $definition[3], $definition[4], $definition[5])
            resolution = [string]$definition[0]
            width = [int]$definition[1]
            height = [int]$definition[2]
            samples = [int]$definition[3]
            bitDepth = 8
            compression = [int]$definition[4]
            compositor = "current Blender Fog Glow"
            shadowStrategy = [string]::Format("profile-only EEVEE {0} MiB shadow pool; approved scene unchanged", $definition[5])
            shadowPoolSize = [string]$definition[5]
            status = "planned"
        })
    }
    return $items.ToArray()
}

$scene = [IO.Path]::GetFullPath($ApprovedScenePath)
$baseProfileFile = [IO.Path]::GetFullPath($BaseProfilePath)
if (-not (Test-Path -LiteralPath $scene -PathType Leaf)) { throw "Approved scene does not exist." }
if (-not (Test-Path -LiteralPath $baseProfileFile -PathType Leaf)) { throw "Base profile does not exist." }
$sceneHash = Get-FileSha256 -Path $scene
$baseFileHash = Get-FileSha256 -Path $baseProfileFile
$baseProfile = Import-WzhkRenderProfile -Path $baseProfileFile -VerifyFiles

if ($Mode -eq "Plan") {
    $safety = Get-WzhkRenderSafetyAudit -RepositoryRoot $repositoryRoot
    $machine = Get-WzhkMachineFingerprint -BlenderExecutable $BlenderExecutable
    $calibrationId = "cal-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" + [Guid]::NewGuid().ToString("N").Substring(0, 8)
    if ([string]::IsNullOrWhiteSpace($CalibrationDirectory)) {
        $CalibrationDirectory = Get-WzhkCalibrationEvidencePath -RepositoryRoot $repositoryRoot -MachineId $machine.MachineId -SceneSha256 $sceneHash -CalibrationId $calibrationId
    }
    $directory = [IO.Path]::GetFullPath($CalibrationDirectory)
    $candidateDirectory = Join-Path $directory "candidates"
    $null = New-Item -ItemType Directory -Path $candidateDirectory -Force
    $matrix = @(Get-CandidateMatrix)
    foreach ($candidate in $matrix) {
        $candidatePath = Join-Path $candidateDirectory ($candidate.id + ".json")
        $saved = New-CandidateProfile -Base $baseProfile -Candidate $candidate -Path $candidatePath
        $candidate | Add-Member -NotePropertyName profilePath -NotePropertyValue $saved.Path
        $candidate | Add-Member -NotePropertyName profileSha256 -NotePropertyValue $saved.FileSha256
    }
    $plan = [pscustomobject][ordered]@{
        schemaVersion = "1.0.0"
        kind = "trackprompt-render-calibration"
        calibrationId = $calibrationId
        createdAt = (Get-Date).ToUniversalTime().ToString("o")
        status = $(if ($safety.SafeForGpuCalibration) { "planned" } else { "blocked-active-render" })
        goal = $Goal
        machine = $machine
        machineFingerprint = $machine.MachineFingerprint
        gpuModel = $machine.GpuModel
        vramMiB = $machine.VramMiB
        gpuDriver = $machine.GpuDriver
        cpuModel = $machine.CpuModel
        physicalCores = $machine.PhysicalCores
        logicalProcessors = $machine.LogicalProcessors
        ramBytes = $machine.RamBytes
        blenderVersion = $machine.BlenderVersion
        scenePath = $scene
        sceneSha256 = $sceneHash
        sceneManifestSha256 = [string]$baseProfile.approvedScene.manifestSha256
        sourceProfilePath = $baseProfileFile
        sourceProfileHash = $baseFileHash
        outputDrive = [IO.Path]::GetPathRoot($directory)
        filesystem = (New-Object IO.DriveInfo([IO.Path]::GetPathRoot($directory))).DriveFormat
        requiredStillFrames = @(1, 2085, 3432, 6972, 8106, 13029)
        requiredConsecutiveRanges = @(
            [pscustomobject]@{ startFrame = 7065; endFrame = 7094 },
            [pscustomobject]@{ startFrame = 8091; endFrame = 8120 }
        )
        searchStrategy = "staged elimination; dominated candidates stop before finalist consecutive ranges"
        candidates = $matrix
        safetyAudit = $safety
        recommendation = $null
    }
    $planPath = Get-CalibrationPlanPath -Directory $directory
    $null = Save-WzhkCalibrationJson -Calibration $plan -Path $planPath
    Write-CalibrationResult ([pscustomobject]@{ Ok = $true; Status = $plan.status; CalibrationDirectory = $directory; CalibrationPlan = $planPath; SafetyAudit = $safety; CandidateCount = $matrix.Count })
    exit 0
}

if ([string]::IsNullOrWhiteSpace($CalibrationDirectory)) { throw "CalibrationDirectory is required for $Mode." }
$calibrationPlanPath = Get-CalibrationPlanPath -Directory $CalibrationDirectory
if (-not (Test-Path -LiteralPath $calibrationPlanPath -PathType Leaf)) { throw "Calibration plan does not exist." }
$calibration = Get-Content -LiteralPath $calibrationPlanPath -Raw | ConvertFrom-Json
if ([string]$calibration.sceneSha256 -ne $sceneHash -or [string]$calibration.sourceProfileHash -ne $baseFileHash) { throw "Calibration identity no longer matches the exact scene and source profile." }

if ($Mode -eq "AddShadowPoolCandidates") {
    $candidateDirectory = Join-Path ([IO.Path]::GetFullPath($CalibrationDirectory)) "candidates"
    $null = New-Item -ItemType Directory -Path $candidateDirectory -Force
    $added = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in @(Get-CandidateMatrix | Where-Object { $null -ne $_.PSObject.Properties["shadowPoolSize"] })) {
        if (@($calibration.candidates | Where-Object { [string]$_.id -eq [string]$candidate.id }).Count -gt 0) { continue }
        $candidatePath = Join-Path $candidateDirectory ($candidate.id + ".json")
        $savedCandidate = New-CandidateProfile -Base $baseProfile -Candidate $candidate -Path $candidatePath
        $candidate | Add-Member -NotePropertyName profilePath -NotePropertyValue $savedCandidate.Path
        $candidate | Add-Member -NotePropertyName profileSha256 -NotePropertyValue $savedCandidate.FileSha256
        $calibration.candidates += $candidate
        $added.Add([string]$candidate.id)
    }
    $calibration | Add-Member -NotePropertyName updatedAt -NotePropertyValue ((Get-Date).ToUniversalTime().ToString("o")) -Force
    $null = Save-WzhkCalibrationJson -Calibration $calibration -Path $calibrationPlanPath
    Write-CalibrationResult ([pscustomobject]@{ Ok = $true; AddedCandidateIds = $added.ToArray(); CandidateCount = @($calibration.candidates).Count })
    exit 0
}

if ($Mode -eq "RunCandidate") {
    if ([string]::IsNullOrWhiteSpace($CandidateId)) { throw "CandidateId is required." }
    $candidate = @($calibration.candidates | Where-Object { [string]$_.id -eq $CandidateId } | Select-Object -First 1)
    if ($candidate.Count -ne 1) { throw "CandidateId was not found in the calibration plan." }
    $safety = Get-WzhkRenderSafetyAudit -RepositoryRoot $repositoryRoot
    if (-not $safety.SafeForGpuCalibration) { Write-CalibrationResult ([pscustomobject]@{ Ok = $false; Status = "blocked-active-render"; SafetyAudit = $safety }); exit 9 }
    if (-not (Test-Path -LiteralPath $BlenderExecutable -PathType Leaf)) { throw "Blender executable does not exist." }
    $helper = Join-Path $repositoryRoot "blender\render_calibration_chunk.py"
    $candidateRoot = Join-Path ([IO.Path]::GetFullPath($CalibrationDirectory)) ("runs\" + $CandidateId)
    $ranges = New-Object System.Collections.Generic.List[object]
    foreach ($frame in @(1, 2085, 3432, 6972, 8106, 13029)) { $ranges.Add([pscustomobject]@{ startFrame = $frame; endFrame = $frame; role = "representative-still" }) }
    $ranges.Add([pscustomobject]@{ startFrame = 7065; endFrame = 7094; role = "consecutive-production-range" })
    $ranges.Add([pscustomobject]@{ startFrame = 8091; endFrame = 8120; role = "consecutive-production-range" })
    $reports = New-Object System.Collections.Generic.List[string]
    $index = 0
    foreach ($range in @($ranges | Select-Object -First $MaximumRanges)) {
        $index += 1
        $rangeName = [string]::Format("range-{0:D2}-{1:D6}-{2:D6}", $index, $range.startFrame, $range.endFrame)
        $output = Join-Path $candidateRoot ($rangeName + "\frames")
        $report = Join-Path $candidateRoot ($rangeName + "\benchmark-range.json")
        if (Test-Path -LiteralPath $report -PathType Leaf) {
            $existingReport = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
            if (
                [string]$existingReport.scene.sha256 -ne $sceneHash -or
                [string]$existingReport.profile.sha256 -ne [string]$candidate[0].profileSha256 -or
                [int]$existingReport.frameRange.start -ne [int]$range.startFrame -or
                [int]$existingReport.frameRange.end -ne [int]$range.endFrame
            ) { throw "Existing calibration report identity does not match $rangeName." }
            if ($null -eq $existingReport.PSObject.Properties["role"]) {
                $existingReport | Add-Member -NotePropertyName role -NotePropertyValue ([string]$range.role)
                $null = Save-WzhkCalibrationJson -Calibration $existingReport -Path $report
            }
            $reports.Add($report)
            continue
        }
        $null = New-Item -ItemType Directory -Path $output -Force
        $arguments = @(
            "--background", $scene,
            "--python-exit-code", "1",
            "--python", $helper,
            "--",
            "--profile", [string]$candidate[0].profilePath,
            "--output", $output,
            "--report", $report,
            "--start", [string]$range.startFrame,
            "--end", [string]$range.endFrame
        )
        $started = Get-Date
        & $BlenderExecutable @arguments
        if ($LASTEXITCODE -ne 0) { throw "Calibration range $rangeName failed with exit code $LASTEXITCODE." }
        $rangeReport = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
        $rangeReport | Add-Member -NotePropertyName processWallSeconds -NotePropertyValue (((Get-Date) - $started).TotalSeconds)
        $rangeReport | Add-Member -NotePropertyName role -NotePropertyValue ([string]$range.role)
        $null = Save-WzhkCalibrationJson -Calibration $rangeReport -Path $report
        $reports.Add($report)
    }
    $candidate[0].status = $(if ($reports.Count -ge 8) { "benchmark-complete-pending-visual-review" } else { "screening-stills-complete" })
    $candidate[0] | Add-Member -NotePropertyName reports -NotePropertyValue $reports.ToArray() -Force
    $calibration.status = "benchmarking"
    $calibration | Add-Member -NotePropertyName updatedAt -NotePropertyValue ((Get-Date).ToUniversalTime().ToString("o")) -Force
    $null = Save-WzhkCalibrationJson -Calibration $calibration -Path $calibrationPlanPath
    Write-CalibrationResult ([pscustomobject]@{ Ok = $true; Status = $candidate[0].status; CandidateId = $CandidateId; Reports = $reports.ToArray(); VisualReviewRequired = $true })
    exit 0
}

if ($Mode -eq "ReviewCandidate") {
    if ([string]::IsNullOrWhiteSpace($CandidateId)) { throw "CandidateId is required." }
    if ($QualityResult -eq "PENDING HUMAN REVIEW") { throw "ReviewCandidate requires PASS, PASS WITH DOCUMENTED CAVEAT, or FAIL." }
    if ($QualityResult -ne "PASS" -and [string]::IsNullOrWhiteSpace($QualityNotes)) { throw "PASS WITH DOCUMENTED CAVEAT and FAIL require quality notes." }
    $candidate = @($calibration.candidates | Where-Object { [string]$_.id -eq $CandidateId } | Select-Object -First 1)
    if ($candidate.Count -ne 1 -or $null -eq $candidate[0].PSObject.Properties["reports"] -or @($candidate[0].reports).Count -lt 8) { throw "Candidate has not completed all required bounded benchmark ranges." }
    $candidate[0].status = $(if ($QualityResult -eq "FAIL") { "quality-failed" } else { "passing" })
    $candidate[0] | Add-Member -NotePropertyName qualityResult -NotePropertyValue $QualityResult -Force
    $candidate[0] | Add-Member -NotePropertyName qualityNotes -NotePropertyValue $QualityNotes -Force
    $candidate[0] | Add-Member -NotePropertyName reviewedAt -NotePropertyValue ((Get-Date).ToUniversalTime().ToString("o")) -Force
    $calibration | Add-Member -NotePropertyName updatedAt -NotePropertyValue ((Get-Date).ToUniversalTime().ToString("o")) -Force
    $null = Save-WzhkCalibrationJson -Calibration $calibration -Path $calibrationPlanPath
    Write-CalibrationResult ([pscustomobject]@{ Ok = $true; CandidateId = $CandidateId; QualityResult = $QualityResult })
    exit 0
}

if ($Mode -eq "Finalize") {
    $passing = New-Object System.Collections.Generic.List[object]
    foreach ($candidate in @($calibration.candidates | Where-Object { [string]$_.status -eq "passing" })) {
        $reports = @($candidate.reports | ForEach-Object { Get-Content -LiteralPath $_ -Raw | ConvertFrom-Json })
        $consecutive = @($reports | Where-Object { [string]$_.role -eq "consecutive-production-range" })
        if ($consecutive.Count -lt 2) { continue }
        $timings = @($consecutive | ForEach-Object { @($_.timings | Select-Object -Skip 1 | ForEach-Object { [double]$_.totalSeconds }) })
        $sizes = @($reports | ForEach-Object { @($_.timings | ForEach-Object { [double]$_.sizeBytes }) })
        $stats = Get-WzhkCalibrationStatistics -Values $timings -ColdValueCount 0
        $sizeP90 = Get-WzhkCalibrationPercentile -Values $sizes -Percentile 90
        $projected = [int64][Math]::Ceiling($sizeP90 * [int]$baseProfile.timeline.frameCount)
        $passing.Add([pscustomobject][ordered]@{
            Id = [string]$candidate.id
            Resolution = [string]$candidate.resolution
            Width = [int]$candidate.width
            Height = [int]$candidate.height
            Samples = [int]$candidate.samples
            BitDepth = [int]$candidate.bitDepth
            Compression = [int]$candidate.compression
            ShadowPoolSize = $(if ($null -ne $candidate.PSObject.Properties["shadowPoolSize"]) { [string]$candidate.shadowPoolSize } else { [string]$baseProfile.render.shadowPoolSize })
            ShadowStrategy = [string]$candidate.shadowStrategy
            WarmMedianSeconds = $stats.WarmMedianSeconds
            P90Seconds = $stats.P90Seconds
            FramesPerHour = $stats.FramesPerHour
            ProjectedStorageBytes = $projected
            QualityResult = [string]$candidate.qualityResult
            QualityNotes = [string]$candidate.qualityNotes
            ProfilePath = [string]$candidate.profilePath
            Confidence = $stats.Confidence
        })
    }
    if ($passing.Count -eq 0) { throw "No fully benchmarked candidate has passed human visual review." }
    $recommended = Select-WzhkRecommendedCalibrationProfile -Candidates $passing.ToArray()
    if ([string]::IsNullOrWhiteSpace($ProfileOutputDirectory)) { $ProfileOutputDirectory = Join-Path $repositoryRoot "render-profiles\trip-to-andromeda" }
    $profileDirectory = [IO.Path]::GetFullPath($ProfileOutputDirectory)
    $null = New-Item -ItemType Directory -Path $profileDirectory -Force
    $saved = New-Object System.Collections.Generic.List[object]
    $resolutionWinners = @{}
    foreach ($resolution in @("720p", "1080p", "1440p", "4k")) {
        $group = @($passing | Where-Object { $_.Resolution -eq $resolution })
        if ($group.Count -gt 0) { $resolutionWinners[$resolution] = Select-WzhkRecommendedCalibrationProfile -Candidates $group }
    }
    $resolutionVerdicts = [ordered]@{}
    foreach ($resolution in @("720p", "1080p", "1440p", "4k")) {
        if (-not $resolutionWinners.ContainsKey($resolution)) {
            $resolutionVerdicts[$resolution] = [pscustomobject]@{ Status = "no-passing-measured-candidate"; Reason = "No fully measured candidate passed human visual review." }
        }
        elseif ($resolution -eq "4k" -and [double]$resolutionWinners[$resolution].WarmMedianSeconds -ge 60.0) {
            $resolutionVerdicts[$resolution] = [pscustomobject]@{ Status = "impractical"; Reason = "Measured native 4K warm median did not meet the practical below-60-seconds-per-frame target."; WarmMedianSeconds = [double]$resolutionWinners[$resolution].WarmMedianSeconds }
            $resolutionWinners.Remove($resolution)
        }
        else {
            $resolutionVerdicts[$resolution] = [pscustomobject]@{ Status = "passing"; WarmMedianSeconds = [double]$resolutionWinners[$resolution].WarmMedianSeconds }
        }
    }
    $definitions = @(
        @("720p", "Trip to Andromeda - 720p Hyper Optimized", "trip-to-andromeda-720p-hyper-optimized", "trip-to-andromeda-720p-hyper-optimized.json"),
        @("1080p", "Trip to Andromeda - 1080p Recommended Calibrated", "trip-to-andromeda-1080p-recommended-calibrated", "trip-to-andromeda-1080p-recommended-calibrated.json"),
        @("1440p", "Trip to Andromeda - 1440p Balanced Calibrated", "trip-to-andromeda-1440p-balanced-calibrated", "trip-to-andromeda-1440p-balanced-calibrated.json"),
        @("4k", "Trip to Andromeda - 4K Balanced Optimized", "trip-to-andromeda-4k-balanced-optimized", "trip-to-andromeda-4k-balanced-optimized.json")
    )
    foreach ($definition in $definitions) {
        if (-not $resolutionWinners.ContainsKey([string]$definition[0])) { continue }
        $winner = $resolutionWinners[[string]$definition[0]]
        $profile = New-WzhkCalibratedRenderProfile -BaseProfile $baseProfile -DisplayName $definition[1] -ProfileId $definition[2] -Width $winner.Width -Height $winner.Height -Samples $winner.Samples -BitDepth $winner.BitDepth -Compression $winner.Compression -ShadowPoolSize $winner.ShadowPoolSize -ShadowStrategy $winner.ShadowStrategy -WarmMedianSeconds $winner.WarmMedianSeconds -P90Seconds $winner.P90Seconds -ProjectedStorageBytes $winner.ProjectedStorageBytes -CalibrationId ([string]$calibration.calibrationId) -CalibrationEvidencePath $CalibrationDirectory -QualityResult $winner.QualityResult -RecommendationReason $winner.RecommendationReason -Confidence $winner.Confidence
        $saved.Add((Save-WzhkRenderProfile -Profile $profile -Path (Join-Path $profileDirectory $definition[3]) -Force))
    }
    $recommendedProfile = New-WzhkCalibratedRenderProfile -BaseProfile $baseProfile -DisplayName "Trip to Andromeda - Calibrated Recommended" -ProfileId "trip-to-andromeda-calibrated-recommended" -Width $recommended.Width -Height $recommended.Height -Samples $recommended.Samples -BitDepth $recommended.BitDepth -Compression $recommended.Compression -ShadowPoolSize $recommended.ShadowPoolSize -ShadowStrategy $recommended.ShadowStrategy -WarmMedianSeconds $recommended.WarmMedianSeconds -P90Seconds $recommended.P90Seconds -ProjectedStorageBytes $recommended.ProjectedStorageBytes -CalibrationId ([string]$calibration.calibrationId) -CalibrationEvidencePath $CalibrationDirectory -QualityResult $recommended.QualityResult -RecommendationReason $recommended.RecommendationReason -Confidence $recommended.Confidence -RecommendedForMachine
    $recommendedPath = Join-Path $profileDirectory "trip-to-andromeda-calibrated-recommended.json"
    $recommendedSaved = Save-WzhkRenderProfile -Profile $recommendedProfile -Path $recommendedPath -Force
    $saved.Add($recommendedSaved)
    $pointerPath = Join-Path $profileDirectory "recommended-profile.json"
    $null = Save-WzhkRecommendedProfilePointer -ProfilePath $recommendedSaved.Path -SceneSha256 $sceneHash -CalibrationId ([string]$calibration.calibrationId) -RecommendationReason $recommended.RecommendationReason -Path $pointerPath
    $calibration.status = "complete"
    $calibration | Add-Member -NotePropertyName completedAt -NotePropertyValue ((Get-Date).ToUniversalTime().ToString("o")) -Force
    $calibration | Add-Member -NotePropertyName recommendation -NotePropertyValue $recommended -Force
    $calibration | Add-Member -NotePropertyName generatedProfiles -NotePropertyValue @($saved | ForEach-Object { [pscustomobject]@{ Path = $_.Path; FileSha256 = $_.FileSha256; ContentSha256 = $_.ContentSha256 } }) -Force
    $calibration | Add-Member -NotePropertyName resolutionVerdicts -NotePropertyValue ([pscustomobject]$resolutionVerdicts) -Force
    $calibration | Add-Member -NotePropertyName recommendedPointer -NotePropertyValue $pointerPath -Force
    $null = Save-WzhkCalibrationJson -Calibration $calibration -Path $calibrationPlanPath
    Write-CalibrationResult ([pscustomobject]@{ Ok = $true; Status = "complete"; RecommendedProfile = $recommendedSaved; RecommendedPointer = $pointerPath; GeneratedProfiles = $calibration.generatedProfiles })
}
