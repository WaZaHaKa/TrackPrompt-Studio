Set-StrictMode -Version Latest

$script:BuilderStageCount = 13
$script:BuilderModuleRoot = $PSScriptRoot
$script:BuilderRepositoryRoot = [IO.Path]::GetFullPath((Join-Path $script:BuilderModuleRoot "..\.."))

if ($null -eq (Get-Command -Name New-WzhkRenderProfile -CommandType Function -ErrorAction SilentlyContinue)) {
    Import-Module (Join-Path $script:BuilderModuleRoot "WZHK.Profiles.psm1") -ErrorAction Stop
}
if ($null -eq (Get-Command -Name Show-WzhkBuilderStage -CommandType Function -ErrorAction SilentlyContinue)) {
    Import-Module (Join-Path $script:BuilderModuleRoot "WZHK.UI.psm1") -ErrorAction Stop
}

function Get-WzhkBuilderPropertyValue {
    param(
        [AllowNull()][object]$InputObject,
        [Parameter(Mandatory = $true)][string]$PropertyPath,
        [AllowNull()][object]$Default = $null
    )

    $current = $InputObject
    foreach ($segment in @($PropertyPath.Split('.'))) {
        if ($null -eq $current) { return $Default }
        if ($current -is [Collections.IDictionary]) {
            if (-not $current.Contains($segment)) { return $Default }
            $current = $current[$segment]
            continue
        }
        $property = $current.PSObject.Properties[$segment]
        if ($null -eq $property) { return $Default }
        $current = $property.Value
    }
    if ($null -eq $current) { return $Default }
    return $current
}

function Get-WzhkBuilderFirstValue {
    param(
        [AllowNull()][object]$InputObject,
        [Parameter(Mandatory = $true)][string[]]$PropertyPaths,
        [AllowNull()][object]$Default = $null
    )

    $missing = New-Object object
    foreach ($propertyPath in $PropertyPaths) {
        $value = Get-WzhkBuilderPropertyValue -InputObject $InputObject -PropertyPath $propertyPath -Default $missing
        if (-not [object]::ReferenceEquals($value, $missing)) { return $value }
    }
    return $Default
}

function Copy-WzhkBuilderObject {
    param([Parameter(Mandatory = $true)][object]$InputObject)
    return ($InputObject | ConvertTo-Json -Depth 100 | ConvertFrom-Json)
}

function Set-WzhkBuilderValues {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][Collections.IDictionary]$Values
    )

    $updated = $Profile
    foreach ($propertyPath in $Values.Keys) {
        $updated = Set-WzhkProfileValue -Profile $updated -PropertyPath ([string]$propertyPath) -Value $Values[$propertyPath]
    }
    return $updated
}

function New-WzhkBuilderChoiceItem {
    param(
        [string]$Label,
        [string]$Description,
        [AllowNull()][object]$Value,
        [bool]$Enabled = $true
    )
    return [pscustomobject]@{ Label = $Label; Description = $Description; Enabled = $Enabled; Value = $Value }
}

function Get-WzhkBuilderBooleanText {
    param([bool]$Value)
    if ($Value) { return "ENABLED" }
    return "DISABLED"
}

function Get-WzhkBuilderStorageEstimate {
    param(
        [int]$Width,
        [int]$Height,
        [int]$FrameCount,
        [double]$Fps,
        [int]$BitDepth,
        [string]$Format
    )

    $channelBytes = if ($BitDepth -gt 8) { 2.0 } else { 1.0 }
    $compressionFactor = if ($Format -eq "OPEN_EXR") { 0.68 } elseif ($BitDepth -gt 8) { 0.42 } else { 0.32 }
    $sequence = [Math]::Ceiling(($Width * $Height * 3.0 * $channelBytes * $compressionFactor * $FrameCount / 1GB) * 1000.0) / 1000.0
    $duration = $FrameCount / $Fps
    $scale = ($Width * $Height) / (1920.0 * 1080.0)
    $master = [Math]::Ceiling(([Math]::Max(0.25, $duration * 0.018 * $scale)) * 1000.0) / 1000.0
    $delivery = [Math]::Ceiling(([Math]::Max(0.05, $duration * 0.0015 * $scale)) * 1000.0) / 1000.0
    $reserve = 2.0
    $contingency = 1.5
    return [pscustomobject]@{
        PlannedFrameSequenceGiB = $sequence
        ProjectedMasterGiB = $master
        ProjectedDeliveryGiB = $delivery
        SupportReserveGiB = $reserve
        ContingencyMultiplier = $contingency
        MinimumLaunchFreeGiB = [double][Math]::Ceiling(($sequence + $master + $delivery + $reserve) * $contingency)
        EstimatedDurationSeconds = $duration
    }
}

function Get-WzhkBuilderDisplayAspect {
    param(
        [int]$Width,
        [int]$Height,
        [double]$PixelAspectX = 1.0,
        [double]$PixelAspectY = 1.0
    )

    if ($Width -le 0 -or $Height -le 0 -or $PixelAspectX -le 0.0 -or $PixelAspectY -le 0.0) { return "unknown" }
    if ([Math]::Abs($PixelAspectX - $PixelAspectY) -lt 0.0000001) {
        return Get-WzhkAspectRatioLabel -Width $Width -Height $Height
    }

    $ratio = ($Width * $PixelAspectX) / ($Height * $PixelAspectY)
    foreach ($known in @(
        @((1.0), "1:1"),
        @((4.0 / 3.0), "4:3"),
        @((3.0 / 2.0), "3:2"),
        @((16.0 / 10.0), "16:10"),
        @((16.0 / 9.0), "16:9"),
        @((17.0 / 9.0), "17:9"),
        @((2.0), "2:1"),
        @((21.0 / 9.0), "21:9"),
        @((2.39), "2.39:1")
    )) {
        if ([Math]::Abs($ratio - [double]$known[0]) -lt 0.0005) { return [string]$known[1] }
    }
    return ($ratio.ToString("0.####", [Globalization.CultureInfo]::InvariantCulture) + ":1")
}

function Get-WzhkProfileBuilderStages {
    return @(
        [pscustomobject]@{ Number = 1; Id = "Identity"; Title = "PROFILE IDENTITY" },
        [pscustomobject]@{ Number = 2; Id = "Scene"; Title = "APPROVED SCENE" },
        [pscustomobject]@{ Number = 3; Id = "Resolution"; Title = "RESOLUTION" },
        [pscustomobject]@{ Number = 4; Id = "Timeline"; Title = "FRAME RATE / RANGE" },
        [pscustomobject]@{ Number = 5; Id = "Quality"; Title = "QUALITY" },
        [pscustomobject]@{ Number = 6; Id = "Render"; Title = "RENDER SETTINGS" },
        [pscustomobject]@{ Number = 7; Id = "Sequence"; Title = "IMAGE SEQUENCE" },
        [pscustomobject]@{ Number = 8; Id = "Color"; Title = "COLOR MANAGEMENT" },
        [pscustomobject]@{ Number = 9; Id = "Production"; Title = "CHUNK / RESUME" },
        [pscustomobject]@{ Number = 10; Id = "Output"; Title = "OUTPUT" },
        [pscustomobject]@{ Number = 11; Id = "Encoding"; Title = "ENCODING" },
        [pscustomobject]@{ Number = 12; Id = "Dashboard"; Title = "PROGRESS DASHBOARD" },
        [pscustomobject]@{ Number = 13; Id = "Review"; Title = "REVIEW" }
    )
}

function Resolve-WzhkQualitySettings {
    param([ValidateSet("DRAFT", "PREVIEW", "BALANCED", "HIGH", "ULTRA", "CUSTOM")][string]$Mode)

    $settings = switch ($Mode) {
        "DRAFT" { @(16, "128", 1, 0.5, $false, "8", 16, 4, 4, $false, $false, 0.5) }
        "PREVIEW" { @(32, "256", 1, 0.75, $false, "8", 24, 8, 6, $false, $false, 0.75) }
        "BALANCED" { @(64, "512", 1, 1.0, $false, "8", 32, 8, 8, $false, $false, 1.0) }
        "HIGH" { @(128, "1024", 2, 1.0, $true, "4", 64, 16, 16, $true, $false, 1.0) }
        "ULTRA" { @(256, "2048", 4, 1.0, $true, "2", 128, 32, 16, $true, $true, 1.0) }
        default { return [pscustomobject][ordered]@{ Mode = "CUSTOM" } }
    }

    return [pscustomobject][ordered]@{
        Mode = $Mode
        Samples = [int]$settings[0]
        ShadowPoolSize = [string]$settings[1]
        ShadowRayCount = [int]$settings[2]
        ShadowResolutionScale = [double]$settings[3]
        RayTracing = [bool]$settings[4]
        VolumetricTileSize = [string]$settings[5]
        VolumetricSamples = [int]$settings[6]
        VolumetricShadowSamples = [int]$settings[7]
        VolumetricRayDepth = [int]$settings[8]
        VolumetricShadows = [bool]$settings[9]
        MotionBlur = [bool]$settings[10]
        DitherIntensity = [double]$settings[11]
        HighQualityNormals = $false
    }
}

function Set-WzhkBuilderQualityMode {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [ValidateSet("DRAFT", "PREVIEW", "BALANCED", "HIGH", "ULTRA", "CUSTOM")][string]$Mode
    )

    $values = [ordered]@{ "render.qualityMode" = $Mode; "quality.mode" = $Mode; "render.highQualityNormals" = $false }
    $resolved = Resolve-WzhkQualitySettings -Mode $Mode
    if ($Mode -ne "CUSTOM") {
        $values["render.samples"] = $resolved.Samples
        $values["render.shadowPoolSize"] = $resolved.ShadowPoolSize
        $values["render.shadowRayCount"] = $resolved.ShadowRayCount
        $values["render.shadowResolutionScale"] = $resolved.ShadowResolutionScale
        $values["render.rayTracing"] = $resolved.RayTracing
        $values["render.rayTracingMethod"] = "PROBE"
        $values["render.volumetricTileSize"] = $resolved.VolumetricTileSize
        $values["render.volumetricSamples"] = $resolved.VolumetricSamples
        $values["render.volumetricShadowSamples"] = $resolved.VolumetricShadowSamples
        $values["render.volumetricRayDepth"] = $resolved.VolumetricRayDepth
        $values["render.volumetricShadows"] = $resolved.VolumetricShadows
        $values["render.motionBlur"] = $resolved.MotionBlur
        $values["render.ditherIntensity"] = $resolved.DitherIntensity
    }
    return Set-WzhkBuilderValues -Profile $Profile -Values $values
}

function Get-WzhkBuilderStagePaths {
    param([Parameter(Mandatory = $true)][string]$StageId)

    switch ($StageId) {
        "Identity" { return @("displayName", "profileId", "description", "project", "preset") }
        "Scene" { return @("approvedScene", "approvedScenePath", "approvedSceneSha256", "sceneManifestPath", "sourceIdentities.sceneManifestSha256") }
        "Resolution" { return @("resolution", "aspect") }
        "Timeline" { return @("timeline", "frameStart", "frameEnd", "fps", "durationSeconds") }
        "Quality" { return @("quality", "render.qualityMode", "render.samples", "render.shadowPoolSize", "render.shadowRayCount", "render.shadowResolutionScale", "render.rayTracing", "render.rayTracingMethod", "render.volumetricTileSize", "render.volumetricSamples", "render.volumetricShadowSamples", "render.volumetricRayDepth", "render.volumetricShadows", "render.motionBlur", "render.ditherIntensity", "render.highQualityNormals") }
        "Render" { return @("render", "compositor") }
        "Sequence" { return @("imageSequence", "output.framesSubdirectory") }
        "Color" { return @("colorManagement") }
        "Production" { return @("production", "chunking") }
        "Output" { return @("output") }
        "Encoding" { return @("encoding") }
        "Dashboard" { return @("dashboard") }
        default { return @() }
    }
}

function Copy-WzhkBuilderStageDefaults {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][object]$RecommendedProfile,
        [Parameter(Mandatory = $true)][string]$StageId
    )

    $updated = $Profile
    $missing = New-Object object
    foreach ($propertyPath in @(Get-WzhkBuilderStagePaths -StageId $StageId)) {
        $value = Get-WzhkBuilderPropertyValue -InputObject $RecommendedProfile -PropertyPath $propertyPath -Default $missing
        if (-not [object]::ReferenceEquals($value, $missing)) {
            $updated = Set-WzhkProfileValue -Profile $updated -PropertyPath $propertyPath -Value (Copy-WzhkBuilderObject -InputObject $value)
        }
    }
    $updated = Set-WzhkProfileValue -Profile $updated -PropertyPath "render.highQualityNormals" -Value $false
    return $updated
}

function Get-WzhkNormalizedSceneCandidate {
    param([Parameter(Mandatory = $true)][object]$Candidate)

    $path = if ($Candidate -is [string]) { [string]$Candidate } else { [string](Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("Path", "FullName", "approvedScenePath") -Default "") }
    if ([string]::IsNullOrWhiteSpace($path)) { return $null }
    try { $path = [IO.Path]::GetFullPath($path) } catch { return $null }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or [IO.Path]::GetExtension($path).ToLowerInvariant() -ne ".blend") { return $null }

    $sceneHash = Get-WzhkFileSha256 -Path $path
    $manifestPath = [string](Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("ManifestPath", "manifestPath", "approvedScene.manifestPath") -Default "")
    $manifestHash = ""
    if (-not [string]::IsNullOrWhiteSpace($manifestPath)) {
        try { $manifestPath = [IO.Path]::GetFullPath($manifestPath) } catch { $manifestPath = "" }
        if (-not [string]::IsNullOrWhiteSpace($manifestPath) -and (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            $manifestHash = Get-WzhkFileSha256 -Path $manifestPath
        }
        else { $manifestPath = "" }
    }

    return [pscustomobject][ordered]@{
        Path = $path
        Name = [IO.Path]::GetFileName($path)
        Sha256 = $sceneHash
        ManifestPath = $manifestPath
        ManifestSha256 = $manifestHash
        BlenderVersion = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("BlenderVersion", "blenderVersion") -Default "5.2.0 LTS"
        ObjectCount = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("ObjectCount", "objectCount") -Default "unknown"
        MaterialCount = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("MaterialCount", "materialCount") -Default "unknown"
        CollectionCount = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("CollectionCount", "collectionCount") -Default "unknown"
        FCurveCount = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("FCurveCount", "fCurveCount") -Default "unknown"
        MacroStateCount = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("MacroStateCount", "macroStateCount") -Default "unknown"
        AudioBusCurveCount = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("AudioBusCurveCount", "audioBusCurveCount") -Default "unknown"
        ActiveCamera = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("ActiveCamera", "activeCamera") -Default "unknown"
        Preset = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("Preset", "preset") -Default "space-journey"
        FrameStart = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("FrameStart", "frameStart") -Default 1
        FrameEnd = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("FrameEnd", "frameEnd") -Default 13029
        Fps = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("Fps", "fps") -Default 30.0
        Width = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("Width", "width") -Default $null
        Height = Get-WzhkBuilderFirstValue -InputObject $Candidate -PropertyPaths @("Height", "height") -Default $null
    }
}

function Set-WzhkBuilderScene {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][object]$Scene,
        [switch]$ResetSceneDefaults
    )

    $values = [ordered]@{
        "approvedScene.path" = $Scene.Path
        "approvedScene.sha256" = $Scene.Sha256
        "approvedScene.manifestPath" = $Scene.ManifestPath
        "approvedScene.manifestSha256" = $Scene.ManifestSha256
        "approvedScene.blenderVersion" = $Scene.BlenderVersion
        "approvedScene.objectCount" = $Scene.ObjectCount
        "approvedScene.materialCount" = $Scene.MaterialCount
        "approvedScene.collectionCount" = $Scene.CollectionCount
        "approvedScene.fCurveCount" = $Scene.FCurveCount
        "approvedScene.macroStateCount" = $Scene.MacroStateCount
        "approvedScene.audioBusCurveCount" = $Scene.AudioBusCurveCount
        "approvedScene.activeCamera" = $Scene.ActiveCamera
        "approvedScene.preset" = $Scene.Preset
        "approvedScenePath" = $Scene.Path
        "approvedSceneSha256" = $Scene.Sha256
        "sceneManifestPath" = $Scene.ManifestPath
        "sourceIdentities.sceneManifestSha256" = $Scene.ManifestSha256
        "blenderVersion" = $Scene.BlenderVersion
        "sourceTimeline.frameStart" = [int]$Scene.FrameStart
        "sourceTimeline.frameEnd" = [int]$Scene.FrameEnd
        "sourceTimeline.fps" = [double]$Scene.Fps
    }
    if ($ResetSceneDefaults) {
        $frameStart = [int]$Scene.FrameStart
        $frameEnd = [int]$Scene.FrameEnd
        $fps = [double]$Scene.Fps
        $frameCount = $frameEnd - $frameStart + 1
        $duration = $frameCount / $fps
        $values["preset"] = [string]$Scene.Preset
        $values["timeline.frameStart"] = $frameStart
        $values["timeline.frameEnd"] = $frameEnd
        $values["timeline.frameCount"] = $frameCount
        $values["timeline.fps"] = $fps
        $values["timeline.durationSeconds"] = $duration
        $values["frameStart"] = $frameStart
        $values["frameEnd"] = $frameEnd
        $values["fps"] = $fps
        $values["durationSeconds"] = $duration
        if ($null -ne $Scene.Width -and $null -ne $Scene.Height) {
            $width = [int]$Scene.Width
            $height = [int]$Scene.Height
            $values["resolution.width"] = $width
            $values["resolution.height"] = $height
            $values["resolution.percentage"] = 100
            $values["resolution.displayAspect"] = Get-WzhkAspectRatioLabel -Width $width -Height $height
            $values["aspect.display"] = Get-WzhkAspectRatioLabel -Width $width -Height $height
        }
    }
    return Set-WzhkBuilderValues -Profile $Profile -Values $values
}

function Update-WzhkBuilderDerivedValues {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $originalUpdatedAt = Get-WzhkBuilderPropertyValue -InputObject $Profile -PropertyPath "updatedAt" -Default $null
    $originalTimestampUpdatedAt = Get-WzhkBuilderPropertyValue -InputObject $Profile -PropertyPath "timestamps.updatedAt" -Default $null
    $originalAuthorizationValue = Get-WzhkBuilderPropertyValue -InputObject $Profile -PropertyPath "authorization" -Default $null
    $originalAuthorization = if ($null -eq $originalAuthorizationValue) { $null } else { Copy-WzhkBuilderObject -InputObject $originalAuthorizationValue }
    $width = [int](Get-WzhkBuilderPropertyValue -InputObject $Profile -PropertyPath "resolution.width" -Default 1920)
    $height = [int](Get-WzhkBuilderPropertyValue -InputObject $Profile -PropertyPath "resolution.height" -Default 1080)
    $pixelAspectX = [double](Get-WzhkBuilderPropertyValue -InputObject $Profile -PropertyPath "resolution.pixelAspectX" -Default 1.0)
    $pixelAspectY = [double](Get-WzhkBuilderPropertyValue -InputObject $Profile -PropertyPath "resolution.pixelAspectY" -Default 1.0)
    $frameStart = [int](Get-WzhkBuilderFirstValue -InputObject $Profile -PropertyPaths @("timeline.frameStart", "frameStart") -Default 1)
    $frameEnd = [int](Get-WzhkBuilderFirstValue -InputObject $Profile -PropertyPaths @("timeline.frameEnd", "frameEnd") -Default $frameStart)
    $fps = [double](Get-WzhkBuilderFirstValue -InputObject $Profile -PropertyPaths @("timeline.fps", "fps") -Default 30.0)
    $format = ([string](Get-WzhkBuilderPropertyValue -InputObject $Profile -PropertyPath "imageSequence.format" -Default "PNG")).ToUpperInvariant().Replace("OPENEXR", "OPEN_EXR")
    $bitDepth = [int](Get-WzhkBuilderPropertyValue -InputObject $Profile -PropertyPath "imageSequence.bitDepth" -Default 16)
    $frameCount = [Math]::Max(1, $frameEnd - $frameStart + 1)
    $duration = $frameCount / $fps
    $aspect = Get-WzhkBuilderDisplayAspect -Width $width -Height $height -PixelAspectX $pixelAspectX -PixelAspectY $pixelAspectY
    $resolutionLabel = Get-WzhkResolutionLabel -Width $width -Height $height
    $chunkSize = [int](Get-WzhkBuilderFirstValue -InputObject $Profile -PropertyPaths @("production.framesPerChunk", "chunking.framesPerChunk") -Default 150)
    $chunkSize = [Math]::Min(1200, [Math]::Min($frameCount, [Math]::Max(1, $chunkSize)))
    $estimate = Get-WzhkBuilderStorageEstimate -Width $width -Height $height -FrameCount $frameCount -Fps $fps -BitDepth $bitDepth -Format $format
    $displayBaked = ($format -eq "PNG")
    $templateId = [string](Get-WzhkBuilderPropertyValue $Profile "templateId" "CUSTOM")
    $defaultQuality = switch ($templateId) {
        "FULL-HD-FAST" { "PREVIEW" }
        "1440P-BALANCED" { "BALANCED" }
        "4K-BALANCED" { "BALANCED" }
        "4K-HIGH" { "HIGH" }
        "4K-ULTRA" { "ULTRA" }
        default { "CUSTOM" }
    }
    $outputRoot = [string](Get-WzhkBuilderPropertyValue $Profile "output.rootDirectory" (Join-Path $script:BuilderRepositoryRoot "final-output"))
    $currentFreeGiB = Get-WzhkBuilderFreeDiskGiB -Path $outputRoot

    $values = [ordered]@{
        "timeline.frameStart" = $frameStart
        "timeline.frameEnd" = $frameEnd
        "timeline.frameCount" = $frameCount
        "timeline.fps" = $fps
        "timeline.durationSeconds" = $duration
        "frameStart" = $frameStart
        "frameEnd" = $frameEnd
        "fps" = $fps
        "durationSeconds" = $duration
        "resolution.label" = $resolutionLabel
        "resolution.displayAspect" = $aspect
        "aspect.display" = $aspect
        "render.highQualityNormals" = $false
        "production.framesPerChunk" = $chunkSize
        "production.chunkSize" = $chunkSize
        "chunking.framesPerChunk" = $chunkSize
        "imageSequence.colorManagement.displayTransformBaked" = $displayBaked
        "imageSequence.colorManagement.encodedColorSpace" = $(if ($displayBaked) { "sRGB" } else { "scene-linear" })
        "imageSequence.colorManagement.note" = $(if ($displayBaked) { "The reviewed AgX display transform and compositor result are baked into each opaque PNG." } else { "OpenEXR half-float frames remain scene-linear; profile encoding stays disabled until approved linear-to-delivery filters are supplied." })
        "storage.plannedFrameSequenceGiB" = $estimate.PlannedFrameSequenceGiB
        "storage.projectedMasterGiB" = $estimate.ProjectedMasterGiB
        "storage.projectedDeliveryGiB" = $estimate.ProjectedDeliveryGiB
        "storage.supportReserveGiB" = $estimate.SupportReserveGiB
        "storage.contingencyMultiplier" = $estimate.ContingencyMultiplier
        "storage.minimumLaunchFreeGiB" = $estimate.MinimumLaunchFreeGiB
        "estimates.durationSeconds" = $estimate.EstimatedDurationSeconds
        "estimates.duration" = [TimeSpan]::FromSeconds($estimate.EstimatedDurationSeconds).ToString()
        "estimates.frameCount" = $frameCount
        "estimates.plannedFrameSequenceGiB" = $estimate.PlannedFrameSequenceGiB
        "estimates.frameSequenceSize" = ([string]$estimate.PlannedFrameSequenceGiB + " GiB")
        "estimates.totalStorage" = ([string]$estimate.MinimumLaunchFreeGiB + " GiB minimum free")
        "estimates.minimumLaunchFreeGiB" = $estimate.MinimumLaunchFreeGiB
        "dashboard.resolutionLabel" = $resolutionLabel
        "description" = [string](Get-WzhkBuilderPropertyValue $Profile "description" "Explicit resumable TrackPrompt production render profile.")
        "render.qualityMode" = [string](Get-WzhkBuilderPropertyValue $Profile "render.qualityMode" $defaultQuality)
        "quality.mode" = [string](Get-WzhkBuilderFirstValue $Profile @("quality.mode", "render.qualityMode") $defaultQuality)
        "production.resumeMissingFrames" = [bool](Get-WzhkBuilderFirstValue $Profile @("production.resumeMissingFrames", "production.resumeEnabled") $true)
        "production.overwriteInvalidFrames" = [bool](Get-WzhkBuilderPropertyValue $Profile "production.overwriteInvalidFrames" $true)
        "production.overwriteExistingFrames" = $false
        "production.atomicPublication" = [bool](Get-WzhkBuilderFirstValue $Profile @("production.atomicPublication", "production.atomicChunkCommit") $true)
        "production.verifyEachChunk" = [bool](Get-WzhkBuilderFirstValue $Profile @("production.verifyEachChunk", "production.verifyExistingFrames") $true)
        "production.stopOnValidationFailure" = $true
        "output.rootDirectory" = [string](Get-WzhkBuilderPropertyValue $Profile "output.rootDirectory" (Join-Path $script:BuilderRepositoryRoot "final-output"))
        "output.lastKnownFreeGiB" = $currentFreeGiB
        "estimates.currentFreeDisk" = $currentFreeGiB
        "output.policy" = [string](Get-WzhkBuilderPropertyValue $Profile "output.policy" "create-new")
        "output.compatibilityKeys" = @(Get-WzhkBuilderPropertyValue $Profile "output.compatibilityKeys" @("sceneSha256", "profileSha256", "width", "height", "fps", "imageFormat", "frameStart", "frameEnd"))
        "dashboard.enabled" = [bool](Get-WzhkBuilderPropertyValue $Profile "dashboard.enabled" $true)
        "dashboard.autoLaunch" = [bool](Get-WzhkBuilderPropertyValue $Profile "dashboard.autoLaunch" $true)
        "dashboard.refreshSeconds" = [int](Get-WzhkBuilderPropertyValue $Profile "dashboard.refreshSeconds" 6)
        "dashboard.showLatestFrame" = [bool](Get-WzhkBuilderPropertyValue $Profile "dashboard.showLatestFrame" $true)
        "dashboard.showInflightFrames" = [bool](Get-WzhkBuilderPropertyValue $Profile "dashboard.showInflightFrames" $true)
        "dashboard.showPublishedFrames" = [bool](Get-WzhkBuilderPropertyValue $Profile "dashboard.showPublishedFrames" $true)
        "dashboard.showEta" = [bool](Get-WzhkBuilderPropertyValue $Profile "dashboard.showEta" $true)
        "dashboard.showRollingSecondsPerFrame" = [bool](Get-WzhkBuilderPropertyValue $Profile "dashboard.showRollingSecondsPerFrame" $true)
        "dashboard.showStorageGrowth" = [bool](Get-WzhkBuilderPropertyValue $Profile "dashboard.showStorageGrowth" $true)
        "dashboard.openOutputWhenComplete" = [bool](Get-WzhkBuilderPropertyValue $Profile "dashboard.openOutputWhenComplete" $true)
    }
    $audioHash = [string](Get-WzhkBuilderPropertyValue $Profile "audio.sha256" "")
    $approvedAudioBound = ($audioHash -match '^[A-Fa-f0-9]{64}$' -and $audioHash -ne ("0" * 64))
    $sourceStart = Get-WzhkBuilderPropertyValue $Profile "sourceTimeline.frameStart" $frameStart
    $sourceEnd = Get-WzhkBuilderPropertyValue $Profile "sourceTimeline.frameEnd" $frameEnd
    $sourceFps = [double](Get-WzhkBuilderPropertyValue $Profile "sourceTimeline.fps" $fps)
    $clockMatchesApprovedAudio = (
        [int]$sourceStart -eq $frameStart -and
        [int]$sourceEnd -eq $frameEnd -and
        [Math]::Abs($sourceFps - $fps) -le 0.0001
    )
    if ($format -eq "OPEN_EXR") {
        $values["encoding.master.enabled"] = $false
        $values["encoding.delivery.enabled"] = $false
        $values["imageSequence.encodingWarning"] = "Encoding is disabled for scene-linear OpenEXR until approved linearToDeliveryFilter values are configured."
    }
    elseif (-not $approvedAudioBound) {
        $values["encoding.master.enabled"] = $false
        $values["encoding.delivery.enabled"] = $false
        $values["imageSequence.encodingWarning"] = "Encoding is disabled until an exact approved audio SHA-256 identity is bound."
    }
    elseif (-not $clockMatchesApprovedAudio) {
        $values["encoding.master.enabled"] = $false
        $values["encoding.delivery.enabled"] = $false
        $values["imageSequence.encodingWarning"] = "Encoding is disabled because the selected timeline/FPS differs from the approved full-track audio clock."
    }
    else {
        $values["imageSequence.encodingWarning"] = ""
        $values["encoding.master.enabled"] = [bool](Get-WzhkBuilderPropertyValue $Profile "encoding.master.enabled" $true)
        $values["encoding.delivery.enabled"] = [bool](Get-WzhkBuilderPropertyValue $Profile "encoding.delivery.enabled" $true)
    }
    $updated = Set-WzhkBuilderValues -Profile $Profile -Values $values
    $compositor = Get-WzhkBuilderPropertyValue -InputObject $updated -PropertyPath "compositor" -Default $null
    if ($null -ne $compositor) {
        if ($null -eq $compositor.PSObject.Properties["fogGlow"]) {
            $compositor = Copy-WzhkBuilderObject -InputObject $compositor
            Add-Member -InputObject $compositor -NotePropertyName fogGlow -NotePropertyValue ([bool](Get-WzhkBuilderPropertyValue $updated "compositor.enabled" $true))
        }
        $updated = Set-WzhkProfileValue -Profile $updated -PropertyPath "render.compositor" -Value $compositor
    }
    $updated = Set-WzhkBuilderValues -Profile $updated -Values ([ordered]@{
        "encoding.masterEnabled" = [bool](Get-WzhkBuilderPropertyValue $updated "encoding.master.enabled" $true)
        "encoding.masterCodec" = [string](Get-WzhkBuilderPropertyValue $updated "encoding.master.videoCodec" "prores_ks")
        "encoding.deliveryEnabled" = [bool](Get-WzhkBuilderPropertyValue $updated "encoding.delivery.enabled" $true)
        "encoding.deliveryCodec" = [string](Get-WzhkBuilderPropertyValue $updated "encoding.delivery.videoCodec" "libx264")
        "encoding.audioCodec" = [string](Get-WzhkBuilderPropertyValue $updated "encoding.delivery.audioCodec" "aac")
    })
    if ($null -ne $originalUpdatedAt -and $null -ne $updated.PSObject.Properties["updatedAt"]) { $updated.updatedAt = $originalUpdatedAt }
    if ($null -ne $originalTimestampUpdatedAt -and $null -ne $updated.PSObject.Properties["timestamps"]) { $updated.timestamps.updatedAt = $originalTimestampUpdatedAt }
    if ($null -ne $originalAuthorization) {
        if ($null -eq $updated.PSObject.Properties["authorization"]) { Add-Member -InputObject $updated -NotePropertyName authorization -NotePropertyValue $originalAuthorization }
        else { $updated.authorization = $originalAuthorization }
    }
    return $updated
}

function Get-WzhkProfileBuilderStageFields {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][ValidateRange(1, 13)][int]$StageNumber
    )

    $field = New-Object System.Collections.Generic.List[object]
    switch ($StageNumber) {
        1 {
            $field.Add([pscustomobject]@{ Label = "Display name"; Value = Get-WzhkBuilderPropertyValue $Profile "displayName" "" })
            $field.Add([pscustomobject]@{ Label = "Profile ID"; Value = Get-WzhkBuilderPropertyValue $Profile "profileId" "" })
            $field.Add([pscustomobject]@{ Label = "Description"; Value = Get-WzhkBuilderPropertyValue $Profile "description" "" })
            $field.Add([pscustomobject]@{ Label = "Project"; Value = Get-WzhkBuilderPropertyValue $Profile "project" "" })
            $field.Add([pscustomobject]@{ Label = "Preset"; Value = Get-WzhkBuilderPropertyValue $Profile "preset" "" })
        }
        2 {
            $field.Add([pscustomobject]@{ Label = "Scene"; Value = Get-WzhkBuilderPropertyValue $Profile "approvedScene.path" "not selected" })
            $field.Add([pscustomobject]@{ Label = "Scene SHA-12"; Value = Get-WzhkShortHash ([string](Get-WzhkBuilderPropertyValue $Profile "approvedScene.sha256" "")) })
            $field.Add([pscustomobject]@{ Label = "Blender"; Value = Get-WzhkBuilderPropertyValue $Profile "approvedScene.blenderVersion" "unknown" })
            $field.Add([pscustomobject]@{ Label = "Objects / materials"; Value = [string]::Format("{0} / {1}", (Get-WzhkBuilderPropertyValue $Profile "approvedScene.objectCount" "unknown"), (Get-WzhkBuilderPropertyValue $Profile "approvedScene.materialCount" "unknown")) })
            $field.Add([pscustomobject]@{ Label = "Collections / F-curves"; Value = [string]::Format("{0} / {1}", (Get-WzhkBuilderPropertyValue $Profile "approvedScene.collectionCount" "unknown"), (Get-WzhkBuilderPropertyValue $Profile "approvedScene.fCurveCount" "unknown")) })
            $field.Add([pscustomobject]@{ Label = "Camera / preset"; Value = [string]::Format("{0} / {1}", (Get-WzhkBuilderPropertyValue $Profile "approvedScene.activeCamera" "unknown"), (Get-WzhkBuilderPropertyValue $Profile "approvedScene.preset" "unknown")) })
            $field.Add([pscustomobject]@{ Label = "Macro / audio curves"; Value = [string]::Format("{0} / {1}", (Get-WzhkBuilderPropertyValue $Profile "approvedScene.macroStateCount" "unknown"), (Get-WzhkBuilderPropertyValue $Profile "approvedScene.audioBusCurveCount" "unknown")) })
            $field.Add([pscustomobject]@{ Label = "Scene frame range"; Value = [string]::Format("{0}–{1}", (Get-WzhkBuilderPropertyValue $Profile "sourceTimeline.frameStart" "unknown"), (Get-WzhkBuilderPropertyValue $Profile "sourceTimeline.frameEnd" "unknown")) })
            $field.Add([pscustomobject]@{ Label = "Scene FPS"; Value = Get-WzhkBuilderPropertyValue $Profile "sourceTimeline.fps" "unknown" })
        }
        3 {
            $width = [int](Get-WzhkBuilderPropertyValue $Profile "resolution.width" 0)
            $height = [int](Get-WzhkBuilderPropertyValue $Profile "resolution.height" 0)
            $field.Add([pscustomobject]@{ Label = "Resolved size"; Value = Get-WzhkResolutionLabel -Width $width -Height $height })
            $field.Add([pscustomobject]@{ Label = "Percentage"; Value = ([string](Get-WzhkBuilderPropertyValue $Profile "resolution.percentage" 100) + "%") })
            $field.Add([pscustomobject]@{ Label = "Pixel aspect"; Value = Get-WzhkBuilderPropertyValue $Profile "resolution.pixelAspect" "1:1" })
            $field.Add([pscustomobject]@{ Label = "Display aspect"; Value = Get-WzhkBuilderPropertyValue $Profile "resolution.displayAspect" "unknown" })
            $field.Add([pscustomobject]@{ Label = "Dynamic range"; Value = Get-WzhkBuilderPropertyValue $Profile "resolution.dynamicRange" "SDR" })
        }
        4 {
            $field.Add([pscustomobject]@{ Label = "FPS"; Value = Get-WzhkBuilderPropertyValue $Profile "timeline.fps" "unknown" })
            $field.Add([pscustomobject]@{ Label = "Frame range"; Value = [string]::Format("{0}–{1}", (Get-WzhkBuilderPropertyValue $Profile "timeline.frameStart" "?"), (Get-WzhkBuilderPropertyValue $Profile "timeline.frameEnd" "?")) })
            $field.Add([pscustomobject]@{ Label = "Frame count"; Value = Get-WzhkBuilderPropertyValue $Profile "timeline.frameCount" "unknown" })
            $duration = [double](Get-WzhkBuilderPropertyValue $Profile "timeline.durationSeconds" 0.0)
            $field.Add([pscustomobject]@{ Label = "Duration"; Value = [TimeSpan]::FromSeconds($duration).ToString() })
        }
        5 {
            $field.Add([pscustomobject]@{ Label = "Quality mode"; Value = Get-WzhkBuilderPropertyValue $Profile "render.qualityMode" "CUSTOM" })
            $field.Add([pscustomobject]@{ Label = "Resolved samples"; Value = Get-WzhkBuilderPropertyValue $Profile "render.samples" "unknown" })
            $field.Add([pscustomobject]@{ Label = "Shadow pool"; Value = Get-WzhkBuilderPropertyValue $Profile "render.shadowPoolSize" "unknown" })
            $field.Add([pscustomobject]@{ Label = "Ray tracing"; Value = Get-WzhkBuilderBooleanText ([bool](Get-WzhkBuilderPropertyValue $Profile "render.rayTracing" $false)) })
            $field.Add([pscustomobject]@{ Label = "Motion blur"; Value = Get-WzhkBuilderBooleanText ([bool](Get-WzhkBuilderPropertyValue $Profile "render.motionBlur" $false)) })
        }
        6 {
            foreach ($pair in @(
                @("Engine", "render.engine"), @("Samples", "render.samples"),
                @("Shadow pool", "render.shadowPoolSize"), @("Shadow rays", "render.shadowRayCount"),
                @("Shadow scale", "render.shadowResolutionScale"), @("Ray tracing", "render.rayTracing"),
                @("Ray method", "render.rayTracingMethod"), @("Volumetric tile", "render.volumetricTileSize"),
                @("Volumetric samples", "render.volumetricSamples"), @("Volumetric shadow samples", "render.volumetricShadowSamples"),
                @("Volumetric ray depth", "render.volumetricRayDepth"), @("Volumetric shadows", "render.volumetricShadows"),
                @("Motion blur", "render.motionBlur"), @("Transparent film", "render.filmTransparent"),
                @("Compositor", "render.useCompositing"), @("Fog Glow", "compositor.fogGlow"),
                @("Fog Glow quality", "compositor.fogGlowQuality"), @("Fog Glow threshold", "compositor.fogGlowThreshold"),
                @("Fog Glow strength", "compositor.fogGlowStrength"), @("Fog Glow size", "compositor.fogGlowSize"),
                @("Fog Glow iterations", "compositor.fogGlowIterations"),
                @("Dither", "render.ditherIntensity"), @("High quality normals", "render.highQualityNormals")
            )) {
                $field.Add([pscustomobject]@{ Label = $pair[0]; Value = Get-WzhkBuilderPropertyValue $Profile $pair[1] "unknown" })
            }
            $field.Add([pscustomobject]@{ Label = "Device policy"; Value = "Blender/system configuration (checked at preflight)" })
        }
        7 {
            $field.Add([pscustomobject]@{ Label = "Format"; Value = Get-WzhkBuilderPropertyValue $Profile "imageSequence.format" "unknown" })
            $field.Add([pscustomobject]@{ Label = "Bit depth / mode"; Value = [string]::Format("{0}-bit / {1}", (Get-WzhkBuilderPropertyValue $Profile "imageSequence.bitDepth" "?"), (Get-WzhkBuilderPropertyValue $Profile "imageSequence.colorMode" "?")) })
            $field.Add([pscustomobject]@{ Label = "Compression"; Value = Get-WzhkBuilderPropertyValue $Profile "imageSequence.compression" "unknown" })
            $field.Add([pscustomobject]@{ Label = "Filename pattern"; Value = Get-WzhkBuilderPropertyValue $Profile "imageSequence.filenamePattern" "unknown" })
            $field.Add([pscustomobject]@{ Label = "Frames subdir"; Value = Get-WzhkBuilderPropertyValue $Profile "output.framesSubdirectory" "frames" })
            $warning = [string](Get-WzhkBuilderPropertyValue $Profile "imageSequence.encodingWarning" "")
            if (-not [string]::IsNullOrWhiteSpace($warning)) { $field.Add([pscustomobject]@{ Label = "Encoding warning"; Value = $warning }) }
        }
        8 {
            foreach ($pair in @(@("Display device", "colorManagement.displayDevice"), @("View transform", "colorManagement.viewTransform"), @("Look", "colorManagement.look"), @("Exposure", "colorManagement.exposure"), @("Gamma", "colorManagement.gamma"), @("Sequencer space", "colorManagement.sequencerColorSpace"))) {
                $field.Add([pscustomobject]@{ Label = $pair[0]; Value = Get-WzhkBuilderPropertyValue $Profile $pair[1] "unknown" })
            }
        }
        9 {
            $chunk = [int](Get-WzhkBuilderPropertyValue $Profile "production.framesPerChunk" 1)
            $frameCount = [int](Get-WzhkBuilderPropertyValue $Profile "timeline.frameCount" 1)
            $field.Add([pscustomobject]@{ Label = "Frames / chunk"; Value = $chunk })
            $field.Add([pscustomobject]@{ Label = "Estimated chunks"; Value = [int][Math]::Ceiling($frameCount / [double]$chunk) })
            foreach ($pair in @(@("Resume missing", "production.resumeEnabled"), @("Overwrite invalid", "production.overwriteInvalidFrames"), @("Overwrite valid", "production.overwriteValidFrames"), @("Atomic publication", "production.atomicChunkCommit"), @("Verify existing", "production.verifyExistingFrames"), @("Stop on failure", "production.stopOnValidationFailure"))) {
                $field.Add([pscustomobject]@{ Label = $pair[0]; Value = Get-WzhkBuilderBooleanText ([bool](Get-WzhkBuilderPropertyValue $Profile $pair[1] $false)) })
            }
        }
        10 {
            foreach ($pair in @(@("Output root", "output.rootDirectory"), @("Output policy", "output.policy"), @("Directory pattern", "output.directoryPattern"), @("Frames subdir", "output.framesSubdirectory"), @("Compatibility", "output.compatibilityManifest"), @("Never mix profiles", "output.neverMixProfiles"))) {
                $field.Add([pscustomobject]@{ Label = $pair[0]; Value = Get-WzhkBuilderPropertyValue $Profile $pair[1] "unknown" })
            }
        }
        11 {
            foreach ($pair in @(@("Master enabled", "encoding.master.enabled"), @("Master codec", "encoding.master.videoCodec"), @("Master audio", "encoding.master.audioCodec"), @("Delivery enabled", "encoding.delivery.enabled"), @("Delivery codec", "encoding.delivery.videoCodec"), @("CRF", "encoding.delivery.crf"), @("Preset", "encoding.delivery.preset"), @("Pixel format", "encoding.delivery.pixelFormat"), @("AAC bitrate", "encoding.delivery.audioBitrate"), @("Fast start", "encoding.delivery.fastStart"))) {
                $field.Add([pscustomobject]@{ Label = $pair[0]; Value = Get-WzhkBuilderPropertyValue $Profile $pair[1] "unknown" })
            }
        }
        12 {
            foreach ($pair in @(@("Dashboard enabled", "dashboard.enabled"), @("Auto launch", "dashboard.autoLaunch"), @("Refresh seconds", "dashboard.refreshSeconds"), @("Latest frame", "dashboard.showLatestFrame"), @("In-flight", "dashboard.showInflightFrames"), @("Published", "dashboard.showPublishedFrames"), @("ETA", "dashboard.showEta"), @("Rolling speed", "dashboard.showRollingSecondsPerFrame"), @("Storage growth", "dashboard.showStorageGrowth"), @("Open when complete", "dashboard.openOutputWhenComplete"))) {
                $field.Add([pscustomobject]@{ Label = $pair[0]; Value = Get-WzhkBuilderPropertyValue $Profile $pair[1] "unknown" })
            }
        }
        13 {
            $validation = Test-WzhkRenderProfile -Profile $Profile
            $field.Add([pscustomobject]@{ Label = "Validation"; Value = $(if ($validation.Valid) { "VALID" } else { "INVALID" }) })
            $field.Add([pscustomobject]@{ Label = "Warnings"; Value = $validation.Warnings.Count })
            $field.Add([pscustomobject]@{ Label = "Errors"; Value = $validation.Errors.Count })
        }
    }
    return $field.ToArray()
}

function Read-WzhkBuilderBoolean {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [bool]$Current
    )

    $items = @(
        (New-WzhkBuilderChoiceItem -Label "ENABLED" -Description "Store this setting as true." -Value $true),
        (New-WzhkBuilderChoiceItem -Label "DISABLED" -Description "Store this setting as false." -Value $false)
    )
    $selection = Read-WzhkChoice -Title $Title -Items $items -Context @("Current: " + (Get-WzhkBuilderBooleanText $Current)) -ReturnItem
    if ($null -eq $selection) { return $null }
    return [pscustomobject]@{ Selected = $true; Value = [bool]$selection.Value }
}

function Read-WzhkBuilderEvenInteger {
    param(
        [string]$Prompt,
        [int]$Default,
        [int]$Minimum,
        [int]$Maximum
    )

    while ($true) {
        $value = Read-WzhkIntegerInput -Prompt $Prompt -Default $Default -Minimum $Minimum -Maximum $Maximum -AllowCancel
        if ($null -eq $value) { return $null }
        if (($value % 2) -eq 0) { return [int]$value }
        Write-Host "  INPUT REJECTED: encoder-compatible dimensions must be even." -ForegroundColor Red
    }
}

function Edit-WzhkBuilderIdentity {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $displayName = Read-WzhkTextInput -Prompt "Profile display name" -Default ([string](Get-WzhkBuilderPropertyValue $Profile "displayName" "Render Profile")) -MinimumLength 1 -MaximumLength 80 -Required -AllowCancel
    if ($null -eq $displayName) { return $Profile }
    $profileIdText = Read-WzhkTextInput -Prompt "Filesystem-safe profile ID / slug" -Default ([string](Get-WzhkBuilderPropertyValue $Profile "profileId" "render-profile")) -MinimumLength 1 -MaximumLength 64 -Required -AllowCancel
    if ($null -eq $profileIdText) { return $Profile }
    $description = Read-WzhkTextInput -Prompt "Profile description" -Default ([string](Get-WzhkBuilderPropertyValue $Profile "description" "Explicit resumable TrackPrompt render profile.")) -MaximumLength 240 -AllowCancel
    if ($null -eq $description) { return $Profile }
    $project = Read-WzhkTextInput -Prompt "Project" -Default ([string](Get-WzhkBuilderPropertyValue $Profile "project" "trip-to-andromeda")) -MinimumLength 1 -MaximumLength 80 -Required -AllowCancel
    if ($null -eq $project) { return $Profile }
    $preset = Read-WzhkTextInput -Prompt "Visualizer preset" -Default ([string](Get-WzhkBuilderPropertyValue $Profile "preset" "space-journey")) -MinimumLength 1 -MaximumLength 80 -Required -AllowCancel
    if ($null -eq $preset) { return $Profile }

    return Set-WzhkBuilderValues -Profile $Profile -Values ([ordered]@{
        "displayName" = ConvertTo-WzhkSafeProfileName -Name $displayName
        "profileId" = (ConvertTo-WzhkProfileSlug -Name $profileIdText).ToUpperInvariant()
        "description" = $description
        "project" = ConvertTo-WzhkSafeProfileName -Name $project -Fallback "trackprompt"
        "preset" = ConvertTo-WzhkSafeProfileName -Name $preset -Fallback "custom"
    })
}

function Edit-WzhkBuilderScene {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][object[]]$Scenes
    )

    if ($Scenes.Count -eq 0) {
        Show-WzhkMessage -Title "NO VERIFIED APPROVED SCENE" -Lines @(
            "The profile builder requires an existing frozen .blend candidate.",
            "No renderer or authorization action was attempted."
        ) -Color Red
        return [pscustomobject]@{ Profile = $Profile; Scene = $null }
    }

    $pageSize = 7
    $page = 0
    $pageCount = [Math]::Max(1, [int][Math]::Ceiling($Scenes.Count / [double]$pageSize))
    $selectedScene = $null
    while ($null -eq $selectedScene) {
        $items = New-Object System.Collections.Generic.List[object]
        foreach ($scene in @($Scenes | Select-Object -Skip ($page * $pageSize) -First $pageSize)) {
            $items.Add((New-WzhkBuilderChoiceItem `
                -Label $scene.Name `
                -Description ([string]::Format("SHA-12 {0}  •  frames {1}–{2}  •  {3} fps  •  {4}", (Get-WzhkShortHash $scene.Sha256), $scene.FrameStart, $scene.FrameEnd, $scene.Fps, $scene.Preset)) `
                -Value ([pscustomobject]@{ Kind = "Scene"; Scene = $scene })))
        }
        if ($page -gt 0) { $items.Add((New-WzhkBuilderChoiceItem "PREVIOUS PAGE" "Show earlier approved scenes." ([pscustomobject]@{ Kind = "Previous" }))) }
        if ($page -lt ($pageCount - 1)) { $items.Add((New-WzhkBuilderChoiceItem "NEXT PAGE" "Show more approved scenes." ([pscustomobject]@{ Kind = "Next" }))) }
        $selection = Read-WzhkChoice -Title "PROFILE BUILDER // APPROVED FROZEN SCENE" -Items $items.ToArray() -Context @(
            "Only existing .blend files hashed locally are selectable.",
            "Selecting a scene invalidates any prior profile authorization.",
            [string]::Format("Page {0} of {1}  •  {2} approved scenes", ($page + 1), $pageCount, $Scenes.Count)
        ) -ReturnItem
        if ($null -eq $selection) { return [pscustomobject]@{ Profile = $Profile; Scene = $null } }
        if ($selection.Value.Kind -eq "Previous") { $page -= 1; continue }
        if ($selection.Value.Kind -eq "Next") { $page += 1; continue }
        $selectedScene = $selection.Value.Scene
    }
    # A different frozen scene changes the source timing and preset contract.
    # Reset those scene-derived fields now so an old range/preset cannot survive
    # until the first Blender chunk.
    $updated = Set-WzhkBuilderScene -Profile $Profile -Scene $selectedScene -ResetSceneDefaults
    return [pscustomobject]@{ Profile = $updated; Scene = $selectedScene }
}

function Edit-WzhkBuilderResolution {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $items = @(
        (New-WzhkBuilderChoiceItem "FULL HD — 1920×1080" "16:9 encoder-compatible Full HD." "FHD"),
        (New-WzhkBuilderChoiceItem "1440P — 2560×1440" "16:9 balanced high-resolution output." "1440P"),
        (New-WzhkBuilderChoiceItem "NATIVE 4K — 3840×2160" "Native UHD; substantially higher time and storage." "4K"),
        (New-WzhkBuilderChoiceItem "CUSTOM RESOLUTION" "Enter explicit even dimensions, percentage, and pixel aspect." "CUSTOM")
    )
    $choice = Read-WzhkChoice -Title "PROFILE BUILDER // RESOLUTION" -Items $items -ReturnItem
    if ($null -eq $choice) { return $Profile }

    $width = 1920
    $height = 1080
    switch ([string]$choice.Value) {
        "1440P" { $width = 2560; $height = 1440 }
        "4K" { $width = 3840; $height = 2160 }
        "CUSTOM" {
            $widthValue = Read-WzhkBuilderEvenInteger -Prompt "Resolved output width" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "resolution.width" 1920)) -Minimum 16 -Maximum 16384
            if ($null -eq $widthValue) { return $Profile }
            $heightValue = Read-WzhkBuilderEvenInteger -Prompt "Resolved output height" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "resolution.height" 1080)) -Minimum 16 -Maximum 16384
            if ($null -eq $heightValue) { return $Profile }
            $width = $widthValue
            $height = $heightValue
        }
    }

    $percentage = Read-WzhkIntegerInput -Prompt "Resolution percentage (resolved profiles require 100%)" -Default 100 -Minimum 100 -Maximum 100 -AllowCancel
    if ($null -eq $percentage) { return $Profile }
    $aspectItems = @(
        (New-WzhkBuilderChoiceItem "SQUARE PIXELS — 1:1" "Recommended production pixel aspect." "SQUARE"),
        (New-WzhkBuilderChoiceItem "CUSTOM PIXEL ASPECT" "Enter explicit X and Y values." "CUSTOM")
    )
    $aspectChoice = Read-WzhkChoice -Title "PROFILE BUILDER // PIXEL ASPECT" -Items $aspectItems -ReturnItem
    if ($null -eq $aspectChoice) { return $Profile }
    $pixelAspect = "1:1"
    $pixelX = 1.0
    $pixelY = 1.0
    if ($aspectChoice.Value -eq "CUSTOM") {
        $pixelX = Read-WzhkDecimalInput -Prompt "Pixel aspect X" -Default 1.0 -Minimum 0.01 -Maximum 100.0 -AllowCancel
        if ($null -eq $pixelX) { return $Profile }
        $pixelY = Read-WzhkDecimalInput -Prompt "Pixel aspect Y" -Default 1.0 -Minimum 0.01 -Maximum 100.0 -AllowCancel
        if ($null -eq $pixelY) { return $Profile }
        $culture = [Globalization.CultureInfo]::InvariantCulture
        $pixelAspect = [string]::Format($culture, "{0:0.####}:{1:0.####}", $pixelX, $pixelY)
    }

    $displayAspect = Get-WzhkBuilderDisplayAspect -Width $width -Height $height -PixelAspectX $pixelX -PixelAspectY $pixelY
    $displayRatio = ($width * [double]$pixelX) / ($height * [double]$pixelY)
    if ([Math]::Abs($displayRatio - (16.0 / 9.0)) -gt 0.0005) {
        Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // ASPECT WARNING"
        Write-WzhkFrameLine -Text ("  Dimensions plus pixel aspect resolve to " + $displayAspect + ", not 16:9.") -Color Yellow
        if (-not (Read-WzhkYesNo -Prompt "Keep this explicit custom aspect?" -YesText "KEEP CUSTOM" -NoText "FIX RESOLUTION")) { return $Profile }
    }

    return Set-WzhkBuilderValues -Profile $Profile -Values ([ordered]@{
        "resolution.label" = Get-WzhkResolutionLabel -Width $width -Height $height
        "resolution.width" = $width
        "resolution.height" = $height
        "resolution.percentage" = [int]$percentage
        "resolution.pixelAspect" = $pixelAspect
        "resolution.pixelAspectX" = [double]$pixelX
        "resolution.pixelAspectY" = [double]$pixelY
        "resolution.displayAspect" = $displayAspect
        "resolution.dynamicRange" = "SDR"
        "aspect.pixel" = $pixelAspect
        "aspect.display" = $displayAspect
    })
}

function Edit-WzhkBuilderTimeline {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [AllowNull()][object]$Scene
    )

    $nativeFps = if ($null -ne $Scene) { [double]$Scene.Fps } else { [double](Get-WzhkBuilderPropertyValue $Profile "timeline.fps" 30.0) }
    $fpsItems = @(
        (New-WzhkBuilderChoiceItem "SCENE-NATIVE FPS" ([string]::Format("Preserve approved timing at {0} fps.", $nativeFps)) $nativeFps),
        (New-WzhkBuilderChoiceItem "24 FPS" "Cinema integer frame rate." 24.0),
        (New-WzhkBuilderChoiceItem "25 FPS" "PAL integer frame rate." 25.0),
        (New-WzhkBuilderChoiceItem "30 FPS" "Recommended for this approved TrackPrompt scene." 30.0),
        (New-WzhkBuilderChoiceItem "50 FPS" "High-frame-rate PAL delivery." 50.0),
        (New-WzhkBuilderChoiceItem "60 FPS" "High-frame-rate delivery." 60.0),
        (New-WzhkBuilderChoiceItem "23.976 FPS" "Fractional cinema delivery." 23.976),
        (New-WzhkBuilderChoiceItem "29.97 FPS" "Fractional broadcast delivery." 29.97),
        (New-WzhkBuilderChoiceItem "CUSTOM FPS" "Enter a finite rate up to 240 fps." "CUSTOM")
    )
    $fpsChoice = Read-WzhkChoice -Title "PROFILE BUILDER // FRAME RATE" -Items $fpsItems -ReturnItem
    if ($null -eq $fpsChoice) { return $Profile }
    $fps = if ($fpsChoice.Value -eq "CUSTOM") {
        Read-WzhkDecimalInput -Prompt "Frames per second" -Default $nativeFps -Minimum 1.0 -Maximum 240.0 -AllowCancel
    }
    else { [double]$fpsChoice.Value }
    if ($null -eq $fps) { return $Profile }

    if ([Math]::Abs([double]$fps - $nativeFps) -gt 0.0001) {
        Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // TIMING WARNING"
        Write-WzhkFrameLine -Text ([string]::Format("  Scene-native timing is {0} fps; selected value is {1} fps.", $nativeFps, $fps)) -Color Yellow
        Write-WzhkFrameLine -Text "  Changing FPS can alter approved timing and audio synchronization." -Color Yellow
        if (-not (Read-WzhkYesNo -Prompt "Keep the non-native FPS?" -YesText "KEEP FPS" -NoText "FIX FPS")) { return $Profile }
    }

    $sceneStart = if ($null -ne $Scene) { [int]$Scene.FrameStart } else { [int](Get-WzhkBuilderPropertyValue $Profile "timeline.frameStart" 1) }
    $sceneEnd = if ($null -ne $Scene) { [int]$Scene.FrameEnd } else { [int](Get-WzhkBuilderPropertyValue $Profile "timeline.frameEnd" 13029) }
    $rangeItems = @(
        (New-WzhkBuilderChoiceItem "FULL SCENE TIMELINE" ([string]::Format("Frames {0}–{1}.", $sceneStart, $sceneEnd)) "FULL"),
        (New-WzhkBuilderChoiceItem "CUSTOM BOUNDED RANGE" "Enter start and end within the approved scene." "CUSTOM"),
        (New-WzhkBuilderChoiceItem "SHORT BOUNDED TEST RANGE" "Enter a start frame and short frame count." "SHORT")
    )
    $rangeChoice = Read-WzhkChoice -Title "PROFILE BUILDER // FRAME RANGE" -Items $rangeItems -ReturnItem
    if ($null -eq $rangeChoice) { return $Profile }
    $frameStart = $sceneStart
    $frameEnd = $sceneEnd
    if ($rangeChoice.Value -eq "CUSTOM") {
        $startValue = Read-WzhkIntegerInput -Prompt "First frame" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "timeline.frameStart" $sceneStart)) -Minimum $sceneStart -Maximum $sceneEnd -AllowCancel
        if ($null -eq $startValue) { return $Profile }
        $endValue = Read-WzhkIntegerInput -Prompt "Last frame" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "timeline.frameEnd" $sceneEnd)) -Minimum $startValue -Maximum $sceneEnd -AllowCancel
        if ($null -eq $endValue) { return $Profile }
        $frameStart = [int]$startValue
        $frameEnd = [int]$endValue
    }
    elseif ($rangeChoice.Value -eq "SHORT") {
        $startValue = Read-WzhkIntegerInput -Prompt "Short range first frame" -Default $sceneStart -Minimum $sceneStart -Maximum $sceneEnd -AllowCancel
        if ($null -eq $startValue) { return $Profile }
        $maximumCount = $sceneEnd - [int]$startValue + 1
        $countValue = Read-WzhkIntegerInput -Prompt "Short range frame count" -Default ([Math]::Min(90, $maximumCount)) -Minimum 1 -Maximum $maximumCount -AllowCancel
        if ($null -eq $countValue) { return $Profile }
        $frameStart = [int]$startValue
        $frameEnd = $frameStart + [int]$countValue - 1
    }
    $frameCount = $frameEnd - $frameStart + 1
    $duration = $frameCount / [double]$fps
    $timelineValues = [ordered]@{
        "timeline.frameStart" = $frameStart; "timeline.frameEnd" = $frameEnd; "timeline.frameCount" = $frameCount
        "timeline.fps" = [double]$fps; "timeline.durationSeconds" = $duration
        "frameStart" = $frameStart; "frameEnd" = $frameEnd; "fps" = [double]$fps; "durationSeconds" = $duration
    }
    if ($frameStart -ne $sceneStart -or $frameEnd -ne $sceneEnd -or [Math]::Abs([double]$fps - $nativeFps) -gt 0.0001) {
        $timelineValues["encoding.master.enabled"] = $false
        $timelineValues["encoding.delivery.enabled"] = $false
        $timelineValues["encoding.clockWarning"] = "Encoding disabled because the selected timeline/FPS no longer matches the approved full-track audio identity."
    }
    else {
        $timelineValues["encoding.clockWarning"] = ""
    }
    return Set-WzhkBuilderValues -Profile $Profile -Values $timelineValues
}

function Edit-WzhkBuilderQuality {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $items = New-Object System.Collections.Generic.List[object]
    foreach ($mode in @("DRAFT", "PREVIEW", "BALANCED", "HIGH", "ULTRA", "CUSTOM")) {
        $resolved = Resolve-WzhkQualitySettings -Mode $mode
        $description = if ($mode -eq "CUSTOM") { "Keep current explicit values, then edit Render Settings." } else { [string]::Format("{0} samples  •  shadow {1}  •  ray tracing {2}", $resolved.Samples, $resolved.ShadowPoolSize, $resolved.RayTracing) }
        $items.Add((New-WzhkBuilderChoiceItem $mode $description $mode))
    }
    $choice = Read-WzhkChoice -Title "PROFILE BUILDER // QUALITY MODE" -Items $items.ToArray() -Context @("Labels always resolve to explicit Blender 5.2 EEVEE values.") -ReturnItem
    if ($null -eq $choice) { return $Profile }
    if ($choice.Value -eq "ULTRA") {
        Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // ULTRA WARNING"
        Write-WzhkFrameLine -Text "  ULTRA substantially increases render time and may increase storage pressure." -Color Yellow
        if (-not (Read-WzhkYesNo -Prompt "Apply the explicit ULTRA settings?" -YesText "APPLY ULTRA" -NoText "FIX QUALITY")) { return $Profile }
    }
    return Set-WzhkBuilderQualityMode -Profile $Profile -Mode ([string]$choice.Value)
}

function Edit-WzhkBuilderRenderSettings {
    param([Parameter(Mandatory = $true)][object]$Profile)

    Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // EEVEE CONTRACT"
    Write-WzhkFrameLine -Text "  Engine: BLENDER_EEVEE (approved and renderer-consumed)." -Color Green
    Write-WzhkFrameLine -Text "  Execution device is selected by Blender/system configuration, not stored as a false scene setting." -Color DarkGray
    $samples = Read-WzhkIntegerInput -Prompt "Final render samples" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "render.samples" 64)) -Minimum 1 -Maximum 4096 -AllowCancel
    if ($null -eq $samples) { return $Profile }

    $shadowItems = New-Object System.Collections.Generic.List[object]
    foreach ($size in @("128", "256", "512", "1024", "2048")) { $shadowItems.Add((New-WzhkBuilderChoiceItem ("SHADOW POOL " + $size) "Blender 5.2 EEVEE approved enum value." $size)) }
    $shadowChoice = Read-WzhkChoice -Title "PROFILE BUILDER // SHADOW QUALITY" -Items $shadowItems.ToArray() -ReturnItem
    if ($null -eq $shadowChoice) { return $Profile }
    $shadowRays = Read-WzhkIntegerInput -Prompt "Shadow ray count" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "render.shadowRayCount" 1)) -Minimum 1 -Maximum 4 -AllowCancel
    if ($null -eq $shadowRays) { return $Profile }
    $shadowScale = Read-WzhkDecimalInput -Prompt "Shadow resolution scale" -Default ([double](Get-WzhkBuilderPropertyValue $Profile "render.shadowResolutionScale" 1.0)) -Minimum 0.0 -Maximum 1.0 -AllowCancel
    if ($null -eq $shadowScale) { return $Profile }

    $rayChoice = Read-WzhkBuilderBoolean -Title "PROFILE BUILDER // EEVEE RAY TRACING" -Current ([bool](Get-WzhkBuilderPropertyValue $Profile "render.rayTracing" $false))
    if ($null -eq $rayChoice) { return $Profile }
    $methodItems = @(
        (New-WzhkBuilderChoiceItem "PROBE" "Blender 5.2 probe-based ray tracing method." "PROBE"),
        (New-WzhkBuilderChoiceItem "SCREEN" "Blender 5.2 screen-space ray tracing method." "SCREEN")
    )
    $methodChoice = Read-WzhkChoice -Title "PROFILE BUILDER // RAY TRACING METHOD" -Items $methodItems -ReturnItem
    if ($null -eq $methodChoice) { return $Profile }

    $tileItems = New-Object System.Collections.Generic.List[object]
    foreach ($tile in @("1", "2", "4", "8", "16")) { $tileItems.Add((New-WzhkBuilderChoiceItem ("VOLUMETRIC TILE " + $tile) "Supported Blender 5.2 EEVEE tile size." $tile)) }
    $tileChoice = Read-WzhkChoice -Title "PROFILE BUILDER // VOLUMETRIC TILE" -Items $tileItems.ToArray() -ReturnItem
    if ($null -eq $tileChoice) { return $Profile }
    $volSamples = Read-WzhkIntegerInput -Prompt "Volumetric samples" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "render.volumetricSamples" 32)) -Minimum 1 -Maximum 256 -AllowCancel
    if ($null -eq $volSamples) { return $Profile }
    $volShadowSamples = Read-WzhkIntegerInput -Prompt "Volumetric shadow samples" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "render.volumetricShadowSamples" 8)) -Minimum 1 -Maximum 128 -AllowCancel
    if ($null -eq $volShadowSamples) { return $Profile }
    $volDepth = Read-WzhkIntegerInput -Prompt "Volumetric ray depth" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "render.volumetricRayDepth" 8)) -Minimum 1 -Maximum 16 -AllowCancel
    if ($null -eq $volDepth) { return $Profile }
    $volShadowsChoice = Read-WzhkBuilderBoolean -Title "PROFILE BUILDER // VOLUMETRIC SHADOWS" -Current ([bool](Get-WzhkBuilderPropertyValue $Profile "render.volumetricShadows" $false))
    if ($null -eq $volShadowsChoice) { return $Profile }
    $motionChoice = Read-WzhkBuilderBoolean -Title "PROFILE BUILDER // MOTION BLUR" -Current ([bool](Get-WzhkBuilderPropertyValue $Profile "render.motionBlur" $false))
    if ($null -eq $motionChoice) { return $Profile }
    Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // OPAQUE RGB CONTRACT"
    Write-WzhkFrameLine -Text "  Transparent film is fixed OFF because canonical resumable frames are RGB without alpha." -Color Green
    $compositorChoice = Read-WzhkBuilderBoolean -Title "PROFILE BUILDER // COMPOSITOR" -Current ([bool](Get-WzhkBuilderPropertyValue $Profile "render.useCompositing" $true))
    if ($null -eq $compositorChoice) { return $Profile }
    $fogGlowChoice = Read-WzhkBuilderBoolean -Title "PROFILE BUILDER // FOG GLOW" -Current ([bool](Get-WzhkBuilderFirstValue $Profile @("compositor.fogGlow", "compositor.enabled") $true))
    if ($null -eq $fogGlowChoice) { return $Profile }
    $resolvedFogGlow = ([bool]$compositorChoice.Value -and [bool]$fogGlowChoice.Value)
    if (-not [bool]$compositorChoice.Value -and [bool]$fogGlowChoice.Value) {
        Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // COMPOSITOR COUPLING"
        Write-WzhkFrameLine -Text "  Fog Glow is resolved OFF because the compositor is disabled." -Color Yellow
    }
    $fogQualityItems = @(
        (New-WzhkBuilderChoiceItem "LOW" "Fastest reviewed Blender 5.2 Fog Glow quality." "LOW"),
        (New-WzhkBuilderChoiceItem "MEDIUM" "Balanced reviewed Blender 5.2 Fog Glow quality." "MEDIUM"),
        (New-WzhkBuilderChoiceItem "HIGH" "Highest reviewed Blender 5.2 Fog Glow quality." "HIGH")
    )
    $fogQualityChoice = Read-WzhkChoice -Title "PROFILE BUILDER // FOG GLOW QUALITY" -Items $fogQualityItems -ReturnItem
    if ($null -eq $fogQualityChoice) { return $Profile }
    $fogThreshold = Read-WzhkDecimalInput -Prompt "Fog Glow threshold" -Default ([double](Get-WzhkBuilderPropertyValue $Profile "compositor.fogGlowThreshold" 1.026)) -Minimum 0.0 -Maximum 100.0 -AllowCancel
    if ($null -eq $fogThreshold) { return $Profile }
    $fogStrength = Read-WzhkDecimalInput -Prompt "Fog Glow strength" -Default ([double](Get-WzhkBuilderPropertyValue $Profile "compositor.fogGlowStrength" 0.418)) -Minimum 0.0 -Maximum 1.0 -AllowCancel
    if ($null -eq $fogStrength) { return $Profile }
    $fogSize = Read-WzhkDecimalInput -Prompt "Fog Glow size" -Default ([double](Get-WzhkBuilderPropertyValue $Profile "compositor.fogGlowSize" 0.704)) -Minimum 0.0 -Maximum 1.0 -AllowCancel
    if ($null -eq $fogSize) { return $Profile }
    $fogIterations = Read-WzhkIntegerInput -Prompt "Fog Glow iterations" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "compositor.fogGlowIterations" 3)) -Minimum 1 -Maximum 5 -AllowCancel
    if ($null -eq $fogIterations) { return $Profile }
    $dither = Read-WzhkDecimalInput -Prompt "Dither intensity" -Default ([double](Get-WzhkBuilderPropertyValue $Profile "render.ditherIntensity" 1.0)) -Minimum 0.0 -Maximum 10.0 -AllowCancel
    if ($null -eq $dither) { return $Profile }

    return Set-WzhkBuilderValues -Profile $Profile -Values ([ordered]@{
        "render.engine" = "BLENDER_EEVEE"
        "render.samples" = [int]$samples
        "render.shadowPoolSize" = [string]$shadowChoice.Value
        "render.shadowRayCount" = [int]$shadowRays
        "render.shadowResolutionScale" = [double]$shadowScale
        "render.rayTracing" = [bool]$rayChoice.Value
        "render.rayTracingMethod" = [string]$methodChoice.Value
        "render.highQualityNormals" = $false
        "render.volumetricTileSize" = [string]$tileChoice.Value
        "render.volumetricSamples" = [int]$volSamples
        "render.volumetricShadowSamples" = [int]$volShadowSamples
        "render.volumetricRayDepth" = [int]$volDepth
        "render.volumetricShadows" = [bool]$volShadowsChoice.Value
        "render.motionBlur" = [bool]$motionChoice.Value
        "render.filmTransparent" = $false
        "render.useCompositing" = [bool]$compositorChoice.Value
        "render.ditherIntensity" = [double]$dither
        "compositor.enabled" = [bool]$compositorChoice.Value
        "compositor.fogGlow" = [bool]$resolvedFogGlow
        "compositor.fogGlowEnabled" = [bool]$resolvedFogGlow
        "compositor.fogGlowQuality" = [string]$fogQualityChoice.Value
        "compositor.fogGlowThreshold" = [double]$fogThreshold
        "compositor.fogGlowStrength" = [double]$fogStrength
        "compositor.fogGlowSize" = [double]$fogSize
        "compositor.fogGlowIterations" = [int]$fogIterations
    })
}

function Edit-WzhkBuilderImageSequence {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $items = @(
        (New-WzhkBuilderChoiceItem "PNG 8-BIT RGB" "Compact resumable sequence with baked display transform." "PNG8"),
        (New-WzhkBuilderChoiceItem "PNG 16-BIT RGB" "Recommended production sequence with baked display transform." "PNG16"),
        (New-WzhkBuilderChoiceItem "OPENEXR HALF-FLOAT RGB" "Scene-linear ZIP/PIZ sequence; encoding remains disabled without approved linear filters." "EXR")
    )
    $choice = Read-WzhkChoice -Title "PROFILE BUILDER // IMAGE SEQUENCE" -Items $items -Context @("Direct full-length Blender-to-movie output is intentionally unavailable.") -ReturnItem
    if ($null -eq $choice) { return $Profile }
    $format = "PNG"
    $extension = "png"
    $bitDepth = if ($choice.Value -eq "PNG8") { 8 } else { 16 }
    $compression = 15
    if ($choice.Value -eq "EXR") {
        $format = "OPEN_EXR"
        $extension = "exr"
        $codecItems = @(
            (New-WzhkBuilderChoiceItem "ZIP" "Lossless OpenEXR ZIP compression." "ZIP"),
            (New-WzhkBuilderChoiceItem "PIZ" "Lossless wavelet OpenEXR compression." "PIZ")
        )
        $codecChoice = Read-WzhkChoice -Title "PROFILE BUILDER // OPENEXR COMPRESSION" -Items $codecItems -ReturnItem
        if ($null -eq $codecChoice) { return $Profile }
        $compression = [string]$codecChoice.Value
    }
    else {
        $compressionValue = Read-WzhkIntegerInput -Prompt "PNG compression (0 fastest through 100 smallest)" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "imageSequence.compression" 15)) -Minimum 0 -Maximum 100 -AllowCancel
        if ($null -eq $compressionValue) { return $Profile }
        $compression = [int]$compressionValue
    }

    $requiredPattern = [string]::Format("frame_%06d.{0}", $extension)
    while ($true) {
        $pattern = Read-WzhkTextInput -Prompt "Filename pattern (renderer requires canonical six-digit frames)" -Default $requiredPattern -MinimumLength 1 -MaximumLength 80 -Required -AllowCancel
        if ($null -eq $pattern) { return $Profile }
        if ($pattern -ceq $requiredPattern) { break }
        Write-Host ("  INPUT REJECTED: the resumable renderer requires " + $requiredPattern) -ForegroundColor Red
    }
    $subdirectory = Read-WzhkTextInput -Prompt "Output frames subdirectory (single safe name)" -Default ([string](Get-WzhkBuilderPropertyValue $Profile "output.framesSubdirectory" "frames")) -MinimumLength 1 -MaximumLength 64 -Required -AllowCancel
    if ($null -eq $subdirectory) { return $Profile }
    $subdirectory = ConvertTo-WzhkProfileSlug -Name $subdirectory
    $displayBaked = ($format -eq "PNG")
    $updated = Set-WzhkBuilderValues -Profile $Profile -Values ([ordered]@{
        "imageSequence.format" = $format
        "imageSequence.extension" = $extension
        "imageSequence.bitDepth" = $bitDepth
        "imageSequence.colorMode" = "RGB"
        "imageSequence.compression" = $compression
        "imageSequence.filenamePattern" = $requiredPattern
        "imageSequence.colorManagement.displayTransformBaked" = $displayBaked
        "imageSequence.colorManagement.encodedColorSpace" = $(if ($displayBaked) { "sRGB" } else { "scene-linear" })
        "output.framesSubdirectory" = $subdirectory
    })
    if ($format -eq "OPEN_EXR") {
        $updated = Set-WzhkBuilderValues -Profile $updated -Values ([ordered]@{
            "encoding.master.enabled" = $false
            "encoding.delivery.enabled" = $false
            "imageSequence.encodingWarning" = "Encoding disabled: approved linearToDeliveryFilter values are required for OpenEXR."
        })
    }
    return $updated
}

function Edit-WzhkBuilderColorManagement {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $displayItems = @(
        (New-WzhkBuilderChoiceItem "sRGB" "Approved scene display device." "sRGB"),
        (New-WzhkBuilderChoiceItem "DISPLAY P3" "Wide-gamut display device; review before authorization." "Display P3"),
        (New-WzhkBuilderChoiceItem "REC.1886" "Broadcast display device; review before authorization." "Rec.1886")
    )
    $displayChoice = Read-WzhkChoice -Title "PROFILE BUILDER // DISPLAY DEVICE" -Items $displayItems -ReturnItem
    if ($null -eq $displayChoice) { return $Profile }
    $viewItems = @(
        (New-WzhkBuilderChoiceItem "AgX" "Approved cinematic view transform." "AgX"),
        (New-WzhkBuilderChoiceItem "STANDARD" "Blender 5.2 standard view transform." "Standard")
    )
    $viewChoice = Read-WzhkChoice -Title "PROFILE BUILDER // VIEW TRANSFORM" -Items $viewItems -ReturnItem
    if ($null -eq $viewChoice) { return $Profile }
    $lookItems = if ($viewChoice.Value -eq "AgX") {
        @(
            (New-WzhkBuilderChoiceItem "AgX — MEDIUM HIGH CONTRAST" "Approved Space Journey look." "AgX - Medium High Contrast"),
            (New-WzhkBuilderChoiceItem "AgX — MEDIUM LOW CONTRAST" "Softer AgX contrast." "AgX - Medium Low Contrast"),
            (New-WzhkBuilderChoiceItem "NONE" "No additional AgX look transform." "None")
        )
    }
    else {
        @(
            (New-WzhkBuilderChoiceItem "MEDIUM HIGH CONTRAST" "Supported Standard-view contrast look." "Medium High Contrast"),
            (New-WzhkBuilderChoiceItem "NONE" "No additional Standard-view look transform." "None")
        )
    }
    $lookChoice = Read-WzhkChoice -Title "PROFILE BUILDER // COLOR LOOK" -Items $lookItems -ReturnItem
    if ($null -eq $lookChoice) { return $Profile }
    $exposure = Read-WzhkDecimalInput -Prompt "Exposure" -Default ([double](Get-WzhkBuilderPropertyValue $Profile "colorManagement.exposure" 0.0)) -Minimum -20.0 -Maximum 20.0 -AllowCancel
    if ($null -eq $exposure) { return $Profile }
    $gamma = Read-WzhkDecimalInput -Prompt "Gamma" -Default ([double](Get-WzhkBuilderPropertyValue $Profile "colorManagement.gamma" 1.0)) -Minimum 0.01 -Maximum 10.0 -AllowCancel
    if ($null -eq $gamma) { return $Profile }
    $sequencerItems = @(
        (New-WzhkBuilderChoiceItem "sRGB" "Approved sequencer color space." "sRGB"),
        (New-WzhkBuilderChoiceItem "LINEAR REC.709" "Blender 5.2 scene-linear Rec.709 sequencer behavior." "Linear Rec.709")
    )
    $sequencerChoice = Read-WzhkChoice -Title "PROFILE BUILDER // SEQUENCER COLOR SPACE" -Items $sequencerItems -ReturnItem
    if ($null -eq $sequencerChoice) { return $Profile }

    if ($displayChoice.Value -ne "sRGB" -or $viewChoice.Value -ne "AgX" -or $lookChoice.Value -ne "AgX - Medium High Contrast" -or [double]$exposure -ne 0.0 -or [double]$gamma -ne 1.0) {
        Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // COLOR WARNING"
        Write-WzhkFrameLine -Text "  These values differ from the approved AgX scene defaults." -Color Yellow
        if (-not (Read-WzhkYesNo -Prompt "Keep the explicit color override?" -YesText "KEEP COLOR" -NoText "FIX COLOR")) { return $Profile }
    }

    return Set-WzhkBuilderValues -Profile $Profile -Values ([ordered]@{
        "colorManagement.displayDevice" = [string]$displayChoice.Value
        "colorManagement.viewTransform" = [string]$viewChoice.Value
        "colorManagement.look" = [string]$lookChoice.Value
        "colorManagement.exposure" = [double]$exposure
        "colorManagement.gamma" = [double]$gamma
        "colorManagement.sequencerColorSpace" = [string]$sequencerChoice.Value
    })
}

function Edit-WzhkBuilderProduction {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $frameCount = [int](Get-WzhkBuilderPropertyValue $Profile "timeline.frameCount" 1)
    $templateId = [string](Get-WzhkBuilderPropertyValue $Profile "templateId" "1440P-BALANCED")
    $automatic = 150
    try { $automatic = [int](Get-WzhkRenderProfileTemplate -Id $templateId).FramesPerChunk } catch { $automatic = 150 }
    $automatic = [Math]::Min($frameCount, [Math]::Max(1, $automatic))
    $chunkItems = @(
        (New-WzhkBuilderChoiceItem "AUTOMATIC" ([string]::Format("Template-resolved value: {0} frames.", $automatic)) $automatic),
        (New-WzhkBuilderChoiceItem "50 FRAMES" "Small resumable chunks." 50 ($frameCount -ge 50)),
        (New-WzhkBuilderChoiceItem "100 FRAMES" "Bounded resumable chunks." 100 ($frameCount -ge 100)),
        (New-WzhkBuilderChoiceItem "150 FRAMES" "Recommended production default." 150 ($frameCount -ge 150)),
        (New-WzhkBuilderChoiceItem "200 FRAMES" "Larger resumable chunks." 200 ($frameCount -ge 200)),
        (New-WzhkBuilderChoiceItem "300 FRAMES" "Large resumable chunks." 300 ($frameCount -ge 300)),
        (New-WzhkBuilderChoiceItem "CUSTOM" "Enter 1–1200 frames, bounded by the selected timeline." "CUSTOM")
    )
    $chunkChoice = Read-WzhkChoice -Title "PROFILE BUILDER // CHUNK SIZE" -Items $chunkItems -ReturnItem
    if ($null -eq $chunkChoice) { return $Profile }
    $chunkSize = if ($chunkChoice.Value -eq "CUSTOM") {
        Read-WzhkIntegerInput -Prompt "Frames per chunk" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "production.framesPerChunk" $automatic)) -Minimum 1 -Maximum ([Math]::Min(1200, $frameCount)) -AllowCancel
    }
    else { [int]$chunkChoice.Value }
    if ($null -eq $chunkSize) { return $Profile }

    $overwriteInvalid = Read-WzhkBuilderBoolean -Title "PROFILE BUILDER // QUARANTINE AND RE-RENDER INVALID FRAMES" -Current ([bool](Get-WzhkBuilderPropertyValue $Profile "production.overwriteInvalidFrames" $true))
    if ($null -eq $overwriteInvalid) { return $Profile }

    Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // VALID FRAME SAFETY"
    Write-WzhkFrameLine -Text "  Valid completed frames are immutable in this production workflow." -Color Green
    Write-WzhkFrameLine -Text "  Enabled mode moves invalid canonical frames to recoverable checkpoints before re-render." -Color Green
    Write-WzhkFrameLine -Text "  Resume, verification, atomic publication, and stop-on-failure remain mandatory." -Color Green
    if (-not (Read-WzhkYesNo -Prompt "Keep mandatory never-overwrite-valid safety?" -YesText "KEEP SAFETY" -NoText "CANCEL EDIT")) { return $Profile }

    return Set-WzhkBuilderValues -Profile $Profile -Values ([ordered]@{
        "production.framesPerChunk" = [int]$chunkSize
        "production.chunkSize" = [int]$chunkSize
        "chunking.framesPerChunk" = [int]$chunkSize
        "production.resumeEnabled" = $true
        "production.resumeMissingFrames" = $true
        "production.resumePolicy" = "validated-missing-frames-only"
        "production.overwriteInvalidFrames" = [bool]$overwriteInvalid.Value
        "production.overwriteValidFrames" = $false
        "production.overwriteExistingFrames" = $false
        "production.atomicChunkCommit" = $true
        "production.atomicPublication" = $true
        "production.verifyExistingFrames" = $true
        "production.verifyEachChunk" = $true
        "production.stopOnValidationFailure" = $true
        "production.maximumFramesPerChunk" = 1200
    })
}

function Get-WzhkBuilderFreeDiskGiB {
    param([string]$Path)
    try {
        $fullPath = [IO.Path]::GetFullPath($Path)
        $root = [IO.Path]::GetPathRoot($fullPath)
        $drive = New-Object IO.DriveInfo($root)
        return [Math]::Round($drive.AvailableFreeSpace / 1GB, 2)
    }
    catch { return $null }
}

function Edit-WzhkBuilderOutput {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $policyItems = @(
        (New-WzhkBuilderChoiceItem "CREATE NEW OUTPUT" "Create an isolated output directory from the profile pattern." "create-new"),
        (New-WzhkBuilderChoiceItem "SELECT EXISTING COMPATIBLE OUTPUT" "Use only after exact manifest compatibility validation." "select-compatible"),
        (New-WzhkBuilderChoiceItem "RESUME MATCHING OUTPUT" "Resume only matching scene/profile/resolution/FPS/format/range." "resume-compatible")
    )
    $policyChoice = Read-WzhkChoice -Title "PROFILE BUILDER // OUTPUT POLICY" -Items $policyItems -ReturnItem
    if ($null -eq $policyChoice) { return $Profile }
    $defaultRoot = [string](Get-WzhkBuilderPropertyValue $Profile "output.rootDirectory" (Join-Path $script:BuilderRepositoryRoot "final-output"))
    $outputRoot = Read-WzhkTextInput -Prompt "Output root or compatible existing output path" -Default $defaultRoot -MinimumLength 1 -MaximumLength 240 -Required -AllowCancel
    if ($null -eq $outputRoot) { return $Profile }
    try { $outputRoot = [IO.Path]::GetFullPath($outputRoot) } catch {
        Show-WzhkMessage -Title "INVALID OUTPUT PATH" -Lines @($_.Exception.Message) -Color Red
        return $Profile
    }
    $pathRoot = [IO.Path]::GetPathRoot($outputRoot)
    $relativeOutput = $outputRoot.Substring($pathRoot.Length).Trim('\')
    $pathParts = @($relativeOutput -split '\\' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($outputRoot.TrimEnd('\') -ieq $pathRoot.TrimEnd('\') -or $pathParts.Count -lt 2) {
        Show-WzhkMessage -Title "UNSAFE OUTPUT PATH" -Lines @(
            "The production output path is too broad.",
            "Choose an isolated directory at least two levels below the drive root."
        ) -Color Red
        return $Profile
    }
    $directoryPattern = $null
    while ($true) {
        $directoryPattern = Read-WzhkTextInput -Prompt "New output directory pattern" -Default ([string](Get-WzhkBuilderPropertyValue $Profile "output.directoryPattern" "{project}-{preset}-{resolution}-{timestamp}")) -MinimumLength 1 -MaximumLength 120 -Required -AllowCancel
        if ($null -eq $directoryPattern) { return $Profile }
        $patternValidation = Test-WzhkOutputDirectoryPattern -Pattern $directoryPattern
        if ($patternValidation.Valid) { break }
        Write-Host ("  INPUT REJECTED: " + $patternValidation.Message) -ForegroundColor Red
    }
    $freeGiB = Get-WzhkBuilderFreeDiskGiB -Path $outputRoot
    $requiredGiB = Get-WzhkBuilderPropertyValue $Profile "storage.minimumLaunchFreeGiB" "pending"
    $existingFrames = 0
    $framesSubdirectory = [string](Get-WzhkBuilderPropertyValue $Profile "output.framesSubdirectory" "frames")
    $existingFramesRoot = Join-Path $outputRoot $framesSubdirectory
    if (Test-Path -LiteralPath $outputRoot -PathType Container) {
        $existingFrames = @(Get-ChildItem -LiteralPath $existingFramesRoot -File -Filter "frame_*.png" -ErrorAction SilentlyContinue).Count
        $existingFrames += @(Get-ChildItem -LiteralPath $existingFramesRoot -File -Filter "frame_*.exr" -ErrorAction SilentlyContinue).Count
    }
    $compatibilityState = "NEW OUTPUT ROOT — exact compatibility is initialized from the saved profile at render time"
    if ($policyChoice.Value -ne "create-new") {
        $manifestPath = Join-Path $outputRoot "manifests\render-manifest.json"
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            Show-WzhkMessage -Title "OUTPUT NOT MANAGED" -Lines @(
                "Existing/resume selection requires a render manifest.",
                $manifestPath
            ) -Color Red
            return $Profile
        }
        try {
            $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -ErrorAction Stop
            $contract = $manifest.frameContract
            $mismatches = New-Object System.Collections.Generic.List[string]
            if ([string]$manifest.scene.sha256 -ne [string](Get-WzhkBuilderPropertyValue $Profile "approvedSceneSha256" "")) { $mismatches.Add("scene SHA-256") }
            foreach ($check in @(
                @("width", (Get-WzhkBuilderPropertyValue $Profile "resolution.width" 0)),
                @("height", (Get-WzhkBuilderPropertyValue $Profile "resolution.height" 0)),
                @("fps", (Get-WzhkBuilderPropertyValue $Profile "timeline.fps" 0)),
                @("frameStart", (Get-WzhkBuilderPropertyValue $Profile "timeline.frameStart" 0)),
                @("frameEnd", (Get-WzhkBuilderPropertyValue $Profile "timeline.frameEnd" 0)),
                @("format", (Get-WzhkBuilderPropertyValue $Profile "imageSequence.format" "")),
                @("bitDepth", (Get-WzhkBuilderPropertyValue $Profile "imageSequence.bitDepth" 0)),
                @("pixelAspectX", (Get-WzhkBuilderPropertyValue $Profile "resolution.pixelAspectX" 1.0)),
                @("pixelAspectY", (Get-WzhkBuilderPropertyValue $Profile "resolution.pixelAspectY" 1.0)),
                @("framesSubdirectory", $framesSubdirectory)
            )) {
                $property = $contract.PSObject.Properties[[string]$check[0]]
                if ($null -eq $property -or [string]$property.Value -cne [string]$check[1]) { $mismatches.Add([string]$check[0]) }
            }
            if ($mismatches.Count -gt 0) { throw ("Existing output contract differs: " + ($mismatches -join ", ")) }
            $compatibilityState = "CONTRACT PRECHECKED — exact saved-file profile SHA-256 is rechecked after save"
        }
        catch {
            Show-WzhkMessage -Title "OUTPUT CONTRACT REJECTED" -Lines @($_.Exception.Message) -Color Red
            return $Profile
        }
    }

    Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // OUTPUT SAFETY"
    Write-WzhkFrameLine -Text ("  Output: " + $outputRoot) -Color White
    Write-WzhkFrameLine -Text ([string]::Format("  Free disk: {0} GiB  •  estimated minimum: {1} GiB", $(if ($null -eq $freeGiB) { "unknown" } else { $freeGiB }), $requiredGiB)) -Color Cyan
    Write-WzhkFrameLine -Text ("  Existing sequence frames at selected path: " + $existingFrames) -Color Cyan
    Write-WzhkFrameLine -Text ("  Compatibility: " + $compatibilityState) -Color Green
    Write-WzhkFrameLine -Text "  Resume will reject scene/profile/resolution/FPS/format/range mismatches." -Color Green
    if (-not (Read-WzhkYesNo -Prompt "Keep this output policy and path?" -YesText "KEEP OUTPUT" -NoText "FIX OUTPUT")) { return $Profile }

    return Set-WzhkBuilderValues -Profile $Profile -Values ([ordered]@{
        "output.rootDirectory" = $outputRoot
        "output.policy" = [string]$policyChoice.Value
        "output.directoryPattern" = $directoryPattern
        "output.neverMixProfiles" = $true
        "output.compatibilityManifest" = "manifests/render-manifest.json"
        "output.compatibilityKeys" = @("sceneSha256", "profileSha256", "width", "height", "pixelAspectX", "pixelAspectY", "fps", "imageFormat", "frameStart", "frameEnd", "framesSubdirectory")
        "output.compatibilityStatus" = $compatibilityState
        "output.lastKnownFreeGiB" = $freeGiB
        "output.existingFrameCount" = $existingFrames
    })
}

function Edit-WzhkBuilderEncoding {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $format = [string](Get-WzhkBuilderPropertyValue $Profile "imageSequence.format" "PNG")
    $audioHash = [string](Get-WzhkBuilderPropertyValue $Profile "audio.sha256" "")
    $audioBound = ($audioHash -match '^[A-Fa-f0-9]{64}$' -and $audioHash -ne ("0" * 64))
    $frameStart = [int](Get-WzhkBuilderPropertyValue $Profile "timeline.frameStart" 1)
    $frameEnd = [int](Get-WzhkBuilderPropertyValue $Profile "timeline.frameEnd" 1)
    $fps = [double](Get-WzhkBuilderPropertyValue $Profile "timeline.fps" 30.0)
    $sourceStart = [int](Get-WzhkBuilderPropertyValue $Profile "sourceTimeline.frameStart" $frameStart)
    $sourceEnd = [int](Get-WzhkBuilderPropertyValue $Profile "sourceTimeline.frameEnd" $frameEnd)
    $sourceFps = [double](Get-WzhkBuilderPropertyValue $Profile "sourceTimeline.fps" $fps)
    if (-not $audioBound -or $frameStart -ne $sourceStart -or $frameEnd -ne $sourceEnd -or [Math]::Abs($fps - $sourceFps) -gt 0.0001) {
        Show-WzhkMessage -Title "ENCODING REQUIRES APPROVED FULL-TRACK AUDIO" -Lines @(
            "Optional master/delivery encoding is disabled for this profile.",
            "Encoding requires an exact approved audio SHA-256 and the full approved scene timeline/FPS.",
            "The resumable image sequence remains fully available."
        ) -Color Yellow
        return Set-WzhkBuilderValues -Profile $Profile -Values ([ordered]@{
            "encoding.master.enabled" = $false
            "encoding.delivery.enabled" = $false
        })
    }
    if ($format -eq "OPEN_EXR") {
        Show-WzhkMessage -Title "OPENEXR ENCODING DISABLED" -Lines @(
            "Scene-linear OpenEXR requires approved master and delivery linearToDeliveryFilter values.",
            "No approved filters are stored in this profile, so both encoding targets remain disabled.",
            "The OpenEXR image sequence remains the source of truth."
        ) -Color Yellow
        return Set-WzhkBuilderValues -Profile $Profile -Values ([ordered]@{
            "encoding.master.enabled" = $false
            "encoding.delivery.enabled" = $false
        })
    }

    $masterChoice = Read-WzhkBuilderBoolean -Title "PROFILE BUILDER // MASTER ENCODE" -Current ([bool](Get-WzhkBuilderPropertyValue $Profile "encoding.master.enabled" $true))
    if ($null -eq $masterChoice) { return $Profile }
    $masterCodec = "PRORES"
    if ($masterChoice.Value) {
        $masterItems = @(
            (New-WzhkBuilderChoiceItem "PRORES 422 HQ" "Visually lossless 10-bit MOV with PCM audio." "PRORES"),
            (New-WzhkBuilderChoiceItem "FFV1 LOSSLESS" "Matroska FFV1 lossless master with PCM audio." "FFV1")
        )
        $masterCodecChoice = Read-WzhkChoice -Title "PROFILE BUILDER // MASTER CODEC" -Items $masterItems -ReturnItem
        if ($null -eq $masterCodecChoice) { return $Profile }
        $masterCodec = [string]$masterCodecChoice.Value
    }
    $pcmItems = @(
        (New-WzhkBuilderChoiceItem "PCM 24-BIT" "pcm_s24le production master audio." "pcm_s24le"),
        (New-WzhkBuilderChoiceItem "PCM 16-BIT" "pcm_s16le compatible master audio." "pcm_s16le")
    )
    $pcmChoice = Read-WzhkChoice -Title "PROFILE BUILDER // MASTER AUDIO" -Items $pcmItems -ReturnItem
    if ($null -eq $pcmChoice) { return $Profile }

    $deliveryChoice = Read-WzhkBuilderBoolean -Title "PROFILE BUILDER // DELIVERY ENCODE" -Current ([bool](Get-WzhkBuilderPropertyValue $Profile "encoding.delivery.enabled" $true))
    if ($null -eq $deliveryChoice) { return $Profile }
    $deliveryCodec = "H264"
    $crf = [int](Get-WzhkBuilderPropertyValue $Profile "encoding.delivery.crf" 16)
    $preset = [string](Get-WzhkBuilderPropertyValue $Profile "encoding.delivery.preset" "slow")
    $pixelFormat = [string](Get-WzhkBuilderPropertyValue $Profile "encoding.delivery.pixelFormat" "yuv420p")
    $audioBitrate = [string](Get-WzhkBuilderPropertyValue $Profile "encoding.delivery.audioBitrate" "320k")
    $fastStart = [bool](Get-WzhkBuilderPropertyValue $Profile "encoding.delivery.fastStart" $true)
    $rec709 = [bool](Get-WzhkBuilderPropertyValue $Profile "encoding.delivery.requireRec709Metadata" $true)
    if ($deliveryChoice.Value) {
        $codecItems = @(
            (New-WzhkBuilderChoiceItem "H.264" "libx264 widely compatible delivery." "H264"),
            (New-WzhkBuilderChoiceItem "H.265 / HEVC" "libx265 delivery when the local FFmpeg build supports it." "H265")
        )
        $codecChoice = Read-WzhkChoice -Title "PROFILE BUILDER // DELIVERY CODEC" -Items $codecItems -ReturnItem
        if ($null -eq $codecChoice) { return $Profile }
        $deliveryCodec = [string]$codecChoice.Value
        $crfValue = Read-WzhkIntegerInput -Prompt "Delivery CRF (lower is higher quality)" -Default $crf -Minimum 0 -Maximum 30 -AllowCancel
        if ($null -eq $crfValue) { return $Profile }
        $crf = [int]$crfValue
        $presetItems = New-Object System.Collections.Generic.List[object]
        foreach ($name in @("medium", "slow", "slower", "veryslow")) { $presetItems.Add((New-WzhkBuilderChoiceItem $name.ToUpperInvariant() "FFmpeg encoder preset." $name)) }
        $presetChoice = Read-WzhkChoice -Title "PROFILE BUILDER // DELIVERY PRESET" -Items $presetItems.ToArray() -ReturnItem
        if ($null -eq $presetChoice) { return $Profile }
        $preset = [string]$presetChoice.Value
        $pixelFormat = if ($deliveryCodec -eq "H265") { "yuv420p10le" } else { "yuv420p" }
        Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // DELIVERY PIXEL CONTRACT"
        Write-WzhkFrameLine -Text ([string]::Format("  {0} resolves to {1}; unsupported codec/pixel-profile combinations are not offered.", $deliveryCodec, $pixelFormat)) -Color Green
        $audioItems = @(
            (New-WzhkBuilderChoiceItem "AAC 192K" "Compact high-quality stereo AAC." "192k"),
            (New-WzhkBuilderChoiceItem "AAC 256K" "High-quality stereo AAC." "256k"),
            (New-WzhkBuilderChoiceItem "AAC 320K" "Recommended maximum-quality stereo AAC." "320k")
        )
        $audioChoice = Read-WzhkChoice -Title "PROFILE BUILDER // AAC BITRATE" -Items $audioItems -ReturnItem
        if ($null -eq $audioChoice) { return $Profile }
        $audioBitrate = [string]$audioChoice.Value
        $fastStartChoice = Read-WzhkBuilderBoolean -Title "PROFILE BUILDER // FAST START" -Current $fastStart
        if ($null -eq $fastStartChoice) { return $Profile }
        $fastStart = [bool]$fastStartChoice.Value
        $rec709Choice = Read-WzhkBuilderBoolean -Title "PROFILE BUILDER // REC.709 METADATA" -Current $rec709
        if ($null -eq $rec709Choice) { return $Profile }
        $rec709 = [bool]$rec709Choice.Value
    }

    $values = [ordered]@{
        "encoding.master.enabled" = [bool]$masterChoice.Value
        "encoding.master.audioCodec" = [string]$pcmChoice.Value
        "encoding.master.expectedAudioCodec" = [string]$pcmChoice.Value
        "encoding.delivery.enabled" = [bool]$deliveryChoice.Value
        "encoding.delivery.crf" = $crf
        "encoding.delivery.preset" = $preset
        "encoding.delivery.pixelFormat" = $pixelFormat
        "encoding.delivery.audioBitrate" = $audioBitrate
        "encoding.delivery.fastStart" = $fastStart
        "encoding.delivery.requireRec709Metadata" = $rec709
    }
    if ($masterCodec -eq "FFV1") {
        $values["encoding.master.container"] = "matroska"
        $values["encoding.master.fileExtension"] = ".mkv"
        $values["encoding.master.videoCodec"] = "ffv1"
        $values["encoding.master.expectedVideoCodec"] = "ffv1"
        $values["encoding.master.profile"] = "3"
        $values["encoding.master.profileName"] = "FFV1 Level 3 Lossless"
        $values["encoding.master.pixelFormat"] = "yuv444p16le"
    }
    else {
        $values["encoding.master.container"] = "mov"
        $values["encoding.master.fileExtension"] = ".mov"
        $values["encoding.master.videoCodec"] = "prores_ks"
        $values["encoding.master.expectedVideoCodec"] = "prores"
        $values["encoding.master.profile"] = "3"
        $values["encoding.master.profileName"] = "ProRes 422 HQ"
        $values["encoding.master.pixelFormat"] = "yuv422p10le"
    }
    if ($deliveryCodec -eq "H265") {
        $values["encoding.delivery.videoCodec"] = "libx265"
        $values["encoding.delivery.expectedVideoCodec"] = "hevc"
        $values["encoding.delivery.profile"] = "main10"
        $values["encoding.delivery.profileName"] = "H.265 Main 10"
        $values["encoding.delivery.pixelFormat"] = "yuv420p10le"
    }
    else {
        $values["encoding.delivery.videoCodec"] = "libx264"
        $values["encoding.delivery.expectedVideoCodec"] = "h264"
        $values["encoding.delivery.profile"] = "high"
        $values["encoding.delivery.profileName"] = "H.264 High quality"
        $values["encoding.delivery.pixelFormat"] = "yuv420p"
    }
    return Set-WzhkBuilderValues -Profile $Profile -Values $values
}

function Edit-WzhkBuilderDashboard {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $names = @(
        @("enabled", "ENABLE VISUAL DASHBOARD", $true),
        @("autoLaunch", "AUTO-LAUNCH DASHBOARD", $true),
        @("showLatestFrame", "SHOW LATEST FRAME", $true),
        @("showInflightFrames", "SHOW IN-FLIGHT FRAMES", $true),
        @("showPublishedFrames", "SHOW PUBLISHED FRAMES", $true),
        @("showEta", "SHOW ETA", $true),
        @("showRollingSecondsPerFrame", "SHOW ROLLING SECONDS / FRAME", $true),
        @("showStorageGrowth", "SHOW STORAGE GROWTH", $true),
        @("openOutputWhenComplete", "OPEN OUTPUT WHEN COMPLETE", $true)
    )
    $values = [ordered]@{}
    foreach ($definition in $names) {
        $name = [string]$definition[0]
        $current = [bool](Get-WzhkBuilderPropertyValue $Profile ("dashboard." + $name) ([bool]$definition[2]))
        $choice = Read-WzhkBuilderBoolean -Title ("PROFILE BUILDER // " + [string]$definition[1]) -Current $current
        if ($null -eq $choice) { return $Profile }
        $values["dashboard." + $name] = [bool]$choice.Value
    }
    $refresh = Read-WzhkIntegerInput -Prompt "Dashboard refresh interval in seconds" -Default ([int](Get-WzhkBuilderPropertyValue $Profile "dashboard.refreshSeconds" 6)) -Minimum 1 -Maximum 300 -AllowCancel
    if ($null -eq $refresh) { return $Profile }
    $values["dashboard.refreshSeconds"] = [int]$refresh
    return Set-WzhkBuilderValues -Profile $Profile -Values $values
}

function Invoke-WzhkBuilderStageEdit {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][ValidateRange(1, 12)][int]$StageNumber,
        [Parameter(Mandatory = $true)][object[]]$Scenes,
        [AllowNull()][object]$SelectedScene
    )

    $updated = $Profile
    $scene = $SelectedScene
    switch ($StageNumber) {
        1 { $updated = Edit-WzhkBuilderIdentity -Profile $Profile }
        2 {
            $sceneResult = Edit-WzhkBuilderScene -Profile $Profile -Scenes $Scenes
            $updated = $sceneResult.Profile
            if ($null -ne $sceneResult.Scene) { $scene = $sceneResult.Scene }
        }
        3 { $updated = Edit-WzhkBuilderResolution -Profile $Profile }
        4 { $updated = Edit-WzhkBuilderTimeline -Profile $Profile -Scene $SelectedScene }
        5 { $updated = Edit-WzhkBuilderQuality -Profile $Profile }
        6 { $updated = Edit-WzhkBuilderRenderSettings -Profile $Profile }
        7 { $updated = Edit-WzhkBuilderImageSequence -Profile $Profile }
        8 { $updated = Edit-WzhkBuilderColorManagement -Profile $Profile }
        9 { $updated = Edit-WzhkBuilderProduction -Profile $Profile }
        10 { $updated = Edit-WzhkBuilderOutput -Profile $Profile }
        11 { $updated = Edit-WzhkBuilderEncoding -Profile $Profile }
        12 { $updated = Edit-WzhkBuilderDashboard -Profile $Profile }
    }
    return [pscustomobject]@{ Profile = (Update-WzhkBuilderDerivedValues -Profile $updated); Scene = $scene }
}

function Select-WzhkBuilderReviewSection {
    while ($true) {
        $firstPage = @(
            (New-WzhkBuilderChoiceItem "PROFILE IDENTITY" "Edit name, ID, description, project, and preset." 1),
            (New-WzhkBuilderChoiceItem "APPROVED SCENE" "Select and hash an approved frozen scene." 2),
            (New-WzhkBuilderChoiceItem "RESOLUTION" "Edit explicit dimensions and aspect." 3),
            (New-WzhkBuilderChoiceItem "FRAME RATE / RANGE" "Edit FPS and bounded timeline." 4),
            (New-WzhkBuilderChoiceItem "QUALITY" "Select an explicitly resolved quality preset." 5),
            (New-WzhkBuilderChoiceItem "RENDER SETTINGS" "Edit supported Blender 5.2 EEVEE settings." 6),
            (New-WzhkBuilderChoiceItem "IMAGE SEQUENCE" "Edit resumable frame format and naming." 7),
            (New-WzhkBuilderChoiceItem "COLOR MANAGEMENT" "Edit view, look, exposure, and gamma." 8),
            (New-WzhkBuilderChoiceItem "MORE SECTIONS →" "Chunking, output, encoding, and dashboard." "MORE")
        )
        $choice = Read-WzhkChoice -Title "PROFILE BUILDER // FIX SETTINGS // PAGE 1" -Items $firstPage -ReturnItem
        if ($null -eq $choice) { return $null }
        if ($choice.Value -ne "MORE") { return [int]$choice.Value }

        $secondPage = @(
            (New-WzhkBuilderChoiceItem "CHUNK / RESUME" "Edit chunks and immutable-frame safeguards." 9),
            (New-WzhkBuilderChoiceItem "OUTPUT" "Edit output root and compatibility policy." 10),
            (New-WzhkBuilderChoiceItem "ENCODING" "Edit optional master and delivery encodes." 11),
            (New-WzhkBuilderChoiceItem "PROGRESS DASHBOARD" "Edit local visual progress preferences." 12),
            (New-WzhkBuilderChoiceItem "← PREVIOUS PAGE" "Return to sections 1–8." "BACK")
        )
        $choice = Read-WzhkChoice -Title "PROFILE BUILDER // FIX SETTINGS // PAGE 2" -Items $secondPage -ReturnItem
        if ($null -eq $choice) { return $null }
        if ($choice.Value -ne "BACK") { return [int]$choice.Value }
    }
}

function Test-WzhkBuilderDuplicateProfileId {
    param(
        [Parameter(Mandatory = $true)][string]$ProfileDirectory,
        [Parameter(Mandatory = $true)][string]$ProfileId,
        [Parameter(Mandatory = $true)][string]$TargetPath
    )

    if (-not (Test-Path -LiteralPath $ProfileDirectory -PathType Container)) { return @() }
    $duplicates = New-Object System.Collections.Generic.List[string]
    foreach ($file in Get-ChildItem -LiteralPath $ProfileDirectory -Recurse -File -Filter "*.json" -ErrorAction SilentlyContinue) {
        if ($file.Name -match '\.authorization(-request)?\.json$' -or $file.FullName -eq $TargetPath) { continue }
        try {
            $candidate = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            $candidateId = [string](Get-WzhkBuilderPropertyValue $candidate "profileId" "")
            if ($candidateId -ceq $ProfileId) { $duplicates.Add($file.FullName) }
        }
        catch { continue }
    }
    return $duplicates.ToArray()
}

function Save-WzhkBuilderProfileInteractive {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][string]$ProfileDirectory,
        [string]$InitialProfilePath = "",
        [string]$SuggestedSavePath = ""
    )

    $projectSlug = ConvertTo-WzhkProfileSlug -Name ([string](Get-WzhkBuilderPropertyValue $Profile "project" "trackprompt"))
    $profileSlug = ConvertTo-WzhkProfileSlug -Name ([string](Get-WzhkBuilderPropertyValue $Profile "profileId" "render-profile"))
    $saveMode = "New"
    if (-not [string]::IsNullOrWhiteSpace($InitialProfilePath)) {
        $saveModeChoice = Read-WzhkChoice -Title "PROFILE BUILDER // SAVE MODE" -Items @(
            (New-WzhkBuilderChoiceItem "SAVE" "Atomically replace the currently loaded saved profile." "Existing"),
            (New-WzhkBuilderChoiceItem "SAVE AS NEW" "Choose a new path and create new stable/profile IDs with pending authorization." "SaveAs"),
            (New-WzhkBuilderChoiceItem "CANCEL" "Return to the review without writing a profile." "Cancel")
        ) -ReturnItem
        if ($null -eq $saveModeChoice -or $saveModeChoice.Value -eq "Cancel") { return [pscustomobject]@{ Saved = $false; Result = $null } }
        $saveMode = [string]$saveModeChoice.Value
    }

    $defaultPath = if ($saveMode -eq "Existing") {
        $InitialProfilePath
    }
    elseif (-not [string]::IsNullOrWhiteSpace($SuggestedSavePath)) {
        $SuggestedSavePath
    }
    elseif ($saveMode -eq "SaveAs") {
        Join-Path (Join-Path $ProfileDirectory $projectSlug) ($profileSlug + "-copy.json")
    }
    else {
        Join-Path (Join-Path $ProfileDirectory $projectSlug) ($profileSlug + ".json")
    }
    try { $defaultPath = [IO.Path]::GetFullPath($defaultPath) } catch { $defaultPath = Join-Path $ProfileDirectory ($profileSlug + ".json") }

    $pathText = if ($saveMode -eq "Existing") {
        $defaultPath
    }
    else {
        Read-WzhkTextInput -Prompt "Profile JSON save path" -Default $defaultPath -MinimumLength 1 -MaximumLength 240 -Required -AllowCancel
    }
    if ($null -eq $pathText) { return [pscustomobject]@{ Saved = $false; Result = $null } }
    try {
        $targetPath = [IO.Path]::GetFullPath($pathText)
        if ([IO.Path]::GetExtension($targetPath).ToLowerInvariant() -ne ".json") { $targetPath += ".json" }
    }
    catch {
        Show-WzhkMessage -Title "INVALID PROFILE PATH" -Lines @($_.Exception.Message) -Color Red
        return [pscustomobject]@{ Saved = $false; Result = $null }
    }
    if ($saveMode -eq "SaveAs" -and $targetPath -ieq [IO.Path]::GetFullPath($InitialProfilePath)) {
        Show-WzhkMessage -Title "SAVE AS NEW REQUIRES A NEW PATH" -Lines @(
            "Choose SAVE to replace the loaded profile.",
            "SAVE AS NEW must preserve the existing file and create a distinct path."
        ) -Color Red
        return [pscustomobject]@{ Saved = $false; Result = $null }
    }

    $profileRoot = [IO.Path]::GetFullPath($ProfileDirectory).TrimEnd('\') + '\'
    if (-not $targetPath.StartsWith($profileRoot, [StringComparison]::OrdinalIgnoreCase) -and
        ([string]::IsNullOrWhiteSpace($InitialProfilePath) -or $targetPath -ne [IO.Path]::GetFullPath($InitialProfilePath))) {
        Show-WzhkMessage -Title "PROFILE PATH OUTSIDE STORE" -Lines @(
            "Profiles must be saved under the dedicated local profile store:",
            $ProfileDirectory
        ) -Color Red
        return [pscustomobject]@{ Saved = $false; Result = $null }
    }

    $exists = Test-Path -LiteralPath $targetPath -PathType Leaf
    if ($exists) {
        Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // OVERWRITE CONFIRMATION"
        Write-WzhkFrameLine -Text ("  Existing profile: " + $targetPath) -Color Yellow
        Write-WzhkFrameLine -Text "  Replacement will be atomic and prior authorization will be invalidated if content changed." -Color Yellow
        if (-not (Read-WzhkYesNo -Prompt "Overwrite this exact saved profile?" -YesText "OVERWRITE ATOMICALLY" -NoText "CHOOSE ANOTHER PATH")) {
            return [pscustomobject]@{ Saved = $false; Result = $null }
        }
    }

    $profileToSave = $Profile
    $isSaveAsNew = ($saveMode -eq "SaveAs")
    if ($isSaveAsNew) {
        $requestedProfileId = [string](Get-WzhkBuilderPropertyValue $Profile "profileId" "")
        $initialProfileId = ""
        try {
            $initialSavedProfile = Import-WzhkRenderProfile -Path $InitialProfilePath
            $initialProfileId = [string](Get-WzhkBuilderPropertyValue $initialSavedProfile "profileId" "")
        }
        catch { $initialProfileId = "" }
        if ($requestedProfileId -ieq $initialProfileId) {
            $requestedProfileId = ConvertTo-WzhkProfileSlug -Name ($requestedProfileId + "-copy")
        }
        $profileToSave = Copy-WzhkRenderProfile `
            -Profile $Profile `
            -NewDisplayName ([string](Get-WzhkBuilderPropertyValue $Profile "displayName" "Render Profile Copy"))
        $profileToSave = Set-WzhkProfileValue -Profile $profileToSave -PropertyPath "profileId" -Value $requestedProfileId.ToUpperInvariant()
        $profileToSave = Set-WzhkProfileValue -Profile $profileToSave -PropertyPath "authorization.profile" -Value $requestedProfileId.ToUpperInvariant()
    }

    $profileId = [string](Get-WzhkBuilderPropertyValue $profileToSave "profileId" "")
    $duplicates = @(Test-WzhkBuilderDuplicateProfileId -ProfileDirectory $ProfileDirectory -ProfileId $profileId -TargetPath $targetPath)
    if ($duplicates.Count -gt 0) {
        Show-WzhkMessage -Title "DUPLICATE PROFILE ID" -Lines @(
            "Profile ID already belongs to another saved profile: $profileId",
            $duplicates[0],
            "Return to PROFILE IDENTITY and choose a unique ID."
        ) -Color Red
        return [pscustomobject]@{ Saved = $false; Result = $null }
    }

    Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // FINAL SAVE CONFIRMATION"
    Write-WzhkFrameLine -Text ("  PROFILE : " + [string](Get-WzhkBuilderPropertyValue $profileToSave "displayName" "Render Profile")) -Color White
    Write-WzhkFrameLine -Text ("  ID      : " + $profileId) -Color White
    Write-WzhkFrameLine -Text ("  PATH    : " + $targetPath) -Color White
    Write-WzhkFrameLine -Text "  Saving does not authorize, render, or encode." -Color Green
    if (-not (Read-WzhkYesNo -Prompt "Save this exact normalized profile now?" -YesText "SAVE PROFILE" -NoText "FIX SETTINGS")) {
        return [pscustomobject]@{ Saved = $false; Result = $null }
    }

    try {
        $result = Save-WzhkRenderProfile -Profile $profileToSave -Path $targetPath -Force:$exists
        Show-WzhkDoneAnimation -Title "PROFILE SAVED" -Details @(
            "Saved profile: $($result.Path)",
            "Summary: $($result.SummaryPath)",
            "Canonical content SHA-12: $(Get-WzhkShortHash $result.ContentSha256)",
            "Saved-file SHA-12: $(Get-WzhkShortHash $result.FileSha256)",
            "Authorization remains pending until a separate two-confirmation workflow completes."
        )
        return [pscustomobject]@{ Saved = $true; Result = $result }
    }
    catch {
        Show-WzhkMessage -Title "PROFILE SAVE FAILED" -Lines @(
            $_.Exception.Message,
            "No production render or authorization action was attempted."
        ) -Color Red
        return [pscustomobject]@{ Saved = $false; Result = $null }
    }
}

function New-WzhkBuilderSceneFromProfile {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $path = [string](Get-WzhkBuilderFirstValue $Profile @("approvedScene.path", "approvedScenePath") "")
    if ([string]::IsNullOrWhiteSpace($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $candidate = [pscustomobject]@{
        Path = $path
        ManifestPath = [string](Get-WzhkBuilderFirstValue $Profile @("approvedScene.manifestPath", "sceneManifestPath") "")
        BlenderVersion = Get-WzhkBuilderFirstValue $Profile @("approvedScene.blenderVersion", "blenderVersion") "5.2.0 LTS"
        ObjectCount = Get-WzhkBuilderPropertyValue $Profile "approvedScene.objectCount" "unknown"
        MaterialCount = Get-WzhkBuilderPropertyValue $Profile "approvedScene.materialCount" "unknown"
        CollectionCount = Get-WzhkBuilderPropertyValue $Profile "approvedScene.collectionCount" "unknown"
        FCurveCount = Get-WzhkBuilderPropertyValue $Profile "approvedScene.fCurveCount" "unknown"
        MacroStateCount = Get-WzhkBuilderPropertyValue $Profile "approvedScene.macroStateCount" "unknown"
        AudioBusCurveCount = Get-WzhkBuilderPropertyValue $Profile "approvedScene.audioBusCurveCount" "unknown"
        ActiveCamera = Get-WzhkBuilderPropertyValue $Profile "approvedScene.activeCamera" "unknown"
        Preset = Get-WzhkBuilderFirstValue $Profile @("approvedScene.preset", "preset") "space-journey"
        FrameStart = Get-WzhkBuilderFirstValue $Profile @("sourceTimeline.frameStart", "timeline.frameStart") 1
        FrameEnd = Get-WzhkBuilderFirstValue $Profile @("sourceTimeline.frameEnd", "timeline.frameEnd") 13029
        Fps = Get-WzhkBuilderFirstValue $Profile @("sourceTimeline.fps", "timeline.fps") 30.0
        Width = Get-WzhkBuilderPropertyValue $Profile "resolution.width" $null
        Height = Get-WzhkBuilderPropertyValue $Profile "resolution.height" $null
    }
    return Get-WzhkNormalizedSceneCandidate -Candidate $candidate
}

function Invoke-WzhkProfileBuilder {
    [CmdletBinding()]
    param(
        [object[]]$SceneCandidates = @(),
        [AllowNull()][object]$InitialProfile = $null,
        [AllowNull()][object]$BaseProfile = $null,
        [string]$InitialProfilePath = "",
        [string]$ProfileDirectory = "",
        [string]$SuggestedSavePath = "",
        [string]$TemplateId = "",
        [string]$DefaultProject = "trip-to-andromeda",
        [string]$DefaultPreset = "space-journey"
    )

    if ([string]::IsNullOrWhiteSpace($ProfileDirectory)) { $ProfileDirectory = Join-Path $script:BuilderRepositoryRoot "render-profiles" }
    $ProfileDirectory = [IO.Path]::GetFullPath($ProfileDirectory)

    if ($null -eq $InitialProfile -and -not [string]::IsNullOrWhiteSpace($InitialProfilePath)) {
        try { $InitialProfile = Import-WzhkRenderProfile -Path $InitialProfilePath -Normalize }
        catch {
            Show-WzhkMessage -Title "PROFILE LOAD FAILED" -Lines @($_.Exception.Message) -Color Red
            return $null
        }
    }

    $scenes = New-Object System.Collections.Generic.List[object]
    foreach ($candidate in $SceneCandidates) {
        $normalizedScene = Get-WzhkNormalizedSceneCandidate -Candidate $candidate
        if ($null -ne $normalizedScene) { $scenes.Add($normalizedScene) }
    }
    $profile = $null
    $selectedScene = $null

    if ($null -ne $InitialProfile) {
        try { $profile = Normalize-WzhkRenderProfile -Profile $InitialProfile }
        catch {
            Show-WzhkMessage -Title "PROFILE NORMALIZATION FAILED" -Lines @($_.Exception.Message) -Color Red
            return $null
        }
        $currentScenePath = [string](Get-WzhkBuilderFirstValue $profile @("approvedScene.path", "approvedScenePath") "")
        $selectedScene = @($scenes | Where-Object { $_.Path -eq $currentScenePath } | Select-Object -First 1)
        if ($selectedScene.Count -gt 0) { $selectedScene = $selectedScene[0] } else { $selectedScene = New-WzhkBuilderSceneFromProfile -Profile $profile }
        if ($null -ne $selectedScene -and @($scenes | Where-Object { $_.Path -eq $selectedScene.Path }).Count -eq 0) { $scenes.Insert(0, $selectedScene) }
        if ($null -ne $selectedScene -and $null -eq (Get-WzhkBuilderPropertyValue $profile "sourceTimeline" $null)) {
            $profile = Set-WzhkBuilderScene -Profile $profile -Scene $selectedScene
        }
        if ([string]::IsNullOrWhiteSpace($TemplateId)) { $TemplateId = [string](Get-WzhkBuilderPropertyValue $profile "templateId" "1440P-BALANCED") }
    }
    else {
        if ($scenes.Count -eq 0) {
            Show-WzhkMessage -Title "NO VERIFIED APPROVED SCENE" -Lines @(
                "Create-profile mode requires at least one existing approved .blend candidate.",
                "No profile, authorization, or render output was created."
            ) -Color Red
            return $null
        }
        if ([string]::IsNullOrWhiteSpace($TemplateId)) {
            $template = Show-WzhkTemplateMenu -Templates @(Get-WzhkRenderProfileTemplates)
            if ($null -eq $template) { return $null }
            $TemplateId = [string]$template.Id
        }
        $selectedScene = $scenes[0]
        $profile = New-WzhkRenderProfile `
            -TemplateId $TemplateId `
            -DisplayName ([string](Get-WzhkRenderProfileTemplate -Id $TemplateId).DisplayName) `
            -Project $DefaultProject `
            -Preset $(if ([string]::IsNullOrWhiteSpace([string]$selectedScene.Preset)) { $DefaultPreset } else { [string]$selectedScene.Preset }) `
            -ApprovedScenePath $selectedScene.Path `
            -ApprovedSceneSha256 $selectedScene.Sha256 `
            -SceneManifestPath $selectedScene.ManifestPath `
            -SceneManifestSha256 $selectedScene.ManifestSha256 `
            -FrameStart ([int]$selectedScene.FrameStart) `
            -FrameEnd ([int]$selectedScene.FrameEnd) `
            -Fps ([double]$selectedScene.Fps) `
            -BlenderVersion ([string]$selectedScene.BlenderVersion) `
            -BaseProfile $BaseProfile
        $profile = Set-WzhkBuilderScene -Profile $profile -Scene $selectedScene
    }

    $profile = Update-WzhkBuilderDerivedValues -Profile $profile
    $recommendedProfile = New-WzhkRenderProfile `
        -TemplateId $TemplateId `
        -DisplayName ([string](Get-WzhkBuilderPropertyValue $profile "displayName" "Render Profile")) `
        -ProfileId ([string](Get-WzhkBuilderPropertyValue $profile "profileId" "")) `
        -Project ([string](Get-WzhkBuilderPropertyValue $profile "project" $DefaultProject)) `
        -Preset ([string](Get-WzhkBuilderPropertyValue $profile "preset" $DefaultPreset)) `
        -ApprovedScenePath $(if ($null -eq $selectedScene) { "" } else { $selectedScene.Path }) `
        -ApprovedSceneSha256 $(if ($null -eq $selectedScene) { "" } else { $selectedScene.Sha256 }) `
        -SceneManifestPath $(if ($null -eq $selectedScene) { "" } else { $selectedScene.ManifestPath }) `
        -SceneManifestSha256 $(if ($null -eq $selectedScene) { "" } else { $selectedScene.ManifestSha256 }) `
        -FrameStart $(if ($null -eq $selectedScene) { [int](Get-WzhkBuilderPropertyValue $profile "timeline.frameStart" 1) } else { [int]$selectedScene.FrameStart }) `
        -FrameEnd $(if ($null -eq $selectedScene) { [int](Get-WzhkBuilderPropertyValue $profile "timeline.frameEnd" 13029) } else { [int]$selectedScene.FrameEnd }) `
        -Fps $(if ($null -eq $selectedScene) { [double](Get-WzhkBuilderPropertyValue $profile "timeline.fps" 30.0) } else { [double]$selectedScene.Fps }) `
        -BaseProfile $profile
    $recommendedProfile = Update-WzhkBuilderDerivedValues -Profile $recommendedProfile

    $stageNumber = 1
    $stages = @(Get-WzhkProfileBuilderStages)
    while ($true) {
        $stage = $stages[$stageNumber - 1]
        $profile = Update-WzhkBuilderDerivedValues -Profile $profile

        if ($stageNumber -eq 13) {
            $validation = Test-WzhkRenderProfile -Profile $profile
            $safetyLines = New-Object System.Collections.Generic.List[string]
            $safetyLines.Add("Resume uses validated missing frames only; valid completed frames are never overwritten.")
            $safetyLines.Add("Atomic publication and exact output compatibility remain enforced by the renderer.")
            $safetyLines.Add("Authorization is separate and remains pending after save.")
            foreach ($warning in @($validation.Warnings)) { $safetyLines.Add("WARNING: " + [string]$warning) }
            foreach ($error in @($validation.Errors)) { $safetyLines.Add("ERROR: " + [string]$error) }
            Show-WzhkProfileSummary -Profile $profile -Title "PROFILE BUILDER // STEP 13 OF 13 // REVIEW" -SafetyLines $safetyLines.ToArray()
            if (-not $validation.Valid) {
                Write-WzhkFrameDivider
                Write-WzhkFrameLine -Text "  [N] FIX SETTINGS is required because this profile is invalid." -Color Red
                Write-WzhkFrameBottom
                $null = [Console]::ReadKey($true)
                $section = Select-WzhkBuilderReviewSection
                if ($null -ne $section) { $stageNumber = $section }
                continue
            }

            if (-not (Read-WzhkYesNo -Prompt "Generate this exact resolved profile?" -YesText "GENERATE PROFILE" -NoText "FIX SETTINGS")) {
                $section = Select-WzhkBuilderReviewSection
                if ($null -ne $section) { $stageNumber = $section }
                continue
            }
            $save = Save-WzhkBuilderProfileInteractive `
                -Profile $profile `
                -ProfileDirectory $ProfileDirectory `
                -InitialProfilePath $InitialProfilePath `
                -SuggestedSavePath $SuggestedSavePath
            if ($save.Saved) { return $save.Result }
            continue
        }
        else {
            $guidance = @(
                "Choose EDIT to change this stage, NEXT to keep it, or BACK to revisit the prior stage.",
                "RECOMMENDED resets this stage; RESET SCENE restores approved scene-derived defaults."
            )
            if ($stageNumber -eq 6) { $guidance += "Blender 5.2 has no supported high-quality-normals RNA setting; it remains false." }
            Show-WzhkBuilderStage -StageTitle $stage.Title -StageNumber $stageNumber -StageCount $script:BuilderStageCount -Fields @(Get-WzhkProfileBuilderStageFields -Profile $profile -StageNumber $stageNumber) -Guidance $guidance -Status "DRAFT"
        }

        $action = Read-WzhkBuilderNavigation `
            -StageTitle $stage.Title `
            -StageNumber $stageNumber `
            -StageCount $script:BuilderStageCount `
            -CanGoBack:($stageNumber -gt 1) `
            -CanGoNext:$true `
            -CanEdit:$true `
            -CanResetScene:($null -ne $selectedScene)
        if ($null -eq $action) { $action = "Cancel" }

        switch ([string]$action) {
            "Back" { if ($stageNumber -gt 1) { $stageNumber -= 1 } }
            "Next" {
                if ($stageNumber -lt 13) { $stageNumber += 1; continue }
                $validation = Test-WzhkRenderProfile -Profile $profile
                if (-not $validation.Valid) {
                    Show-WzhkMessage -Title "PROFILE INVALID" -Lines @($validation.Errors) -Color Red
                    $section = Select-WzhkBuilderReviewSection
                    if ($null -ne $section) { $stageNumber = $section }
                    continue
                }
                Show-WzhkProfileSummary -Profile $profile -Title "PROFILE BUILDER // GENERATE PROFILE" -SafetyLines @(
                    "Saving writes normalized JSON atomically plus a sibling summary.",
                    "Saving does not authorize, encode, or render."
                )
                if (-not (Read-WzhkYesNo -Prompt "Generate and save this exact profile?" -YesText "GENERATE PROFILE" -NoText "FIX SETTINGS")) {
                    $section = Select-WzhkBuilderReviewSection
                    if ($null -ne $section) { $stageNumber = $section }
                    continue
                }
                $save = Save-WzhkBuilderProfileInteractive -Profile $profile -ProfileDirectory $ProfileDirectory -InitialProfilePath $InitialProfilePath -SuggestedSavePath $SuggestedSavePath
                if ($save.Saved) { return $save.Result }
            }
            "Edit" {
                if ($stageNumber -eq 13) {
                    $section = Select-WzhkBuilderReviewSection
                    if ($null -ne $section) { $stageNumber = $section }
                }
                else {
                    $editResult = Invoke-WzhkBuilderStageEdit -Profile $profile -StageNumber $stageNumber -Scenes $scenes.ToArray() -SelectedScene $selectedScene
                    $profile = $editResult.Profile
                    $selectedScene = $editResult.Scene
                }
            }
            "Recommended" {
                if ($stageNumber -eq 13) {
                    Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // RESET RECOMMENDED"
                    Write-WzhkFrameLine -Text "  This resets production settings in stages 3–12 while preserving identity and scene." -Color Yellow
                    if (Read-WzhkYesNo -Prompt "Reset production settings to recommended values?" -YesText "RESET SETTINGS" -NoText "KEEP PROFILE") {
                        foreach ($resetStage in @($stages | Where-Object { $_.Number -ge 3 -and $_.Number -le 12 })) {
                            $profile = Copy-WzhkBuilderStageDefaults -Profile $profile -RecommendedProfile $recommendedProfile -StageId $resetStage.Id
                        }
                        $profile = Update-WzhkBuilderDerivedValues -Profile $profile
                    }
                }
                elseif ($stageNumber -eq 2 -and $null -ne $selectedScene) {
                    $profile = Set-WzhkBuilderScene -Profile $profile -Scene $selectedScene -ResetSceneDefaults
                }
                else {
                    $profile = Copy-WzhkBuilderStageDefaults -Profile $profile -RecommendedProfile $recommendedProfile -StageId $stage.Id
                    $profile = Update-WzhkBuilderDerivedValues -Profile $profile
                }
            }
            "ResetScene" {
                if ($null -ne $selectedScene) {
                    Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // RESET SCENE DEFAULTS"
                    Write-WzhkFrameLine -Text "  This restores the approved scene identity, native FPS, and full scene range." -Color Yellow
                    if (Read-WzhkYesNo -Prompt "Reset to approved scene defaults?" -YesText "RESET SCENE" -NoText "KEEP SETTINGS") {
                        $profile = Set-WzhkBuilderScene -Profile $profile -Scene $selectedScene -ResetSceneDefaults
                        $profile = Update-WzhkBuilderDerivedValues -Profile $profile
                    }
                }
            }
            "Cancel" {
                Write-WzhkScreenHeader -Subtitle "PROFILE BUILDER // CANCEL"
                Write-WzhkFrameLine -Text "  Unsaved builder changes will be discarded; saved profiles remain untouched." -Color Yellow
                if (Read-WzhkYesNo -Prompt "Cancel the profile builder?" -YesText "DISCARD UNSAVED" -NoText "CONTINUE EDITING") { return $null }
            }
        }
    }
}

Export-ModuleMember -Function `
    Get-WzhkProfileBuilderStages, `
    Resolve-WzhkQualitySettings, `
    Get-WzhkProfileBuilderStageFields, `
    Update-WzhkBuilderDerivedValues, `
    Invoke-WzhkProfileBuilder
