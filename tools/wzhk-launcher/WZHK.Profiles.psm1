Set-StrictMode -Version Latest

$script:ProfileSchemaVersion = "1.1.0"
$script:ProfileKind = "trackprompt-render-profile"
$script:AuthorizationKind = "trackprompt-render-profile-authorization"
$script:AuthorizationRequestKind = "trackprompt-render-profile-authorization-request"
$script:RenderManifestKind = "trackprompt-final-render-manifest"
$script:DeliveryColorFilter = "colorspace=ispace=gbr:iprimaries=bt709:itrc=iec61966-2-1:irange=pc:space=bt709:primaries=bt709:trc=bt709:range=tv:format=yuv420p"
$script:MasterColorFilter = "colorspace=ispace=gbr:iprimaries=bt709:itrc=iec61966-2-1:irange=pc:space=bt709:primaries=bt709:trc=bt709:range=tv:format=yuv422p10,format=yuv422p10le"

function Get-WzhkUtcTimestamp {
    return [DateTime]::UtcNow.ToString("o", [Globalization.CultureInfo]::InvariantCulture)
}

function Get-WzhkPropertyValue {
    param(
        [AllowNull()][object]$Object,
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowNull()][object]$Default = $null
    )

    if ($null -eq $Object) { return $Default }
    if ($Object -is [Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return $Object[$Name] }
        return $Default
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
}

function Get-WzhkNestedValue {
    param(
        [AllowNull()][object]$Object,
        [Parameter(Mandatory = $true)][string[]]$Path,
        [AllowNull()][object]$Default = $null
    )

    $current = $Object
    foreach ($segment in $Path) {
        $sentinel = New-Object object
        $next = Get-WzhkPropertyValue -Object $current -Name $segment -Default $sentinel
        if ([Object]::ReferenceEquals($next, $sentinel)) { return $Default }
        $current = $next
    }
    if ($null -eq $current) { return $Default }
    return $current
}

function Copy-WzhkProfileValue {
    param([Parameter(Mandatory = $true)][object]$Value)
    return ($Value | ConvertTo-Json -Depth 100 | ConvertFrom-Json)
}

function ConvertTo-WzhkSafeProfileName {
    param(
        [AllowNull()][string]$Name,
        [string]$Fallback = "UNTITLED RENDER PROFILE",
        [int]$MaximumLength = 80
    )

    $value = if ([string]::IsNullOrWhiteSpace($Name)) { $Fallback } else { $Name.Trim() }
    $value = [regex]::Replace($value, '[\x00-\x1F<>:"/\\|?*]+', " ")
    $value = [regex]::Replace($value, '\s+', " ").Trim(' ', '.')
    if ([string]::IsNullOrWhiteSpace($value)) { $value = $Fallback }
    if ($value.Length -gt $MaximumLength) { $value = $value.Substring(0, $MaximumLength).TrimEnd() }
    return $value
}

function ConvertTo-WzhkProfileSlug {
    param([AllowNull()][string]$Name)

    $safe = (ConvertTo-WzhkSafeProfileName -Name $Name -Fallback "render-profile").ToLowerInvariant()
    $slug = [regex]::Replace($safe, '[^a-z0-9]+', '-')
    $slug = $slug.Trim('-')
    if ([string]::IsNullOrWhiteSpace($slug)) { return "render-profile" }
    if ($slug.Length -gt 64) { $slug = $slug.Substring(0, 64).TrimEnd('-') }
    return $slug
}

function Get-WzhkFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "File does not exist: $Path" }
    $fileHashCommand = Get-Command -Name Get-FileHash -ErrorAction SilentlyContinue
    if ($null -ne $fileHashCommand) {
        return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
    }

    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).ProviderPath
    $stream = [IO.File]::OpenRead($resolved)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToUpperInvariant()
    }
    finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function ConvertTo-WzhkCanonicalNode {
    param(
        [AllowNull()][object]$Value,
        [int]$Depth = 0,
        [string[]]$ExcludedTopLevelProperties = @()
    )

    if ($Depth -gt 100) { throw "Profile nesting exceeds the canonicalization depth limit." }
    if ($null -eq $Value) { return $null }
    if ($Value -is [string] -or $Value -is [char] -or $Value -is [ValueType]) { return $Value }

    if ($Value -is [Collections.IDictionary]) {
        $result = [ordered]@{}
        foreach ($key in @($Value.Keys | ForEach-Object { [string]$_ } | Sort-Object -CaseSensitive)) {
            if ($Depth -eq 0 -and $ExcludedTopLevelProperties -contains $key) { continue }
            $result[$key] = ConvertTo-WzhkCanonicalNode -Value $Value[$key] -Depth ($Depth + 1) -ExcludedTopLevelProperties $ExcludedTopLevelProperties
        }
        return $result
    }

    if ($Value -is [Collections.IEnumerable] -and -not ($Value -is [pscustomobject])) {
        $items = New-Object System.Collections.Generic.List[object]
        foreach ($item in $Value) {
            $items.Add((ConvertTo-WzhkCanonicalNode -Value $item -Depth ($Depth + 1) -ExcludedTopLevelProperties $ExcludedTopLevelProperties))
        }
        return ,$items.ToArray()
    }

    $objectResult = [ordered]@{}
    foreach ($property in @($Value.PSObject.Properties | Sort-Object Name -CaseSensitive)) {
        if ($Depth -eq 0 -and $ExcludedTopLevelProperties -contains $property.Name) { continue }
        $objectResult[$property.Name] = ConvertTo-WzhkCanonicalNode -Value $property.Value -Depth ($Depth + 1) -ExcludedTopLevelProperties $ExcludedTopLevelProperties
    }
    return $objectResult
}

function Get-WzhkCanonicalProfileJson {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [switch]$ForHash
    )

    $excluded = if ($ForHash) { @("integrity", "profileSha256") } else { @() }
    $canonical = ConvertTo-WzhkCanonicalNode -Value $Profile -ExcludedTopLevelProperties $excluded
    return ($canonical | ConvertTo-Json -Depth 100 -Compress)
}

function Get-WzhkProfileContentHash {
    param([Parameter(Mandatory = $true)][object]$Profile)

    $json = Get-WzhkCanonicalProfileJson -Profile $Profile -ForHash
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToUpperInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-WzhkReducedAspect {
    param([int]$Width, [int]$Height)
    if ($Width -lt 1 -or $Height -lt 1) { return "unknown" }
    $a = $Width
    $b = $Height
    while ($b -ne 0) {
        $remainder = $a % $b
        $a = $b
        $b = $remainder
    }
    return [string]::Format("{0}:{1}", ($Width / $a), ($Height / $a))
}

function Get-WzhkRenderProfileTemplates {
    $definitions = @(
        @("FULL-HD-FAST", "FULL HD FAST", 1920, 1080, 32, "256", 450, "PNG", 8, 15, $false),
        @("1440P-BALANCED", "1440P BALANCED", 2560, 1440, 64, "512", 300, "PNG", 16, 15, $false),
        @("4K-BALANCED", "4K BALANCED", 3840, 2160, 64, "512", 180, "PNG", 16, 15, $false),
        @("4K-HIGH", "4K HIGH", 3840, 2160, 128, "1024", 120, "PNG", 16, 10, $false),
        @("4K-ULTRA", "4K ULTRA", 3840, 2160, 256, "2048", 90, "PNG", 16, 5, $true),
        @("CUSTOM", "CUSTOM", 1920, 1080, 64, "512", 300, "PNG", 16, 15, $false)
    )

    $templates = New-Object System.Collections.Generic.List[object]
    foreach ($definition in $definitions) {
        $format = [string]$definition[7]
        $extension = if ($format -eq "OPEN_EXR") { "exr" } else { "png" }
        $templates.Add([pscustomobject][ordered]@{
            Id = [string]$definition[0]
            DisplayName = [string]$definition[1]
            Width = [int]$definition[2]
            Height = [int]$definition[3]
            Samples = [int]$definition[4]
            ShadowPoolSize = [string]$definition[5]
            FramesPerChunk = [int]$definition[6]
            ImageFormat = $format
            Extension = $extension
            BitDepth = [int]$definition[8]
            Compression = $definition[9]
            MotionBlur = [bool]$definition[10]
            Engine = "BLENDER_EEVEE"
            ResolutionPercentage = 100
            ColorMode = "RGB"
            ViewTransform = "AgX"
            Look = "AgX - Medium High Contrast"
            ResumePolicy = "validated-missing-frames-only"
        })
    }
    return $templates.ToArray()
}

function Get-WzhkRenderProfileTemplate {
    param([Parameter(Mandatory = $true)][string]$Id)
    $normalized = $Id.Trim().ToUpperInvariant().Replace(" ", "-")
    $template = Get-WzhkRenderProfileTemplates | Where-Object { $_.Id -eq $normalized } | Select-Object -First 1
    if ($null -eq $template) { throw "Unknown render profile template: $Id" }
    return Copy-WzhkProfileValue -Value $template
}

function Get-WzhkStorageEstimate {
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
    $planned = [Math]::Ceiling(($Width * $Height * 3.0 * $channelBytes * $compressionFactor * $FrameCount / 1GB) * 1000.0) / 1000.0
    $duration = $FrameCount / $Fps
    $scale = ($Width * $Height) / (1920.0 * 1080.0)
    $master = [Math]::Ceiling(([Math]::Max(0.25, $duration * 0.018 * $scale)) * 1000.0) / 1000.0
    $delivery = [Math]::Ceiling(([Math]::Max(0.05, $duration * 0.0015 * $scale)) * 1000.0) / 1000.0
    $reserve = 2.0
    $contingency = 1.5
    $minimum = [Math]::Ceiling(($planned + $master + $delivery + $reserve) * $contingency)
    return [pscustomobject][ordered]@{
        PlannedFrameSequenceGiB = $planned
        ProjectedMasterGiB = $master
        ProjectedDeliveryGiB = $delivery
        SupportReserveGiB = $reserve
        ContingencyMultiplier = $contingency
        MinimumLaunchFreeGiB = [double]$minimum
        EstimatedDurationSeconds = $duration
    }
}

function New-WzhkRenderProfile {
    [CmdletBinding()]
    param(
        [string]$TemplateId = "1440P-BALANCED",
        [string]$DisplayName = "",
        [string]$ProfileId = "",
        [string]$Project = "trip-to-andromeda",
        [string]$Preset = "space-journey",
        [string]$ApprovedScenePath = "",
        [string]$ApprovedSceneSha256 = "",
        [string]$SceneManifestPath = "",
        [string]$SceneManifestSha256 = "",
        [int]$FrameStart = 1,
        [int]$FrameEnd = 13029,
        [double]$Fps = 30.0,
        [string]$BlenderVersion = "5.2.0 LTS",
        [AllowNull()][object]$BaseProfile = $null
    )

    $template = Get-WzhkRenderProfileTemplate -Id $TemplateId
    if ([string]::IsNullOrWhiteSpace($DisplayName)) { $DisplayName = $template.DisplayName }
    $DisplayName = ConvertTo-WzhkSafeProfileName -Name $DisplayName

    if (-not [string]::IsNullOrWhiteSpace($ApprovedScenePath) -and (Test-Path -LiteralPath $ApprovedScenePath -PathType Leaf)) {
        $ApprovedScenePath = [IO.Path]::GetFullPath($ApprovedScenePath)
        if ([string]::IsNullOrWhiteSpace($ApprovedSceneSha256)) { $ApprovedSceneSha256 = Get-WzhkFileSha256 -Path $ApprovedScenePath }
    }
    if (-not [string]::IsNullOrWhiteSpace($SceneManifestPath) -and (Test-Path -LiteralPath $SceneManifestPath -PathType Leaf)) {
        $SceneManifestPath = [IO.Path]::GetFullPath($SceneManifestPath)
        if ([string]::IsNullOrWhiteSpace($SceneManifestSha256)) { $SceneManifestSha256 = Get-WzhkFileSha256 -Path $SceneManifestPath }
    }

    $sceneHash = if ([string]::IsNullOrWhiteSpace($ApprovedSceneSha256)) { "" } else { $ApprovedSceneSha256.ToUpperInvariant() }
    $manifestHash = if ([string]::IsNullOrWhiteSpace($SceneManifestSha256)) { "" } else { $SceneManifestSha256.ToUpperInvariant() }
    $frameCount = [Math]::Max(0, $FrameEnd - $FrameStart + 1)
    $estimate = Get-WzhkStorageEstimate -Width $template.Width -Height $template.Height -FrameCount $frameCount -Fps $Fps -BitDepth $template.BitDepth -Format $template.ImageFormat
    $timestamp = Get-WzhkUtcTimestamp
    $stableId = ([Guid]::NewGuid().ToString("D")).ToUpperInvariant()
    if ([string]::IsNullOrWhiteSpace($ProfileId)) {
        $ProfileId = switch ($template.Id) {
            "FULL-HD-FAST" { "1080P-30-SDR-FAST" }
            "1440P-BALANCED" { "1440P-30-SDR" }
            "4K-BALANCED" { "4K-30-SDR-BALANCED" }
            "4K-HIGH" { "4K-30-SDR-HIGH" }
            "4K-ULTRA" { "4K-30-SDR-ULTRA" }
            default { (ConvertTo-WzhkProfileSlug -Name $DisplayName).ToUpperInvariant() }
        }
    }
    $profileId = (ConvertTo-WzhkProfileSlug -Name $ProfileId).ToUpperInvariant()
    $displayBaked = ($template.ImageFormat -eq "PNG")

    $audio = [ordered]@{
        identityStatus = "unbound"
        sampleRate = 48000
        channels = 2
        durationSeconds = [Math]::Max(0.001, $estimate.EstimatedDurationSeconds)
    }
    $visualQa = [ordered]@{
        namedFrames = @([ordered]@{ frame = $FrameStart; role = "opening" }, [ordered]@{ frame = $FrameEnd; role = "outro" })
        sectionAndTransitionFrames = @()
        highMotionRanges = @()
        humanReviewRequired = $true
    }
    if ($null -ne $BaseProfile) {
        $baseAudio = Get-WzhkPropertyValue -Object $BaseProfile -Name "audio"
        if ($null -ne $baseAudio) {
            $audio = Copy-WzhkProfileValue -Value $baseAudio
            $baseAudioHash = [string](Get-WzhkPropertyValue -Object $audio -Name "sha256" -Default "")
            if (Test-WzhkSha256Text $baseAudioHash) {
                if ($null -eq $audio.PSObject.Properties["identityStatus"]) {
                    Add-Member -InputObject $audio -NotePropertyName identityStatus -NotePropertyValue "approved"
                }
                else {
                    $audio.identityStatus = "approved"
                }
            }
        }
        $baseQa = Get-WzhkPropertyValue -Object $BaseProfile -Name "visualQa"
        if ($null -ne $baseQa) { $visualQa = Copy-WzhkProfileValue -Value $baseQa }
    }

    $compositor = [ordered]@{
        enabled = $true
        name = "TP_SPACE_COMPOSITOR"
        precision = "AUTO"
        fogGlow = $true
        fogGlowEnabled = $true
        fogGlowQuality = "HIGH"
        fogGlowThreshold = 1.026
        fogGlowStrength = 0.418
        fogGlowSize = 0.704
        fogGlowIterations = 3
    }

    $profile = [pscustomobject][ordered]@{
        schemaVersion = $script:ProfileSchemaVersion
        kind = $script:ProfileKind
        id = $stableId
        profileId = $profileId
        displayName = $DisplayName
        templateId = $template.Id
        timestamps = [ordered]@{ createdAt = $timestamp; updatedAt = $timestamp }
        createdAt = $timestamp
        updatedAt = $timestamp
        project = (ConvertTo-WzhkSafeProfileName -Name $Project -Fallback "trackprompt")
        preset = (ConvertTo-WzhkSafeProfileName -Name $Preset -Fallback "custom")
        blenderVersion = $BlenderVersion
        approvedScene = [ordered]@{
            path = $ApprovedScenePath
            sha256 = $sceneHash
            manifestPath = $SceneManifestPath
            manifestSha256 = $manifestHash
        }
        approvedScenePath = $ApprovedScenePath
        approvedSceneSha256 = $sceneHash
        sceneManifestPath = $SceneManifestPath
        sourceIdentities = [ordered]@{ sceneManifestSha256 = $manifestHash }
        timeline = [ordered]@{
            frameStart = $FrameStart
            frameEnd = $FrameEnd
            frameCount = $frameCount
            fps = $Fps
            durationSeconds = $estimate.EstimatedDurationSeconds
        }
        frameStart = $FrameStart
        frameEnd = $FrameEnd
        fps = $Fps
        durationSeconds = $estimate.EstimatedDurationSeconds
        resolution = [ordered]@{
            label = $template.DisplayName
            width = $template.Width
            height = $template.Height
            percentage = 100
            pixelAspect = "1:1"
            pixelAspectX = 1.0
            pixelAspectY = 1.0
            displayAspect = (Get-WzhkReducedAspect -Width $template.Width -Height $template.Height)
            dynamicRange = "SDR"
        }
        aspect = [ordered]@{
            pixel = "1:1"
            display = (Get-WzhkReducedAspect -Width $template.Width -Height $template.Height)
        }
        render = [ordered]@{
            engine = $template.Engine
            device = "GPU"
            samples = $template.Samples
            shadowPoolSize = $template.ShadowPoolSize
            shadowRayCount = 1
            shadowResolutionScale = 1.0
            rayTracing = ($template.Samples -ge 128)
            rayTracingMethod = "PROBE"
            highQualityNormals = $false
            volumetricTileSize = $(if ($template.Samples -ge 256) { "2" } elseif ($template.Samples -ge 128) { "4" } else { "8" })
            volumetricSamples = $(if ($template.Samples -ge 256) { 128 } elseif ($template.Samples -ge 128) { 64 } else { 32 })
            volumetricShadowSamples = $(if ($template.Samples -ge 256) { 32 } elseif ($template.Samples -ge 128) { 16 } else { 8 })
            volumetricRayDepth = $(if ($template.Samples -ge 128) { 16 } else { 8 })
            volumetricShadows = ($template.Samples -ge 128)
            motionBlur = $template.MotionBlur
            useCompositing = $true
            filmTransparent = $false
            ditherIntensity = 1.0
            compositor = (Copy-WzhkProfileValue -Value $compositor)
        }
        imageSequence = [ordered]@{
            format = $template.ImageFormat
            extension = $template.Extension
            bitDepth = $template.BitDepth
            colorMode = "RGB"
            compression = $template.Compression
            filenamePattern = [string]::Format("frame_%06d.{0}", $template.Extension)
            colorManagement = [ordered]@{
                displayTransformBaked = $displayBaked
                encodedColorSpace = $(if ($displayBaked) { "sRGB" } else { "scene-linear" })
                note = $(if ($displayBaked) { "The reviewed AgX display transform and compositor result are baked into each opaque PNG." } else { "OpenEXR frames remain scene-linear; the display transform is applied during review and encoding." })
            }
        }
        colorManagement = [ordered]@{
            displayDevice = "sRGB"
            viewTransform = "AgX"
            look = "AgX - Medium High Contrast"
            exposure = 0.0
            gamma = 1.0
            sequencerColorSpace = "sRGB"
        }
        sourceColorManagement = [ordered]@{
            displayDevice = "sRGB"
            viewTransform = "AgX"
            look = "AgX - Medium High Contrast"
            exposure = 0.0
            gamma = 1.0
            sequencerColorSpace = "sRGB"
        }
        compositor = (Copy-WzhkProfileValue -Value $compositor)
        production = [ordered]@{
            framesPerChunk = [Math]::Min($template.FramesPerChunk, [Math]::Max(1, $frameCount))
            resumeEnabled = $true
            resumePolicy = "validated-missing-frames-only"
            verifyExistingFrames = $true
            overwriteInvalidFrames = $true
            overwriteValidFrames = $false
            atomicChunkCommit = $true
            stopOnValidationFailure = $true
            maximumFramesPerChunk = 1200
        }
        chunking = [ordered]@{
            framesPerChunk = [Math]::Min($template.FramesPerChunk, [Math]::Max(1, $frameCount))
            rationale = "Bounded resumable chunks with validation before atomic publication."
        }
        output = [ordered]@{
            directoryPattern = "{project}-{preset}-{resolution}-{timestamp}"
            framesSubdirectory = "frames"
            checkpointsSubdirectory = "checkpoints"
            manifestsSubdirectory = "manifests"
            neverMixProfiles = $true
            compatibilityManifest = "manifests/render-manifest.json"
        }
        storage = [ordered]@{
            plannedFrameSequenceGiB = $estimate.PlannedFrameSequenceGiB
            projectedMasterGiB = $estimate.ProjectedMasterGiB
            projectedDeliveryGiB = $estimate.ProjectedDeliveryGiB
            supportReserveGiB = $estimate.SupportReserveGiB
            contingencyMultiplier = $estimate.ContingencyMultiplier
            minimumLaunchFreeGiB = $estimate.MinimumLaunchFreeGiB
        }
        audio = $audio
        encoding = [ordered]@{
            clockPolicy = "Image sequence is the video master clock; encode the exact reviewed frame range without -shortest."
            master = [ordered]@{
                enabled = $(Test-WzhkSha256Text ([string](Get-WzhkPropertyValue -Object $audio -Name "sha256" -Default "")))
                container = "mov"; fileExtension = ".mov"; videoCodec = "prores_ks"; expectedVideoCodec = "prores"
                profile = "3"; profileName = "ProRes 422 HQ"; displayToDeliveryFilter = $script:MasterColorFilter
                pixelFormat = "yuv422p10le"; audioCodec = "pcm_s24le"; expectedAudioCodec = "pcm_s24le"
                requireRec709Metadata = $true; color = [ordered]@{ primaries = "bt709"; transfer = "bt709"; space = "bt709"; range = "tv" }
            }
            delivery = [ordered]@{
                enabled = $(Test-WzhkSha256Text ([string](Get-WzhkPropertyValue -Object $audio -Name "sha256" -Default "")))
                container = "mp4"; fileExtension = ".mp4"; videoCodec = "libx264"; expectedVideoCodec = "h264"
                profile = "high"; profileName = "H.264 High quality"; displayToDeliveryFilter = $script:DeliveryColorFilter
                preset = "slow"; crf = 16; pixelFormat = "yuv420p"; audioCodec = "aac"; expectedAudioCodec = "aac"
                audioBitrate = "320k"; fastStart = $true; requireRec709Metadata = $true
                color = [ordered]@{ primaries = "bt709"; transfer = "bt709"; space = "bt709"; range = "tv" }
            }
        }
        dashboard = [ordered]@{
            enabled = $true
            autoLaunch = $true
            refreshSeconds = 6
            showLatestFrame = $true
            showEta = $true
            resolutionLabel = $(if ($template.Width -eq 3840 -and $template.Height -eq 2160) { "NATIVE 4K — 3840×2160" } else { [string]::Format("{0}×{1}", $template.Width, $template.Height) })
        }
        estimates = [ordered]@{
            durationSeconds = $estimate.EstimatedDurationSeconds
            frameCount = $frameCount
            plannedFrameSequenceGiB = $estimate.PlannedFrameSequenceGiB
            minimumLaunchFreeGiB = $estimate.MinimumLaunchFreeGiB
            estimateBasis = "Resolution, bit depth, image format, frame count, and conservative compression factors."
        }
        visualQa = $visualQa
        authorization = [ordered]@{
            status = "pending-operator-approval"
            project = (ConvertTo-WzhkSafeProfileName -Name $Project -Fallback "trackprompt").ToUpperInvariant()
            preset = (ConvertTo-WzhkSafeProfileName -Name $Preset -Fallback "custom").ToUpperInvariant()
            profile = $profileId.ToUpperInvariant()
            sceneSha256 = $sceneHash
            invalidatedAt = $null
            reason = "Exact scene and saved profile require two operator confirmations."
        }
        validation = [ordered]@{ status = "pending"; schemaVersion = $script:ProfileSchemaVersion; errors = @() }
        warnings = @()
        profileSha256 = ""
        integrity = [ordered]@{ algorithm = "SHA-256"; canonicalization = "sorted-json-v1"; profileSha256 = "" }
    }

    $validation = Test-WzhkRenderProfile -Profile $profile
    $profile.validation = [pscustomobject][ordered]@{
        status = $(if ($validation.Valid) { "valid" } else { "invalid" })
        schemaVersion = $script:ProfileSchemaVersion
        errors = @($validation.Errors)
    }
    $profile.warnings = @($validation.Warnings)
    $hash = Get-WzhkProfileContentHash -Profile $profile
    $profile.profileSha256 = $hash
    $profile.integrity.profileSha256 = $hash
    return $profile
}

function Normalize-WzhkRenderProfile {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][object]$Profile)

    $schema = [string](Get-WzhkPropertyValue -Object $Profile -Name "schemaVersion" -Default "")
    if ($schema -eq $script:ProfileSchemaVersion) {
        $copy = Copy-WzhkProfileValue -Value $Profile
        if ($null -eq $copy.PSObject.Properties["kind"]) { Add-Member -InputObject $copy -NotePropertyName kind -NotePropertyValue $script:ProfileKind }
        if ($null -eq $copy.PSObject.Properties["displayName"]) {
            Add-Member -InputObject $copy -NotePropertyName displayName -NotePropertyValue ([string](Get-WzhkPropertyValue -Object $copy -Name "profileId" -Default "RENDER PROFILE"))
        }
        if ($null -eq $copy.PSObject.Properties["id"]) { Add-Member -InputObject $copy -NotePropertyName id -NotePropertyValue ([Guid]::NewGuid().ToString("D").ToUpperInvariant()) }
        if ($null -eq $copy.PSObject.Properties["profileSha256"]) { Add-Member -InputObject $copy -NotePropertyName profileSha256 -NotePropertyValue "" }
        if ($null -eq $copy.PSObject.Properties["integrity"]) {
            Add-Member -InputObject $copy -NotePropertyName integrity -NotePropertyValue ([pscustomobject][ordered]@{ algorithm = "SHA-256"; canonicalization = "sorted-json-v1"; profileSha256 = "" })
        }
        return $copy
    }

    if ($schema -ne "1.0.0") { throw "Unsupported render profile schemaVersion: $schema" }

    $resolution = Get-WzhkPropertyValue -Object $Profile -Name "resolution"
    $width = [int](Get-WzhkPropertyValue -Object $resolution -Name "width" -Default 1920)
    $height = [int](Get-WzhkPropertyValue -Object $resolution -Name "height" -Default 1080)
    $templateId = if ($width -eq 3840 -and $height -eq 2160) { "4K-BALANCED" } elseif ($width -eq 2560 -and $height -eq 1440) { "1440P-BALANCED" } else { "CUSTOM" }
    $timeline = Get-WzhkPropertyValue -Object $Profile -Name "timeline" -Default $Profile
    $frameStart = [int](Get-WzhkPropertyValue -Object $timeline -Name "frameStart" -Default (Get-WzhkPropertyValue -Object $Profile -Name "frameStart" -Default 1))
    $frameEnd = [int](Get-WzhkPropertyValue -Object $timeline -Name "frameEnd" -Default (Get-WzhkPropertyValue -Object $Profile -Name "frameEnd" -Default 1))
    $fps = [double](Get-WzhkPropertyValue -Object $timeline -Name "fps" -Default (Get-WzhkPropertyValue -Object $Profile -Name "fps" -Default 30))
    $approvedSceneHash = [string](Get-WzhkPropertyValue -Object $Profile -Name "approvedSceneSha256" -Default (Get-WzhkNestedValue -Object $Profile -Path @("hashes", "approvedSceneSha256") -Default ""))
    $sourceIdentities = Get-WzhkPropertyValue -Object $Profile -Name "sourceIdentities"
    $manifestHash = [string](Get-WzhkPropertyValue -Object $sourceIdentities -Name "sceneManifestSha256" -Default "")
    $normalized = New-WzhkRenderProfile `
        -TemplateId $templateId `
        -DisplayName ([string](Get-WzhkPropertyValue -Object $Profile -Name "displayName" -Default (Get-WzhkPropertyValue -Object $Profile -Name "profileId" -Default "IMPORTED PROFILE"))) `
        -Project ([string](Get-WzhkPropertyValue -Object $Profile -Name "project" -Default "trackprompt")) `
        -Preset ([string](Get-WzhkPropertyValue -Object $Profile -Name "preset" -Default "custom")) `
        -ApprovedScenePath ([string](Get-WzhkPropertyValue -Object $Profile -Name "approvedScenePath" -Default "")) `
        -ApprovedSceneSha256 $approvedSceneHash `
        -SceneManifestPath ([string](Get-WzhkPropertyValue -Object $Profile -Name "sceneManifestPath" -Default "")) `
        -SceneManifestSha256 $manifestHash `
        -FrameStart $frameStart `
        -FrameEnd $frameEnd `
        -Fps $fps `
        -BlenderVersion ([string](Get-WzhkPropertyValue -Object $Profile -Name "blenderVersion" -Default "5.2.0 LTS")) `
        -BaseProfile $Profile

    $normalized.profileId = [string](Get-WzhkPropertyValue -Object $Profile -Name "profileId" -Default $normalized.profileId)
    $normalized.authorization.profile = $normalized.profileId.ToUpperInvariant()
    $normalized.resolution = Copy-WzhkProfileValue -Value $resolution
    if ($null -eq $normalized.resolution.PSObject.Properties["label"]) { Add-Member -InputObject $normalized.resolution -NotePropertyName label -NotePropertyValue ([string]::Format("{0}x{1}", $width, $height)) }
    $normalized.aspect.display = Get-WzhkReducedAspect -Width $width -Height $height
    $normalized.timeline.frameCount = $frameEnd - $frameStart + 1
    $normalized.timeline.durationSeconds = $normalized.timeline.frameCount / $fps
    $normalized.durationSeconds = $normalized.timeline.durationSeconds

    foreach ($name in @("render", "imageSequence", "colorManagement", "chunking", "storage", "audio", "encoding", "visualQa", "sourceIdentities")) {
        $legacyValue = Get-WzhkPropertyValue -Object $Profile -Name $name
        if ($null -ne $legacyValue) { $normalized.$name = Copy-WzhkProfileValue -Value $legacyValue }
    }
    $normalized.compositor = Copy-WzhkProfileValue -Value (Get-WzhkPropertyValue -Object $normalized.render -Name "compositor" -Default $normalized.compositor)
    $legacyChunkSize = [int](Get-WzhkPropertyValue -Object $normalized.chunking -Name "framesPerChunk" -Default $normalized.production.framesPerChunk)
    $normalized.production.framesPerChunk = $legacyChunkSize
    $normalized.approvedSceneSha256 = $approvedSceneHash.ToUpperInvariant()
    $normalized.approvedScene.sha256 = $normalized.approvedSceneSha256
    $normalized.sourceIdentities.sceneManifestSha256 = $manifestHash.ToUpperInvariant()
    $normalized.authorization.sceneSha256 = $normalized.approvedSceneSha256
    $normalized.authorization.status = "pending-operator-approval"
    $normalized.authorization.reason = "Imported legacy profile requires fresh two-step authorization."
    $normalized.warnings = @("Imported from schema 1.0.0 and normalized to schema 1.1.0; review all resolved settings before authorization.")
    $validation = Test-WzhkRenderProfile -Profile $normalized
    $normalized.validation.status = if ($validation.Valid) { "valid" } else { "invalid" }
    $normalized.validation.errors = @($validation.Errors)
    $normalized.warnings = @($normalized.warnings) + @($validation.Warnings)
    $hash = Get-WzhkProfileContentHash -Profile $normalized
    $normalized.profileSha256 = $hash
    $normalized.integrity.profileSha256 = $hash
    return $normalized
}

function Add-WzhkValidationError {
    param([Collections.Generic.List[string]]$Errors, [string]$Message)
    if (-not $Errors.Contains($Message)) { $Errors.Add($Message) }
}

function Test-WzhkSha256Text {
    param([AllowNull()][object]$Value)
    return ($Value -is [string] -and $Value -match '^[A-Fa-f0-9]{64}$')
}

function Test-WzhkFiniteNumber {
    param([AllowNull()][object]$Value)
    if ($Value -is [bool] -or $null -eq $Value) { return $false }
    $number = 0.0
    if (-not [double]::TryParse([string]$Value, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$number)) { return $false }
    return (-not [double]::IsNaN($number) -and -not [double]::IsInfinity($number))
}

function Test-WzhkOutputDirectoryPattern {
    param([AllowNull()][object]$Pattern)

    $text = [string]$Pattern
    $message = ""
    if ([string]::IsNullOrWhiteSpace($text) -or $text.Length -gt 120) {
        $message = "output.directoryPattern must contain 1 through 120 characters."
    }
    else {
        $tokens = @("{project}", "{preset}", "{resolution}", "{profile}", "{timestamp}")
        $remainder = $text
        foreach ($token in $tokens) { $remainder = $remainder.Replace($token, "") }
        $sample = $text.Replace("{project}", "project").Replace("{preset}", "preset").Replace("{resolution}", "resolution").Replace("{profile}", "profile").Replace("{timestamp}", "20260720-120000")
        if (
            $remainder -match '[{}]' -or
            $remainder -notmatch '^[A-Za-z0-9._-]*$' -or
            $text -match '[\\/:]' -or
            $text -notmatch '\{timestamp\}' -or
            $sample -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$' -or
            $sample.EndsWith('.')
        ) {
            $message = "output.directoryPattern may use safe literals plus {project}, {preset}, {resolution}, {profile}, must include {timestamp}, and must expand to one safe directory name."
        }
    }
    return [pscustomobject]@{ Valid = [string]::IsNullOrWhiteSpace($message); Message = $message }
}

function Test-WzhkRenderProfile {
    [CmdletBinding(DefaultParameterSetName = "Object")]
    param(
        [Parameter(Mandatory = $true, ParameterSetName = "Object")][object]$Profile,
        [Parameter(Mandatory = $true, ParameterSetName = "Path")][string]$Path,
        [switch]$VerifyFiles
    )

    $errors = New-Object System.Collections.Generic.List[string]
    $warnings = New-Object System.Collections.Generic.List[string]
    $candidate = $Profile
    if ($PSCmdlet.ParameterSetName -eq "Path") {
        try { $candidate = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop }
        catch {
            Add-WzhkValidationError -Errors $errors -Message "Profile is not readable valid JSON."
            return [pscustomobject]@{ Valid = $false; Errors = $errors.ToArray(); Warnings = @(); ContentSha256 = ""; Profile = $null }
        }
    }

    $schema = [string](Get-WzhkPropertyValue -Object $candidate -Name "schemaVersion" -Default "")
    if ($schema -notin @("1.0.0", $script:ProfileSchemaVersion)) { Add-WzhkValidationError $errors "schemaVersion must be 1.0.0 or 1.1.0." }
    foreach ($field in @("project", "preset", "profileId")) {
        $value = [string](Get-WzhkPropertyValue -Object $candidate -Name $field -Default "")
        if ([string]::IsNullOrWhiteSpace($value)) { Add-WzhkValidationError $errors "$field must be a non-empty string." }
        elseif ($value -match '[\r\n|]') { Add-WzhkValidationError $errors "$field contains a forbidden delimiter." }
    }
    if ($schema -eq $script:ProfileSchemaVersion) {
        $id = [string](Get-WzhkPropertyValue -Object $candidate -Name "id" -Default "")
        if ($id -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{2,95}$') { Add-WzhkValidationError $errors "id must be a stable safe identifier." }
        $displayName = [string](Get-WzhkPropertyValue -Object $candidate -Name "displayName" -Default "")
        if ([string]::IsNullOrWhiteSpace($displayName) -or $displayName -ne (ConvertTo-WzhkSafeProfileName -Name $displayName)) { Add-WzhkValidationError $errors "displayName must be non-empty and sanitized." }
    }

    $sceneHash = [string](Get-WzhkPropertyValue -Object $candidate -Name "approvedSceneSha256" -Default (Get-WzhkNestedValue -Object $candidate -Path @("approvedScene", "sha256") -Default ""))
    if (-not (Test-WzhkSha256Text $sceneHash)) { Add-WzhkValidationError $errors "approvedSceneSha256 must be a 64-character SHA-256 value." }
    $scenePath = [string](Get-WzhkPropertyValue -Object $candidate -Name "approvedScenePath" -Default (Get-WzhkNestedValue -Object $candidate -Path @("approvedScene", "path") -Default ""))
    if (-not [string]::IsNullOrWhiteSpace($scenePath) -and [IO.Path]::GetExtension($scenePath).ToLowerInvariant() -ne ".blend") { Add-WzhkValidationError $errors "Approved scene path must name a .blend file." }
    if ($VerifyFiles) {
        if ([string]::IsNullOrWhiteSpace($scenePath) -or -not (Test-Path -LiteralPath $scenePath -PathType Leaf)) { Add-WzhkValidationError $errors "Approved scene file does not exist." }
        elseif ((Get-WzhkFileSha256 -Path $scenePath) -ne $sceneHash.ToUpperInvariant()) { Add-WzhkValidationError $errors "Approved scene file hash does not match the profile." }
        $manifestPath = [string](Get-WzhkPropertyValue -Object $candidate -Name "sceneManifestPath" -Default (Get-WzhkNestedValue -Object $candidate -Path @("approvedScene", "manifestPath") -Default ""))
        $manifestHash = [string](Get-WzhkNestedValue -Object $candidate -Path @("approvedScene", "manifestSha256") -Default (Get-WzhkNestedValue -Object $candidate -Path @("sourceIdentities", "sceneManifestSha256") -Default ""))
        if (-not [string]::IsNullOrWhiteSpace($manifestPath)) {
            if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { Add-WzhkValidationError $errors "Scene manifest file does not exist." }
            elseif (-not (Test-WzhkSha256Text $manifestHash) -or (Get-WzhkFileSha256 $manifestPath) -ne $manifestHash.ToUpperInvariant()) { Add-WzhkValidationError $errors "Scene manifest hash does not match the profile." }
        }
    }

    $timeline = Get-WzhkPropertyValue -Object $candidate -Name "timeline" -Default $candidate
    $frameStart = Get-WzhkPropertyValue -Object $timeline -Name "frameStart" -Default (Get-WzhkPropertyValue -Object $candidate -Name "frameStart")
    $frameEnd = Get-WzhkPropertyValue -Object $timeline -Name "frameEnd" -Default (Get-WzhkPropertyValue -Object $candidate -Name "frameEnd")
    $fps = Get-WzhkPropertyValue -Object $timeline -Name "fps" -Default (Get-WzhkPropertyValue -Object $candidate -Name "fps")
    if ($frameStart -isnot [int] -or [int]$frameStart -lt 1) { Add-WzhkValidationError $errors "timeline.frameStart must be an integer of at least 1." }
    if ($frameEnd -isnot [int] -or [int]$frameEnd -lt [int]$frameStart) { Add-WzhkValidationError $errors "timeline.frameEnd must be at least frameStart." }
    if (-not (Test-WzhkFiniteNumber $fps) -or [double]$fps -le 0 -or [double]$fps -gt 240) { Add-WzhkValidationError $errors "timeline.fps must be greater than 0 and at most 240." }
    $frameCount = if ($frameEnd -is [int] -and $frameStart -is [int]) { [int]$frameEnd - [int]$frameStart + 1 } else { 0 }
    $sourceTimeline = Get-WzhkPropertyValue -Object $candidate -Name "sourceTimeline"
    if ($null -ne $sourceTimeline) {
        $sourceStart = Get-WzhkPropertyValue -Object $sourceTimeline -Name "frameStart"
        $sourceEnd = Get-WzhkPropertyValue -Object $sourceTimeline -Name "frameEnd"
        $sourceFps = Get-WzhkPropertyValue -Object $sourceTimeline -Name "fps"
        if ($sourceStart -is [int] -and $sourceEnd -is [int] -and ($frameStart -ne $sourceStart -or $frameEnd -ne $sourceEnd)) {
            $warnings.Add("The selected frame range differs from the approved scene timeline; full-track audio encoding is disabled unless a matching approved audio segment is bound.")
        }
        if ((Test-WzhkFiniteNumber $sourceFps) -and (Test-WzhkFiniteNumber $fps) -and [Math]::Abs([double]$fps - [double]$sourceFps) -gt 0.000001) {
            $warnings.Add("The selected FPS differs from the approved scene clock; review timing and audio synchronization before authorization.")
        }
    }

    $resolution = Get-WzhkPropertyValue -Object $candidate -Name "resolution"
    $width = Get-WzhkPropertyValue -Object $resolution -Name "width"
    $height = Get-WzhkPropertyValue -Object $resolution -Name "height"
    $percentage = Get-WzhkPropertyValue -Object $resolution -Name "percentage" -Default 100
    if ($width -isnot [int] -or [int]$width -lt 16 -or [int]$width -gt 16384) { Add-WzhkValidationError $errors "resolution.width must be an integer from 16 through 16384." }
    if ($height -isnot [int] -or [int]$height -lt 16 -or [int]$height -gt 16384) { Add-WzhkValidationError $errors "resolution.height must be an integer from 16 through 16384." }
    if ($width -is [int] -and ([int]$width % 2) -ne 0) { Add-WzhkValidationError $errors "resolution.width must be even for the approved image-sequence and encoder workflow." }
    if ($height -is [int] -and ([int]$height % 2) -ne 0) { Add-WzhkValidationError $errors "resolution.height must be even for the approved image-sequence and encoder workflow." }
    if ($percentage -isnot [int] -or [int]$percentage -ne 100) { Add-WzhkValidationError $errors "resolution.percentage must be exactly 100." }
    $pixelAspectX = Get-WzhkPropertyValue -Object $resolution -Name "pixelAspectX" -Default 1.0
    $pixelAspectY = Get-WzhkPropertyValue -Object $resolution -Name "pixelAspectY" -Default 1.0
    foreach ($pixelAspectName in @("pixelAspectX", "pixelAspectY")) {
        $pixelAspectValue = if ($pixelAspectName -eq "pixelAspectX") { $pixelAspectX } else { $pixelAspectY }
        if (-not (Test-WzhkFiniteNumber $pixelAspectValue) -or [double]$pixelAspectValue -le 0.0 -or [double]$pixelAspectValue -gt 100.0) {
            Add-WzhkValidationError $errors "resolution.$pixelAspectName must be a positive finite number no greater than 100."
        }
    }
    if (
        $width -is [int] -and [int]$width -gt 0 -and
        $height -is [int] -and [int]$height -gt 0 -and
        (Test-WzhkFiniteNumber $pixelAspectX) -and [double]$pixelAspectX -gt 0.0 -and
        (Test-WzhkFiniteNumber $pixelAspectY) -and [double]$pixelAspectY -gt 0.0 -and
        [Math]::Abs((([double]$width * [double]$pixelAspectX) / ([double]$height * [double]$pixelAspectY)) - (16.0 / 9.0)) -gt 0.0005
    ) {
        $warnings.Add("Resolved display aspect ratio, including pixel aspect, differs from 16:9; verify delivery and display compatibility.")
    }
    if ($width -eq 3840 -and $height -eq 2160) { $warnings.Add("NATIVE 4K — 3840×2160 requires substantially more storage and render time.") }

    $render = Get-WzhkPropertyValue -Object $candidate -Name "render"
    $engine = [string](Get-WzhkPropertyValue -Object $render -Name "engine" -Default "")
    if (-not $engine.StartsWith("BLENDER_EEVEE")) { Add-WzhkValidationError $errors "render.engine must be Blender EEVEE." }
    $samples = Get-WzhkPropertyValue -Object $render -Name "samples"
    if ($samples -isnot [int] -or [int]$samples -lt 1 -or [int]$samples -gt 4096) { Add-WzhkValidationError $errors "render.samples must be an integer from 1 through 4096." }
    $shadowPool = [string](Get-WzhkPropertyValue -Object $render -Name "shadowPoolSize" -Default "")
    if ($shadowPool -notin @("128", "256", "512", "1024", "2048")) { Add-WzhkValidationError $errors "render.shadowPoolSize is not an approved value." }
    foreach ($booleanName in @("motionBlur", "useCompositing", "filmTransparent")) {
        if ((Get-WzhkPropertyValue -Object $render -Name $booleanName) -isnot [bool]) { Add-WzhkValidationError $errors "render.$booleanName must be boolean." }
    }
    if ((Get-WzhkPropertyValue -Object $render -Name "filmTransparent") -eq $true) {
        Add-WzhkValidationError $errors "render.filmTransparent must remain false because the canonical production sequence is opaque RGB."
    }
    $qualityMode = [string](Get-WzhkPropertyValue -Object $render -Name "qualityMode" -Default "CUSTOM")
    if ($qualityMode -notin @("DRAFT", "PREVIEW", "BALANCED", "HIGH", "ULTRA", "CUSTOM")) { Add-WzhkValidationError $errors "render.qualityMode is invalid." }
    $shadowRayCount = Get-WzhkPropertyValue -Object $render -Name "shadowRayCount" -Default $null
    if (($schema -eq $script:ProfileSchemaVersion -or $null -ne $shadowRayCount) -and ($shadowRayCount -isnot [int] -or [int]$shadowRayCount -lt 1 -or [int]$shadowRayCount -gt 4)) { Add-WzhkValidationError $errors "render.shadowRayCount must be an integer from 1 through 4." }
    $shadowResolutionScale = Get-WzhkPropertyValue -Object $render -Name "shadowResolutionScale" -Default $null
    if (($schema -eq $script:ProfileSchemaVersion -or $null -ne $shadowResolutionScale) -and (-not (Test-WzhkFiniteNumber $shadowResolutionScale) -or [double]$shadowResolutionScale -lt 0.0 -or [double]$shadowResolutionScale -gt 1.0)) { Add-WzhkValidationError $errors "render.shadowResolutionScale must be a finite number from 0 through 1." }
    foreach ($booleanName in @("rayTracing", "volumetricShadows")) {
        $booleanValue = Get-WzhkPropertyValue -Object $render -Name $booleanName -Default $null
        if (($schema -eq $script:ProfileSchemaVersion -or $null -ne $booleanValue) -and $booleanValue -isnot [bool]) { Add-WzhkValidationError $errors "render.$booleanName must be boolean." }
    }
    $rayTracingMethod = Get-WzhkPropertyValue -Object $render -Name "rayTracingMethod" -Default $null
    if (($schema -eq $script:ProfileSchemaVersion -or $null -ne $rayTracingMethod) -and [string]$rayTracingMethod -notin @("PROBE", "SCREEN")) { Add-WzhkValidationError $errors "render.rayTracingMethod must be PROBE or SCREEN." }
    $volumetricTileSize = Get-WzhkPropertyValue -Object $render -Name "volumetricTileSize" -Default $null
    if (($schema -eq $script:ProfileSchemaVersion -or $null -ne $volumetricTileSize) -and [string]$volumetricTileSize -notin @("1", "2", "4", "8", "16")) { Add-WzhkValidationError $errors "render.volumetricTileSize must be 1, 2, 4, 8, or 16." }
    foreach ($integerConstraint in @(
        @("volumetricSamples", 256),
        @("volumetricShadowSamples", 128),
        @("volumetricRayDepth", 16)
    )) {
        $integerName = [string]$integerConstraint[0]
        $integerMaximum = [int]$integerConstraint[1]
        $integerValue = Get-WzhkPropertyValue -Object $render -Name $integerName -Default $null
        if (($schema -eq $script:ProfileSchemaVersion -or $null -ne $integerValue) -and ($integerValue -isnot [int] -or [int]$integerValue -lt 1 -or [int]$integerValue -gt $integerMaximum)) {
            Add-WzhkValidationError $errors "render.$integerName must be an integer from 1 through $integerMaximum."
        }
    }
    if ((Get-WzhkPropertyValue -Object $render -Name "highQualityNormals" -Default $false) -eq $true) {
        Add-WzhkValidationError $errors "render.highQualityNormals must be false because Blender 5.2 EEVEE exposes no matching RNA setting."
    }
    $dither = Get-WzhkPropertyValue -Object $render -Name "ditherIntensity"
    if (-not (Test-WzhkFiniteNumber $dither) -or [double]$dither -lt 0) { Add-WzhkValidationError $errors "render.ditherIntensity must be a non-negative finite number." }

    $compositor = Get-WzhkPropertyValue -Object $candidate -Name "compositor" -Default (Get-WzhkPropertyValue -Object $render -Name "compositor")
    $compositorEnabled = Get-WzhkPropertyValue -Object $compositor -Name "enabled" -Default $(if ($schema -eq "1.0.0") { Get-WzhkPropertyValue -Object $render -Name "useCompositing" } else { $null })
    $fogGlow = Get-WzhkPropertyValue -Object $compositor -Name "fogGlow" -Default $(if ($schema -eq "1.0.0") { $compositorEnabled } else { $null })
    $fogGlowEnabled = Get-WzhkPropertyValue -Object $compositor -Name "fogGlowEnabled" -Default $fogGlow
    if ($compositorEnabled -isnot [bool]) { Add-WzhkValidationError $errors "compositor.enabled must be boolean." }
    if ($compositorEnabled -is [bool] -and $compositorEnabled -ne (Get-WzhkPropertyValue -Object $render -Name "useCompositing")) { Add-WzhkValidationError $errors "compositor.enabled and render.useCompositing must agree." }
    if ($fogGlow -isnot [bool] -or $fogGlowEnabled -isnot [bool]) { Add-WzhkValidationError $errors "compositor.fogGlow and compositor.fogGlowEnabled must be boolean." }
    if ($fogGlow -is [bool] -and $fogGlowEnabled -is [bool] -and $fogGlow -ne $fogGlowEnabled) { Add-WzhkValidationError $errors "compositor.fogGlow and compositor.fogGlowEnabled must agree." }
    if ($compositorEnabled -eq $false -and $fogGlow -eq $true) { Add-WzhkValidationError $errors "compositor.fogGlow must be false when the compositor is disabled." }
    if ($schema -eq $script:ProfileSchemaVersion -and [string]::IsNullOrWhiteSpace([string](Get-WzhkPropertyValue -Object $compositor -Name "name" -Default ""))) { Add-WzhkValidationError $errors "compositor.name must be set." }
    $fogGlowQuality = Get-WzhkPropertyValue -Object $compositor -Name "fogGlowQuality" -Default $null
    if (($schema -eq $script:ProfileSchemaVersion -or $null -ne $fogGlowQuality) -and [string]$fogGlowQuality -notin @("LOW", "MEDIUM", "HIGH")) { Add-WzhkValidationError $errors "compositor.fogGlowQuality must be LOW, MEDIUM, or HIGH." }
    foreach ($numberConstraint in @(
        @("fogGlowThreshold", 0.0, 100.0),
        @("fogGlowStrength", 0.0, 1.0),
        @("fogGlowSize", 0.0, 1.0)
    )) {
        $numberName = [string]$numberConstraint[0]
        $numberMinimum = [double]$numberConstraint[1]
        $numberMaximum = [double]$numberConstraint[2]
        $numberValue = Get-WzhkPropertyValue -Object $compositor -Name $numberName -Default $null
        if (($schema -eq $script:ProfileSchemaVersion -or $null -ne $numberValue) -and (-not (Test-WzhkFiniteNumber $numberValue) -or [double]$numberValue -lt $numberMinimum -or [double]$numberValue -gt $numberMaximum)) {
            Add-WzhkValidationError $errors "compositor.$numberName must be a finite number from $numberMinimum through $numberMaximum."
        }
    }
    $fogGlowIterations = Get-WzhkPropertyValue -Object $compositor -Name "fogGlowIterations" -Default $null
    if (($schema -eq $script:ProfileSchemaVersion -or $null -ne $fogGlowIterations) -and ($fogGlowIterations -isnot [int] -or [int]$fogGlowIterations -lt 1 -or [int]$fogGlowIterations -gt 5)) { Add-WzhkValidationError $errors "compositor.fogGlowIterations must be an integer from 1 through 5." }

    $sequence = Get-WzhkPropertyValue -Object $candidate -Name "imageSequence"
    $format = ([string](Get-WzhkPropertyValue -Object $sequence -Name "format" -Default "")).ToUpperInvariant().Replace("OPENEXR", "OPEN_EXR")
    $extension = ([string](Get-WzhkPropertyValue -Object $sequence -Name "extension" -Default "")).TrimStart('.').ToLowerInvariant()
    $bitDepth = Get-WzhkPropertyValue -Object $sequence -Name "bitDepth"
    $expectedExtension = if ($format -eq "OPEN_EXR") { "exr" } else { "png" }
    if ($format -notin @("PNG", "OPEN_EXR")) { Add-WzhkValidationError $errors "imageSequence.format must be PNG or OPEN_EXR." }
    if ($extension -ne $expectedExtension) { Add-WzhkValidationError $errors "imageSequence.extension does not match its format." }
    if (($format -eq "PNG" -and $bitDepth -notin @(8, 16)) -or ($format -eq "OPEN_EXR" -and $bitDepth -ne 16)) { Add-WzhkValidationError $errors "imageSequence.bitDepth is invalid for the selected format." }
    $compression = Get-WzhkPropertyValue -Object $sequence -Name "compression"
    if ($format -eq "PNG" -and ($compression -isnot [int] -or [int]$compression -lt 0 -or [int]$compression -gt 100)) { Add-WzhkValidationError $errors "PNG compression must be an integer from 0 through 100." }
    if ($format -eq "OPEN_EXR" -and [string]$compression -notin @("ZIP", "PIZ")) { Add-WzhkValidationError $errors "OpenEXR compression must be ZIP or PIZ." }
    if ([string](Get-WzhkPropertyValue -Object $sequence -Name "colorMode" -Default "") -ne "RGB") { Add-WzhkValidationError $errors "imageSequence.colorMode must be RGB." }
    $pattern = [string](Get-WzhkPropertyValue -Object $sequence -Name "filenamePattern" -Default "")
    if ($pattern -ne [string]::Format("frame_%06d.{0}", $expectedExtension)) { Add-WzhkValidationError $errors "imageSequence.filenamePattern must use six-digit canonical frame names." }
    $displayBaked = Get-WzhkNestedValue -Object $sequence -Path @("colorManagement", "displayTransformBaked")
    if ($displayBaked -isnot [bool] -or (($format -eq "PNG") -ne [bool]$displayBaked)) { Add-WzhkValidationError $errors "PNG must bake the display transform and OpenEXR must remain scene-linear." }

    $color = Get-WzhkPropertyValue -Object $candidate -Name "colorManagement"
    foreach ($name in @("displayDevice", "viewTransform", "look", "sequencerColorSpace")) {
        if ([string]::IsNullOrWhiteSpace([string](Get-WzhkPropertyValue -Object $color -Name $name -Default ""))) { Add-WzhkValidationError $errors "colorManagement.$name must be set." }
    }
    foreach ($name in @("exposure", "gamma")) {
        if (-not (Test-WzhkFiniteNumber (Get-WzhkPropertyValue -Object $color -Name $name))) { Add-WzhkValidationError $errors "colorManagement.$name must be finite." }
    }
    $displayDevice = [string](Get-WzhkPropertyValue -Object $color -Name "displayDevice" -Default "")
    $viewTransform = [string](Get-WzhkPropertyValue -Object $color -Name "viewTransform" -Default "")
    $look = [string](Get-WzhkPropertyValue -Object $color -Name "look" -Default "")
    $sequencerSpace = [string](Get-WzhkPropertyValue -Object $color -Name "sequencerColorSpace" -Default "")
    if ($displayDevice -notin @("sRGB", "Display P3", "Rec.1886")) { Add-WzhkValidationError $errors "colorManagement.displayDevice is not available in the reviewed Blender 5.2 OCIO configuration." }
    if ($viewTransform -notin @("AgX", "Standard")) { Add-WzhkValidationError $errors "colorManagement.viewTransform is not available in the reviewed Blender 5.2 OCIO configuration." }
    if (($viewTransform -eq "AgX" -and $look -notin @("AgX - Medium High Contrast", "AgX - Medium Low Contrast", "None")) -or ($viewTransform -eq "Standard" -and $look -notin @("Medium High Contrast", "None"))) {
        Add-WzhkValidationError $errors "colorManagement.look is incompatible with the selected viewTransform."
    }
    if ($sequencerSpace -notin @("sRGB", "Linear Rec.709")) { Add-WzhkValidationError $errors "colorManagement.sequencerColorSpace is not an approved Blender 5.2 value." }
    $sourceColor = Get-WzhkPropertyValue -Object $candidate -Name "sourceColorManagement"
    if ($null -ne $sourceColor) {
        $colorChanged = $false
        foreach ($name in @("displayDevice", "viewTransform", "look", "sequencerColorSpace", "exposure", "gamma")) {
            if ([string](Get-WzhkPropertyValue -Object $color -Name $name -Default "") -cne [string](Get-WzhkPropertyValue -Object $sourceColor -Name $name -Default "")) {
                $colorChanged = $true
            }
        }
        if ($colorChanged) { $warnings.Add("Color-management settings differ from the approved scene defaults and require explicit visual review before authorization.") }
    }

    $chunking = Get-WzhkPropertyValue -Object $candidate -Name "chunking"
    $chunkSize = Get-WzhkPropertyValue -Object $chunking -Name "framesPerChunk" -Default (Get-WzhkNestedValue -Object $candidate -Path @("production", "framesPerChunk"))
    if ($chunkSize -isnot [int] -or [int]$chunkSize -lt 1 -or [int]$chunkSize -gt 1200 -or ($frameCount -gt 0 -and [int]$chunkSize -gt $frameCount)) { Add-WzhkValidationError $errors "chunking.framesPerChunk must fit the frame range and may not exceed 1200." }
    if ([string]::IsNullOrWhiteSpace([string](Get-WzhkPropertyValue -Object $chunking -Name "rationale" -Default ""))) { Add-WzhkValidationError $errors "chunking.rationale must be set." }
    $production = Get-WzhkPropertyValue -Object $candidate -Name "production"
    if ($schema -eq $script:ProfileSchemaVersion) {
        foreach ($name in @("resumeEnabled", "verifyExistingFrames", "overwriteInvalidFrames", "overwriteValidFrames", "atomicChunkCommit", "stopOnValidationFailure")) {
            if ((Get-WzhkPropertyValue -Object $production -Name $name) -isnot [bool]) { Add-WzhkValidationError $errors "production.$name must be boolean." }
        }
        if ((Get-WzhkPropertyValue -Object $production -Name "overwriteValidFrames") -eq $true) { Add-WzhkValidationError $errors "production.overwriteValidFrames must remain false." }
        foreach ($mandatoryTrue in @("resumeEnabled", "verifyExistingFrames", "atomicChunkCommit", "stopOnValidationFailure")) {
            if ((Get-WzhkPropertyValue -Object $production -Name $mandatoryTrue) -ne $true) {
                Add-WzhkValidationError $errors "production.$mandatoryTrue must remain true for the resumable atomic renderer contract."
            }
        }
    }

    $storage = Get-WzhkPropertyValue -Object $candidate -Name "storage"
    $sum = 0.0
    foreach ($name in @("plannedFrameSequenceGiB", "projectedMasterGiB", "projectedDeliveryGiB", "supportReserveGiB")) {
        $value = Get-WzhkPropertyValue -Object $storage -Name $name
        if (-not (Test-WzhkFiniteNumber $value) -or [double]$value -le 0) { Add-WzhkValidationError $errors "storage.$name must be greater than zero." } else { $sum += [double]$value }
    }
    $multiplier = Get-WzhkPropertyValue -Object $storage -Name "contingencyMultiplier"
    $minimumFree = Get-WzhkPropertyValue -Object $storage -Name "minimumLaunchFreeGiB"
    if (-not (Test-WzhkFiniteNumber $multiplier) -or [double]$multiplier -lt 1.0) { Add-WzhkValidationError $errors "storage.contingencyMultiplier must be at least 1.0." }
    if (-not (Test-WzhkFiniteNumber $minimumFree) -or [double]$minimumFree -lt ($sum * [double]$multiplier)) { Add-WzhkValidationError $errors "storage.minimumLaunchFreeGiB must cover all projected output plus contingency." }

    $output = Get-WzhkPropertyValue -Object $candidate -Name "output"
    $framesSubdirectory = [string](Get-WzhkPropertyValue -Object $output -Name "framesSubdirectory" -Default "frames")
    if ($framesSubdirectory -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' -or $framesSubdirectory -in @(".", "..")) {
        Add-WzhkValidationError $errors "output.framesSubdirectory must be one safe directory name."
    }
    if ($schema -eq $script:ProfileSchemaVersion) {
        $directoryPattern = [string](Get-WzhkPropertyValue -Object $output -Name "directoryPattern" -Default "")
        $directoryPatternValidation = Test-WzhkOutputDirectoryPattern -Pattern $directoryPattern
        if (-not $directoryPatternValidation.Valid) { Add-WzhkValidationError $errors $directoryPatternValidation.Message }
    }

    $encoding = Get-WzhkPropertyValue -Object $candidate -Name "encoding"
    $anyEncodingEnabled = $false
    foreach ($kind in @("master", "delivery")) {
        $settings = Get-WzhkPropertyValue -Object $encoding -Name $kind
        $enabled = Get-WzhkPropertyValue -Object $settings -Name "enabled" -Default $true
        if ($enabled -isnot [bool]) {
            Add-WzhkValidationError $errors "encoding.$kind.enabled must be boolean."
            continue
        }
        if (-not [bool]$enabled) { continue }
        $anyEncodingEnabled = $true
        foreach ($name in @("container", "fileExtension", "videoCodec", "pixelFormat", "audioCodec")) {
            if ([string]::IsNullOrWhiteSpace([string](Get-WzhkPropertyValue -Object $settings -Name $name -Default ""))) { Add-WzhkValidationError $errors "encoding.$kind.$name must be set." }
        }
        $filterField = if ($format -eq "OPEN_EXR") { "linearToDeliveryFilter" } else { "displayToDeliveryFilter" }
        if ([string]::IsNullOrWhiteSpace([string](Get-WzhkPropertyValue -Object $settings -Name $filterField -Default ""))) {
            Add-WzhkValidationError $errors "encoding.$kind.$filterField is required for the selected image sequence."
        }
    }
    if ($anyEncodingEnabled) {
        $audio = Get-WzhkPropertyValue -Object $candidate -Name "audio"
        $audioHash = [string](Get-WzhkPropertyValue -Object $audio -Name "sha256" -Default "")
        if (-not (Test-WzhkSha256Text $audioHash) -or $audioHash -eq ("0" * 64)) {
            Add-WzhkValidationError $errors "Enabled encoding requires an exact non-placeholder audio.sha256 identity."
        }
        $audioDuration = Get-WzhkPropertyValue -Object $audio -Name "durationSeconds"
        if (-not (Test-WzhkFiniteNumber $audioDuration) -or [double]$audioDuration -le 0.0) {
            Add-WzhkValidationError $errors "Enabled encoding requires a positive audio.durationSeconds value."
        }
    }

    $contentHash = ""
    try { $contentHash = Get-WzhkProfileContentHash -Profile $candidate } catch { Add-WzhkValidationError $errors "Profile could not be canonicalized for hashing." }
    $storedHash = [string](Get-WzhkPropertyValue -Object $candidate -Name "profileSha256" -Default (Get-WzhkNestedValue -Object $candidate -Path @("integrity", "profileSha256") -Default ""))
    if (-not [string]::IsNullOrWhiteSpace($storedHash) -and $storedHash.ToUpperInvariant() -ne $contentHash) { Add-WzhkValidationError $errors "Embedded profileSha256 does not match the canonical profile content." }

    return [pscustomobject]@{
        Valid = ($errors.Count -eq 0)
        Errors = $errors.ToArray()
        Warnings = $warnings.ToArray()
        ContentSha256 = $contentHash
        Profile = $candidate
    }
}

function Set-WzhkProfileAuthorizationPending {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [string]$Reason = "Profile content changed; prior authorization is invalid."
    )

    $copy = Copy-WzhkProfileValue -Value $Profile
    if ($null -eq $copy.PSObject.Properties["authorization"]) {
        Add-Member -InputObject $copy -NotePropertyName authorization -NotePropertyValue ([pscustomobject][ordered]@{})
    }
    foreach ($pair in @{
        status = "pending-operator-approval"
        project = ([string](Get-WzhkPropertyValue -Object $copy -Name "project" -Default "TRACKPROMPT")).ToUpperInvariant()
        preset = ([string](Get-WzhkPropertyValue -Object $copy -Name "preset" -Default "CUSTOM")).ToUpperInvariant()
        profile = ([string](Get-WzhkPropertyValue -Object $copy -Name "profileId" -Default "PROFILE")).ToUpperInvariant()
        sceneSha256 = ([string](Get-WzhkPropertyValue -Object $copy -Name "approvedSceneSha256" -Default "")).ToUpperInvariant()
        invalidatedAt = (Get-WzhkUtcTimestamp)
        reason = $Reason
    }.GetEnumerator()) {
        if ($null -eq $copy.authorization.PSObject.Properties[$pair.Key]) {
            Add-Member -InputObject $copy.authorization -NotePropertyName $pair.Key -NotePropertyValue $pair.Value
        }
        else { $copy.authorization.($pair.Key) = $pair.Value }
    }
    $copy.profileSha256 = ""
    $copy.integrity.profileSha256 = ""
    return $copy
}

function Set-WzhkProfileValue {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][string]$PropertyPath,
        [AllowNull()][object]$Value
    )

    $segments = @($PropertyPath.Split('.') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($segments.Count -eq 0) { throw "PropertyPath must contain at least one property name." }
    $copy = Copy-WzhkProfileValue -Value $Profile
    $current = $copy
    for ($index = 0; $index -lt ($segments.Count - 1); $index++) {
        $name = $segments[$index]
        $property = $current.PSObject.Properties[$name]
        if ($null -eq $property -or $null -eq $property.Value) {
            $child = [pscustomobject][ordered]@{}
            if ($null -eq $property) { Add-Member -InputObject $current -NotePropertyName $name -NotePropertyValue $child }
            else { $property.Value = $child }
            $current = $child
        }
        else { $current = $property.Value }
    }
    $leaf = $segments[-1]
    if ($null -eq $current.PSObject.Properties[$leaf]) { Add-Member -InputObject $current -NotePropertyName $leaf -NotePropertyValue $Value }
    else { $current.$leaf = $Value }

    $timestamp = Get-WzhkUtcTimestamp
    if ($null -ne $copy.PSObject.Properties["updatedAt"]) { $copy.updatedAt = $timestamp }
    if ($null -ne $copy.PSObject.Properties["timestamps"]) { $copy.timestamps.updatedAt = $timestamp }
    return Set-WzhkProfileAuthorizationPending -Profile $copy
}

function Write-WzhkAtomicText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text,
        [switch]$Utf8Bom
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    $directory = [IO.Path]::GetDirectoryName($fullPath)
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { $null = New-Item -ItemType Directory -Path $directory }
    $temporary = Join-Path $directory ([string]::Format(".{0}.{1}.tmp", [IO.Path]::GetFileName($fullPath), [Guid]::NewGuid().ToString("N")))
    $encoding = New-Object Text.UTF8Encoding([bool]$Utf8Bom)
    try {
        [IO.File]::WriteAllText($temporary, $Text, $encoding)
        if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
            $backup = Join-Path $directory ([string]::Format(".{0}.{1}.bak", [IO.Path]::GetFileName($fullPath), [Guid]::NewGuid().ToString("N")))
            try { [IO.File]::Replace($temporary, $fullPath, $backup, $true) }
            finally { if (Test-Path -LiteralPath $backup -PathType Leaf) { Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue } }
        }
        else { [IO.File]::Move($temporary, $fullPath) }
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
    }
    return $fullPath
}

function Get-WzhkProfileSummaryText {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$FileSha256
    )

    $resolution = Get-WzhkPropertyValue -Object $Profile -Name "resolution"
    $timeline = Get-WzhkPropertyValue -Object $Profile -Name "timeline" -Default $Profile
    $image = Get-WzhkPropertyValue -Object $Profile -Name "imageSequence"
    $render = Get-WzhkPropertyValue -Object $Profile -Name "render"
    $chunking = Get-WzhkPropertyValue -Object $Profile -Name "chunking"
    return ((@(
        "WZHK MEDIA RENDER PROFILE",
        "=========================",
        "Name: " + [string](Get-WzhkPropertyValue -Object $Profile -Name "displayName"),
        "Stable ID: " + [string](Get-WzhkPropertyValue -Object $Profile -Name "id"),
        "Renderer profile ID: " + [string](Get-WzhkPropertyValue -Object $Profile -Name "profileId"),
        "Schema: " + [string](Get-WzhkPropertyValue -Object $Profile -Name "schemaVersion"),
        "Project / preset: " + [string](Get-WzhkPropertyValue -Object $Profile -Name "project") + " / " + [string](Get-WzhkPropertyValue -Object $Profile -Name "preset"),
        "Resolution: " + [string](Get-WzhkPropertyValue -Object $resolution -Name "width") + "x" + [string](Get-WzhkPropertyValue -Object $resolution -Name "height") + " @ 100%",
        "Timeline: " + [string](Get-WzhkPropertyValue -Object $timeline -Name "frameStart") + "-" + [string](Get-WzhkPropertyValue -Object $timeline -Name "frameEnd") + " @ " + [string](Get-WzhkPropertyValue -Object $timeline -Name "fps") + " fps",
        "Engine / samples: " + [string](Get-WzhkPropertyValue -Object $render -Name "engine") + " / " + [string](Get-WzhkPropertyValue -Object $render -Name "samples"),
        "Sequence: " + [string](Get-WzhkPropertyValue -Object $image -Name "format") + " " + [string](Get-WzhkPropertyValue -Object $image -Name "bitDepth") + "-bit RGB",
        "Chunk size: " + [string](Get-WzhkPropertyValue -Object $chunking -Name "framesPerChunk"),
        "Scene SHA-256: " + [string](Get-WzhkPropertyValue -Object $Profile -Name "approvedSceneSha256"),
        "Canonical content SHA-256: " + [string](Get-WzhkPropertyValue -Object $Profile -Name "profileSha256"),
        "Saved-file SHA-256: " + $FileSha256,
        "Authorization: " + [string](Get-WzhkNestedValue -Object $Profile -Path @("authorization", "status") -Default "pending"),
        "Path: " + $ProfilePath,
        "",
        "Production authorization is bound to the exact scene SHA-256 and saved-file SHA-256."
    ) -join "`r`n") + "`r`n")
}

function Save-WzhkRenderProfile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$Force
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    if ([IO.Path]::GetExtension($fullPath).ToLowerInvariant() -ne ".json") { throw "Render profile path must end in .json." }
    $existed = Test-Path -LiteralPath $fullPath -PathType Leaf
    if ($existed -and -not $Force) { throw "Render profile already exists; use -Force to edit it atomically." }

    $candidate = Normalize-WzhkRenderProfile -Profile $Profile
    $targetDirectory = [IO.Path]::GetDirectoryName($fullPath)
    if (Test-Path -LiteralPath $targetDirectory -PathType Container) {
        $candidateStableId = [string](Get-WzhkPropertyValue -Object $candidate -Name "id" -Default "")
        $candidateProfileId = [string](Get-WzhkPropertyValue -Object $candidate -Name "profileId" -Default "")
        foreach ($sibling in Get-ChildItem -LiteralPath $targetDirectory -File -Filter "*.json" -ErrorAction SilentlyContinue) {
            if ($sibling.FullName -ieq $fullPath -or $sibling.Name -match '\.authorization(-request)?\.json$') { continue }
            try { $siblingProfile = Get-Content -LiteralPath $sibling.FullName -Raw | ConvertFrom-Json -ErrorAction Stop }
            catch { continue }
            if ([string](Get-WzhkPropertyValue -Object $siblingProfile -Name "kind" -Default "") -eq "trackprompt-recommended-render-profile-pointer") { continue }
            $siblingStableId = [string](Get-WzhkPropertyValue -Object $siblingProfile -Name "id" -Default "")
            $siblingProfileId = [string](Get-WzhkPropertyValue -Object $siblingProfile -Name "profileId" -Default "")
            if (
                (-not [string]::IsNullOrWhiteSpace($candidateStableId) -and $candidateStableId -ieq $siblingStableId) -or
                (-not [string]::IsNullOrWhiteSpace($candidateProfileId) -and $candidateProfileId -ieq $siblingProfileId)
            ) {
                throw ("Another saved profile in this project already uses the same stable ID or profileId: " + $sibling.FullName)
            }
        }
    }
    $existingContentHash = ""
    if ($existed) {
        try {
            $existing = Get-Content -LiteralPath $fullPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $existingContentHash = Get-WzhkProfileContentHash -Profile $existing
        }
        catch { throw "Existing profile cannot be safely replaced because it is unreadable or invalid JSON." }
    }

    $candidate.profileSha256 = ""
    $candidate.integrity.profileSha256 = ""
    $validation = Test-WzhkRenderProfile -Profile $candidate
    $candidate.validation.status = if ($validation.Valid) { "valid" } else { "invalid" }
    $candidate.validation.errors = @($validation.Errors)
    $candidate.warnings = @($validation.Warnings)
    $prospectiveHash = Get-WzhkProfileContentHash -Profile $candidate

    $authorizationInvalidated = $false
    if ($existed -and $existingContentHash -ne $prospectiveHash) {
        $candidate = Set-WzhkProfileAuthorizationPending -Profile $candidate
        $timestamp = Get-WzhkUtcTimestamp
        $candidate.updatedAt = $timestamp
        $candidate.timestamps.updatedAt = $timestamp
        $authorizationInvalidated = $true
    }

    $candidate.profileSha256 = ""
    $candidate.integrity.profileSha256 = ""
    $validation = Test-WzhkRenderProfile -Profile $candidate
    $candidate.validation.status = if ($validation.Valid) { "valid" } else { "invalid" }
    $candidate.validation.errors = @($validation.Errors)
    $candidate.warnings = @($validation.Warnings)
    if (-not $validation.Valid) { throw ("Render profile validation failed: " + ($validation.Errors -join " ")) }
    # Stabilize across PowerShell 5.1 JSON type coercion (for example 30.0
    # becoming 30, or a single-element array being materialized differently).
    $json = ""
    $contentHash = ""
    $stabilized = $false
    for ($attempt = 0; $attempt -lt 4; $attempt++) {
        $candidate.profileSha256 = ""
        $candidate.integrity.profileSha256 = ""
        $withoutHashJson = (ConvertTo-WzhkCanonicalNode -Value $candidate | ConvertTo-Json -Depth 100)
        $candidate = $withoutHashJson | ConvertFrom-Json
        $contentHash = Get-WzhkProfileContentHash -Profile $candidate
        $candidate.profileSha256 = $contentHash
        $candidate.integrity.profileSha256 = $contentHash
        $json = (ConvertTo-WzhkCanonicalNode -Value $candidate | ConvertTo-Json -Depth 100) + "`n"
        $readBack = $json | ConvertFrom-Json
        $readBackHash = Get-WzhkProfileContentHash -Profile $readBack
        if ($readBackHash -eq $contentHash) {
            $candidate = $readBack
            $stabilized = $true
            break
        }
        $candidate = $readBack
    }
    if (-not $stabilized) { throw "Profile JSON canonical hash did not stabilize under PowerShell 5.1 serialization." }
    $directory = [IO.Path]::GetDirectoryName($fullPath)
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { $null = New-Item -ItemType Directory -Path $directory }
    $temporary = Join-Path $directory ([string]::Format(".{0}.{1}.tmp", [IO.Path]::GetFileName($fullPath), [Guid]::NewGuid().ToString("N")))
    try {
        [IO.File]::WriteAllText($temporary, $json, (New-Object Text.UTF8Encoding($false)))
        $directReadBack = Get-Content -LiteralPath $temporary -Raw -Encoding UTF8 | ConvertFrom-Json
        $directReadBackHash = Get-WzhkProfileContentHash -Profile $directReadBack
        $temporaryValidation = Test-WzhkRenderProfile -Path $temporary
        if (-not $temporaryValidation.Valid) {
            throw ("Atomic save candidate failed revalidation: " + ($temporaryValidation.Errors -join " ") + " Expected canonical hash " + $candidate.profileSha256 + "; direct read-back hash " + $directReadBackHash + "; validator read-back hash " + $temporaryValidation.ContentSha256 + ".")
        }
        if ($existed) {
            $backup = Join-Path $directory ([string]::Format(".{0}.{1}.bak", [IO.Path]::GetFileName($fullPath), [Guid]::NewGuid().ToString("N")))
            try { [IO.File]::Replace($temporary, $fullPath, $backup, $true) }
            finally { if (Test-Path -LiteralPath $backup -PathType Leaf) { Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue } }
        }
        else { [IO.File]::Move($temporary, $fullPath) }
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
    }

    $fileHash = Get-WzhkFileSha256 -Path $fullPath
    $summaryPath = [IO.Path]::ChangeExtension($fullPath, ".summary.txt")
    $summary = Get-WzhkProfileSummaryText -Profile $candidate -ProfilePath $fullPath -FileSha256 $fileHash
    $null = Write-WzhkAtomicText -Path $summaryPath -Text $summary -Utf8Bom
    return [pscustomobject]@{
        Path = $fullPath
        SummaryPath = $summaryPath
        Profile = $candidate
        ContentSha256 = $contentHash
        FileSha256 = $fileHash
        AuthorizationInvalidated = $authorizationInvalidated
        Atomic = $true
    }
}

function Import-WzhkRenderProfile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$Normalize,
        [switch]$VerifyFiles
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    $validation = Test-WzhkRenderProfile -Path $fullPath -VerifyFiles:$VerifyFiles
    if (-not $validation.Valid) { throw ("Render profile is invalid: " + ($validation.Errors -join " ")) }
    $profile = $validation.Profile
    if ($Normalize -or [string](Get-WzhkPropertyValue -Object $profile -Name "schemaVersion") -eq "1.0.0") { return Normalize-WzhkRenderProfile -Profile $profile }
    return $profile
}

function Resolve-WzhkRecommendedProfilePointer {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $pointerPath = [IO.Path]::GetFullPath($Path)
    $issues = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        return [pscustomobject]@{ Valid = $false; Path = $pointerPath; ProfilePath = ""; Profile = $null; Issues = @("Recommended-profile pointer does not exist.") }
    }
    try { $pointer = Get-Content -LiteralPath $pointerPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop }
    catch { return [pscustomobject]@{ Valid = $false; Path = $pointerPath; ProfilePath = ""; Profile = $null; Issues = @("Recommended-profile pointer is not valid JSON.") } }

    if ([string](Get-WzhkPropertyValue -Object $pointer -Name "kind" -Default "") -ne "trackprompt-recommended-render-profile-pointer") {
        $issues.Add("File is not a TrackPrompt recommended-profile pointer.")
    }
    $targetText = [string](Get-WzhkPropertyValue -Object $pointer -Name "profilePath" -Default "")
    $targetPath = ""
    if ([string]::IsNullOrWhiteSpace($targetText)) {
        $issues.Add("Pointer profilePath is missing.")
    }
    else {
        $pointerDirectory = [IO.Path]::GetDirectoryName($pointerPath)
        $targetPath = if ([IO.Path]::IsPathRooted($targetText)) { [IO.Path]::GetFullPath($targetText) } else { [IO.Path]::GetFullPath((Join-Path $pointerDirectory $targetText)) }
        $allowedPrefix = $pointerDirectory.TrimEnd('\') + '\'
        if (-not $targetPath.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            $issues.Add("Pointer target must remain inside the pointer directory.")
        }
        elseif (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
            $issues.Add("Pointer target profile does not exist.")
        }
    }

    $profile = $null
    if ($issues.Count -eq 0) {
        $expectedHash = [string](Get-WzhkPropertyValue -Object $pointer -Name "profileSha256" -Default "")
        $actualHash = Get-WzhkFileSha256 -Path $targetPath
        if ($expectedHash -notmatch '^[A-Fa-f0-9]{64}$' -or $expectedHash -ine $actualHash) { $issues.Add("Pointer target saved-file SHA-256 does not match.") }
        try { $profile = Import-WzhkRenderProfile -Path $targetPath -VerifyFiles }
        catch { $issues.Add($_.Exception.Message) }
        if ($null -ne $profile) {
            $pointerProfileId = [string](Get-WzhkPropertyValue -Object $pointer -Name "profileId" -Default "")
            $actualProfileId = [string](Get-WzhkPropertyValue -Object $profile -Name "profileId" -Default "")
            if ([string]::IsNullOrWhiteSpace($pointerProfileId) -or $pointerProfileId -ine $actualProfileId) { $issues.Add("Pointer profile ID does not match the saved profile.") }
            $pointerScene = [string](Get-WzhkPropertyValue -Object $pointer -Name "sceneSha256" -Default "")
            $actualScene = [string](Get-WzhkPropertyValue -Object $profile -Name "approvedSceneSha256" -Default (Get-WzhkNestedValue -Object $profile -Path @("approvedScene", "sha256") -Default ""))
            if ($pointerScene -notmatch '^[A-Fa-f0-9]{64}$' -or $pointerScene -ine $actualScene) { $issues.Add("Pointer scene SHA-256 does not match the saved profile.") }
        }
    }
    return [pscustomobject][ordered]@{
        Valid = ($issues.Count -eq 0)
        Path = $pointerPath
        ProfilePath = $targetPath
        Profile = $profile
        Issues = $issues.ToArray()
    }
}

function Get-WzhkSavedRenderProfiles {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [switch]$Recurse
    )

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { return @() }
    $profiles = New-Object System.Collections.Generic.List[object]
    $files = if ($Recurse) {
        Get-ChildItem -LiteralPath $Directory -Recurse -File -Filter "*.json" -ErrorAction SilentlyContinue
    }
    else {
        Get-ChildItem -LiteralPath $Directory -File -Filter "*.json" -ErrorAction SilentlyContinue
    }
    foreach ($file in $files | Sort-Object FullName) {
        if ($file.Name -match '\.authorization(-request)?\.json$') { continue }
        $validation = Test-WzhkRenderProfile -Path $file.FullName
        if ($null -eq $validation.Profile) { continue }
        $profile = $validation.Profile
        if ([string](Get-WzhkPropertyValue -Object $profile -Name "kind" -Default "") -eq "trackprompt-recommended-render-profile-pointer") { continue }
        $profileSchema = [string](Get-WzhkPropertyValue -Object $profile -Name "schemaVersion" -Default "")
        if ($profileSchema -notin @("1.0.0", $script:ProfileSchemaVersion) -or [string]::IsNullOrWhiteSpace([string](Get-WzhkPropertyValue -Object $profile -Name "profileId" -Default ""))) { continue }
        $authorizationRecordPath = [IO.Path]::Combine($file.DirectoryName, ($file.BaseName + ".authorization.json"))
        $authorizationRecordValid = $false
        $scenePathForRecord = [string](Get-WzhkPropertyValue -Object $profile -Name "approvedScenePath" -Default (Get-WzhkNestedValue -Object $profile -Path @("approvedScene", "path") -Default ""))
        if ((Test-Path -LiteralPath $authorizationRecordPath -PathType Leaf) -and (Test-Path -LiteralPath $scenePathForRecord -PathType Leaf)) {
            try { $authorizationRecordValid = (Test-WzhkProfileAuthorizationRecord -ProfilePath $file.FullName -ScenePath $scenePathForRecord -RecordPath $authorizationRecordPath).Valid } catch { $authorizationRecordValid = $false }
        }
        $profiles.Add([pscustomobject]@{
            Path = $file.FullName
            Name = [string](Get-WzhkPropertyValue -Object $profile -Name "displayName" -Default $file.BaseName)
            Id = [string](Get-WzhkPropertyValue -Object $profile -Name "id" -Default (Get-WzhkPropertyValue -Object $profile -Name "profileId" -Default $file.BaseName))
            ProfileId = [string](Get-WzhkPropertyValue -Object $profile -Name "profileId" -Default $file.BaseName)
            SchemaVersion = [string](Get-WzhkPropertyValue -Object $profile -Name "schemaVersion" -Default "unknown")
            Valid = $validation.Valid
            Errors = @($validation.Errors)
            Width = Get-WzhkNestedValue -Object $profile -Path @("resolution", "width")
            Height = Get-WzhkNestedValue -Object $profile -Path @("resolution", "height")
            Fps = Get-WzhkNestedValue -Object $profile -Path @("timeline", "fps") -Default (Get-WzhkPropertyValue -Object $profile -Name "fps")
            SceneSha256 = [string](Get-WzhkPropertyValue -Object $profile -Name "approvedSceneSha256" -Default (Get-WzhkNestedValue -Object $profile -Path @("approvedScene", "sha256") -Default ""))
            ContentSha256 = [string](Get-WzhkPropertyValue -Object $profile -Name "profileSha256" -Default (Get-WzhkNestedValue -Object $profile -Path @("integrity", "profileSha256") -Default ""))
            AuthorizationStatus = [string](Get-WzhkNestedValue -Object $profile -Path @("authorization", "status") -Default "unknown")
            AuthorizationRecordPath = $(if (Test-Path -LiteralPath $authorizationRecordPath -PathType Leaf) { $authorizationRecordPath } else { "" })
            AuthorizationRecordValid = $authorizationRecordValid
            ImageFormat = [string](Get-WzhkNestedValue -Object $profile -Path @("imageSequence", "format") -Default "unknown")
            BitDepth = Get-WzhkNestedValue -Object $profile -Path @("imageSequence", "bitDepth")
            ChunkSize = Get-WzhkNestedValue -Object $profile -Path @("chunking", "framesPerChunk") -Default (Get-WzhkNestedValue -Object $profile -Path @("production", "framesPerChunk"))
            Samples = Get-WzhkNestedValue -Object $profile -Path @("render", "samples")
            QualityMode = [string](Get-WzhkNestedValue -Object $profile -Path @("render", "qualityMode") -Default (Get-WzhkNestedValue -Object $profile -Path @("quality", "mode") -Default (Get-WzhkPropertyValue -Object $profile -Name "templateId" -Default "custom")))
            OutputRoot = [string](Get-WzhkNestedValue -Object $profile -Path @("output", "rootDirectory") -Default "")
            OutputPolicy = [string](Get-WzhkNestedValue -Object $profile -Path @("output", "policy") -Default "create-new")
            UpdatedAt = [string](Get-WzhkPropertyValue -Object $profile -Name "updatedAt" -Default $file.LastWriteTimeUtc.ToString("o"))
            FileSha256 = Get-WzhkFileSha256 -Path $file.FullName
        })
    }
    return @($profiles | Sort-Object UpdatedAt -Descending)
}

function Copy-WzhkRenderProfile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][string]$NewDisplayName,
        [string]$Path = "",
        [switch]$Force
    )

    $copy = Normalize-WzhkRenderProfile -Profile $Profile
    $timestamp = Get-WzhkUtcTimestamp
    $stableId = ([Guid]::NewGuid().ToString("D")).ToUpperInvariant()
    $copy.id = $stableId
    $copy.profileId = "RP-" + $stableId.Substring(0, 12)
    $copy.displayName = ConvertTo-WzhkSafeProfileName -Name $NewDisplayName
    $copy.createdAt = $timestamp
    $copy.updatedAt = $timestamp
    $copy.timestamps.createdAt = $timestamp
    $copy.timestamps.updatedAt = $timestamp
    $copy.authorization.profile = $copy.profileId.ToUpperInvariant()
    $copy = Set-WzhkProfileAuthorizationPending -Profile $copy -Reason "Duplicated profile requires independent authorization."
    $copy.authorization.invalidatedAt = $null
    $copy.profileSha256 = ""
    $copy.integrity.profileSha256 = ""
    $hash = Get-WzhkProfileContentHash -Profile $copy
    $copy.profileSha256 = $hash
    $copy.integrity.profileSha256 = $hash
    if (-not [string]::IsNullOrWhiteSpace($Path)) { return Save-WzhkRenderProfile -Profile $copy -Path $Path -Force:$Force }
    return $copy
}

function Rename-WzhkRenderProfile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [Parameter(Mandatory = $true)][string]$NewDisplayName
    )
    return Set-WzhkProfileValue -Profile $Profile -PropertyPath "displayName" -Value (ConvertTo-WzhkSafeProfileName -Name $NewDisplayName)
}

function Remove-WzhkRenderProfile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$ConfirmDeletion
    )

    if (-not $ConfirmDeletion) { throw "Profile deletion requires -ConfirmDeletion." }
    $fullPath = [IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { return $false }
    $base = [IO.Path]::Combine([IO.Path]::GetDirectoryName($fullPath), [IO.Path]::GetFileNameWithoutExtension($fullPath))
    foreach ($candidate in @($fullPath, ($base + ".summary.txt"), ($base + ".authorization-request.json"), ($base + ".authorization.json"))) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { Remove-Item -LiteralPath $candidate -Force }
    }
    return $true
}

function Get-WzhkProfileAuthorizationToken {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$ScenePath
    )

    $profilePathFull = [IO.Path]::GetFullPath($ProfilePath)
    $scenePathFull = [IO.Path]::GetFullPath($ScenePath)
    $profile = Import-WzhkRenderProfile -Path $profilePathFull
    if (-not (Test-Path -LiteralPath $scenePathFull -PathType Leaf)) { throw "Approved scene does not exist." }
    $sceneHash = Get-WzhkFileSha256 -Path $scenePathFull
    $expectedSceneHash = ([string](Get-WzhkPropertyValue -Object $profile -Name "approvedSceneSha256" -Default (Get-WzhkNestedValue -Object $profile -Path @("approvedScene", "sha256") -Default ""))).ToUpperInvariant()
    if ($sceneHash -ne $expectedSceneHash) { throw "Approved scene hash does not match the saved profile." }
    $profileHash = Get-WzhkFileSha256 -Path $profilePathFull
    $authorization = Get-WzhkPropertyValue -Object $profile -Name "authorization"
    $project = ([string](Get-WzhkPropertyValue -Object $authorization -Name "project" -Default (Get-WzhkPropertyValue -Object $profile -Name "project" -Default "TRACKPROMPT"))).ToUpperInvariant()
    $preset = ([string](Get-WzhkPropertyValue -Object $authorization -Name "preset" -Default (Get-WzhkPropertyValue -Object $profile -Name "preset" -Default "CUSTOM"))).ToUpperInvariant()
    $profileLabel = ([string](Get-WzhkPropertyValue -Object $authorization -Name "profile" -Default (Get-WzhkPropertyValue -Object $profile -Name "profileId" -Default "PROFILE"))).ToUpperInvariant()
    return [string]::Format(
        "AUTHORIZE FULL RENDER: {0} | {1} | {2} | SCENE {3} | PROFILE {4}",
        $project,
        $preset,
        $profileLabel,
        $sceneHash.Substring(0, 12),
        $profileHash.Substring(0, 12)
    )
}

function Get-WzhkStringSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace("-", "").ToUpperInvariant()
    }
    finally { $sha.Dispose() }
}

function New-WzhkProfileAuthorizationRequest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [string]$Path = ""
    )

    $profileFull = [IO.Path]::GetFullPath($ProfilePath)
    $sceneFull = [IO.Path]::GetFullPath($ScenePath)
    $profile = Import-WzhkRenderProfile -Path $profileFull -VerifyFiles
    $token = Get-WzhkProfileAuthorizationToken -ProfilePath $profileFull -ScenePath $sceneFull
    $profileHash = Get-WzhkFileSha256 -Path $profileFull
    $sceneHash = Get-WzhkFileSha256 -Path $sceneFull
    $request = [pscustomobject][ordered]@{
        schemaVersion = $script:ProfileSchemaVersion
        kind = $script:AuthorizationRequestKind
        status = "pending-two-confirmations"
        requestedAt = Get-WzhkUtcTimestamp
        profile = [ordered]@{
            id = [string](Get-WzhkPropertyValue -Object $profile -Name "id" -Default "")
            profileId = [string](Get-WzhkPropertyValue -Object $profile -Name "profileId")
            displayName = [string](Get-WzhkPropertyValue -Object $profile -Name "displayName" -Default (Get-WzhkPropertyValue -Object $profile -Name "profileId"))
            path = $profileFull
            sha256 = $profileHash
        }
        scene = [ordered]@{ path = $sceneFull; sha256 = $sceneHash }
        tokenSha256 = Get-WzhkStringSha256 -Text $token
        tokenPreview = [string]::Format("SCENE {0} | PROFILE {1}", $sceneHash.Substring(0, 12), $profileHash.Substring(0, 12))
        confirmations = [ordered]@{
            settingsAndHashesReviewed = $false
            productionRenderAuthorized = $false
        }
        note = "The plaintext authorization token is intentionally absent until both explicit confirmations are recorded."
    }

    $savedPath = ""
    if (-not [string]::IsNullOrWhiteSpace($Path)) {
        $requestJson = (ConvertTo-WzhkCanonicalNode -Value $request | ConvertTo-Json -Depth 100) + "`n"
        $savedPath = Write-WzhkAtomicText -Path $Path -Text $requestJson
    }
    return [pscustomobject]@{ Request = $request; AuthorizationToken = $token; Path = $savedPath }
}

function New-WzhkProfileAuthorizationRecord {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [switch]$SettingsAndHashesReviewed,
        [switch]$ProductionRenderAuthorized,
        [string]$Path = ""
    )

    if (-not $SettingsAndHashesReviewed -or -not $ProductionRenderAuthorized) {
        throw "Authorization requires both explicit confirmations: settings/hashes reviewed and production render authorized."
    }
    $profileFull = [IO.Path]::GetFullPath($ProfilePath)
    $sceneFull = [IO.Path]::GetFullPath($ScenePath)
    $profile = Import-WzhkRenderProfile -Path $profileFull -VerifyFiles
    $token = Get-WzhkProfileAuthorizationToken -ProfilePath $profileFull -ScenePath $sceneFull
    $profileHash = Get-WzhkFileSha256 -Path $profileFull
    $sceneHash = Get-WzhkFileSha256 -Path $sceneFull
    $record = [pscustomobject][ordered]@{
        schemaVersion = $script:ProfileSchemaVersion
        kind = $script:AuthorizationKind
        status = "authorized"
        authorizedAt = Get-WzhkUtcTimestamp
        profile = [ordered]@{
            id = [string](Get-WzhkPropertyValue -Object $profile -Name "id" -Default "")
            profileId = [string](Get-WzhkPropertyValue -Object $profile -Name "profileId")
            path = $profileFull
            sha256 = $profileHash
        }
        scene = [ordered]@{ path = $sceneFull; sha256 = $sceneHash }
        confirmations = [ordered]@{
            settingsAndHashesReviewed = $true
            productionRenderAuthorized = $true
        }
        authorizationToken = $token
        tokenSha256 = Get-WzhkStringSha256 -Text $token
    }
    if ([string]::IsNullOrWhiteSpace($Path)) {
        $base = [IO.Path]::Combine([IO.Path]::GetDirectoryName($profileFull), [IO.Path]::GetFileNameWithoutExtension($profileFull))
        $Path = $base + ".authorization.json"
    }
    $recordJson = (ConvertTo-WzhkCanonicalNode -Value $record | ConvertTo-Json -Depth 100) + "`n"
    $savedPath = Write-WzhkAtomicText -Path $Path -Text $recordJson
    return [pscustomobject]@{ Record = $record; AuthorizationToken = $token; Path = $savedPath }
}

function Test-WzhkProfileAuthorizationRecord {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [Parameter(Mandatory = $true)][string]$RecordPath
    )

    $issues = New-Object System.Collections.Generic.List[string]
    try { $record = Get-Content -LiteralPath $RecordPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop }
    catch { return [pscustomobject]@{ Valid = $false; Issues = @("Authorization record is unreadable or invalid JSON."); AuthorizationToken = "" } }
    $profileHash = Get-WzhkFileSha256 -Path $ProfilePath
    $sceneHash = Get-WzhkFileSha256 -Path $ScenePath
    $token = Get-WzhkProfileAuthorizationToken -ProfilePath $ProfilePath -ScenePath $ScenePath
    if ([string](Get-WzhkPropertyValue -Object $record -Name "kind" -Default "") -ne $script:AuthorizationKind) { $issues.Add("Authorization record kind is unsupported.") }
    if ([string](Get-WzhkPropertyValue -Object $record -Name "status" -Default "") -ne "authorized") { $issues.Add("Authorization record is not authorized.") }
    if ([string](Get-WzhkNestedValue -Object $record -Path @("profile", "sha256") -Default "") -ne $profileHash) { $issues.Add("Authorization record profile hash does not match the current saved file.") }
    if ([string](Get-WzhkNestedValue -Object $record -Path @("scene", "sha256") -Default "") -ne $sceneHash) { $issues.Add("Authorization record scene hash does not match the current scene.") }
    if ((Get-WzhkNestedValue -Object $record -Path @("confirmations", "settingsAndHashesReviewed") -Default $false) -ne $true -or (Get-WzhkNestedValue -Object $record -Path @("confirmations", "productionRenderAuthorized") -Default $false) -ne $true) { $issues.Add("Authorization record does not contain both confirmations.") }
    if ([string](Get-WzhkPropertyValue -Object $record -Name "authorizationToken" -Default "") -cne $token) { $issues.Add("Authorization record token does not match the exact scene and profile.") }
    if ([string](Get-WzhkPropertyValue -Object $record -Name "tokenSha256" -Default "") -ne (Get-WzhkStringSha256 -Text $token)) { $issues.Add("Authorization record token hash is invalid.") }
    return [pscustomobject]@{ Valid = ($issues.Count -eq 0); Issues = $issues.ToArray(); AuthorizationToken = $(if ($issues.Count -eq 0) { $token } else { "" }) }
}

function Get-WzhkOutputDirectoryInspection {
    param([Parameter(Mandatory = $true)][string]$OutputPath)

    $full = [IO.Path]::GetFullPath($OutputPath).TrimEnd('\')
    if (-not (Test-Path -LiteralPath $full)) {
        return [pscustomobject][ordered]@{ Path = $full; Classification = "new-output"; Exists = $false; CompatibleInitialized = $false; Entries = @(); ConflictingEntries = @() }
    }
    if (-not (Test-Path -LiteralPath $full -PathType Container)) {
        return [pscustomobject][ordered]@{ Path = $full; Classification = "not-a-directory"; Exists = $true; CompatibleInitialized = $false; Entries = @(); ConflictingEntries = @([IO.Path]::GetFileName($full)) }
    }
    $entries = @(
        Get-ChildItem -LiteralPath $full -Force -ErrorAction Stop |
            Sort-Object Name |
            ForEach-Object {
                $flags = New-Object System.Collections.Generic.List[string]
                if ($_.PSIsContainer) { $flags.Add("directory") } else { $flags.Add("file") }
                if (($_.Attributes -band [IO.FileAttributes]::Hidden) -ne 0) { $flags.Add("hidden") }
                if (($_.Attributes -band [IO.FileAttributes]::System) -ne 0) { $flags.Add("system") }
                if (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { $flags.Add("reparse-point") }
                [pscustomobject]@{ Name = $_.Name; FullName = $_.FullName; Type = $(if ($_.PSIsContainer) { "directory" } else { "file" }); Attributes = $flags.ToArray() }
            }
    )
    $manifestPath = Join-Path $full "manifests\render-manifest.json"
    $classification = if ($entries.Count -eq 0) {
        "truly-empty-directory"
    }
    elseif (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        "initialized-render-directory"
    }
    elseif (@($entries | Where-Object { $_.Type -eq "file" }).Count -eq 0) {
        "parent-directory-with-child-folders"
    }
    else {
        "directory-with-unrelated-entries"
    }
    return [pscustomobject][ordered]@{
        Path = $full
        Classification = $classification
        Exists = $true
        CompatibleInitialized = $false
        ManifestPath = $manifestPath
        Entries = $entries
        ConflictingEntries = @($entries | ForEach-Object { $_.Name + " [" + ($_.Attributes -join ", ") + "]" })
    }
}

function New-WzhkUniqueRenderSubfolder {
    param(
        [Parameter(Mandatory = $true)][string]$ParentDirectory,
        [Parameter(Mandatory = $true)][string]$BaseName
    )
    $parent = [IO.Path]::GetFullPath($ParentDirectory)
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw "Parent output directory does not exist." }
    $safe = (ConvertTo-WzhkProfileSlug -Name $BaseName).ToLowerInvariant()
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    for ($attempt = 0; $attempt -lt 1000; $attempt++) {
        $suffix = if ($attempt -eq 0) { "" } else { "-" + $attempt.ToString("D3") }
        $candidate = Join-Path $parent ($safe + "-" + $stamp + $suffix)
        if (-not (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    throw "Could not allocate a collision-safe render subfolder name."
}

function Test-WzhkOutputCompatibility {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [Parameter(Mandatory = $true)][string]$OutputPath
    )

    $issues = New-Object System.Collections.Generic.List[object]
    $profile = Import-WzhkRenderProfile -Path $ProfilePath
    $profileHash = Get-WzhkFileSha256 -Path $ProfilePath
    $sceneHash = Get-WzhkFileSha256 -Path $ScenePath
    $expectedSceneHash = ([string](Get-WzhkPropertyValue -Object $profile -Name "approvedSceneSha256" -Default "")).ToUpperInvariant()
    if ($sceneHash -ne $expectedSceneHash) { $issues.Add([pscustomobject]@{ Code = "scene-hash-mismatch"; Message = "Selected scene does not match the profile." }) }

    $outputFull = [IO.Path]::GetFullPath($OutputPath).TrimEnd('\')
    $outputRoot = [IO.Path]::GetPathRoot($outputFull)
    $relativeOutput = $outputFull.Substring($outputRoot.Length).Trim('\')
    $outputParts = @($relativeOutput -split '\\' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($outputFull -ieq $outputRoot.TrimEnd('\') -or $outputParts.Count -lt 2) {
        $issues.Add([pscustomobject]@{ Code = "unsafe-output-root"; Message = "Output directory is too broad; choose an isolated directory at least two levels below the drive root." })
    }
    foreach ($sourcePath in @([IO.Path]::GetFullPath($ProfilePath), [IO.Path]::GetFullPath($ScenePath))) {
        $outputPrefix = $outputFull + [IO.Path]::DirectorySeparatorChar
        if ($sourcePath.StartsWith($outputPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            $issues.Add([pscustomobject]@{ Code = "unsafe-output-root"; Message = "The approved scene and saved profile must not be stored inside the production output directory." })
        }
    }
    $directoryInspection = Get-WzhkOutputDirectoryInspection -OutputPath $outputFull
    if (-not $directoryInspection.Exists) {
        return [pscustomobject]@{ Compatible = ($issues.Count -eq 0); Status = "new-output"; Issues = $issues.ToArray(); ManifestPath = (Join-Path $outputFull "manifests\render-manifest.json") }
    }
    if ($directoryInspection.Classification -eq "not-a-directory") {
        $issues.Add([pscustomobject]@{ Code = "output-not-directory"; Message = "Output path exists but is not a directory." })
        return [pscustomobject]@{ Compatible = $false; Status = "rejected"; Issues = $issues.ToArray(); ManifestPath = "" }
    }
    $manifestPath = Join-Path $outputFull "manifests\render-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        if ($directoryInspection.Entries.Count -gt 0) {
            $conflicts = @($directoryInspection.ConflictingEntries) -join "; "
            $issues.Add([pscustomobject]@{
                Code = $(if ($directoryInspection.Classification -eq "parent-directory-with-child-folders") { "parent-directory-selected" } else { "unmanaged-output" })
                Message = "Selected directory is classified as '$($directoryInspection.Classification)' and cannot be initialized directly. Conflicting entries: $conflicts. Use CREATE A NEW UNIQUE RENDER SUBFOLDER HERE."
                ConflictingEntries = @($directoryInspection.ConflictingEntries)
            })
        }
        return [pscustomobject]@{ Compatible = ($issues.Count -eq 0); Status = $(if ($issues.Count -eq 0) { "empty-output" } else { "rejected" }); Issues = $issues.ToArray(); ManifestPath = $manifestPath }
    }
    try { $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop }
    catch {
        $issues.Add([pscustomobject]@{ Code = "invalid-manifest"; Message = "Existing render manifest is invalid JSON." })
        return [pscustomobject]@{ Compatible = $false; Status = "rejected"; Issues = $issues.ToArray(); ManifestPath = $manifestPath }
    }

    if ([string](Get-WzhkPropertyValue -Object $manifest -Name "kind" -Default "") -ne $script:RenderManifestKind) { $issues.Add([pscustomobject]@{ Code = "manifest-kind-mismatch"; Message = "Render manifest kind is unsupported." }) }
    if ([string](Get-WzhkNestedValue -Object $manifest -Path @("scene", "sha256") -Default "") -ne $sceneHash) { $issues.Add([pscustomobject]@{ Code = "scene-mismatch"; Message = "Output belongs to another scene." }) }
    if ([string](Get-WzhkNestedValue -Object $manifest -Path @("renderProfile", "sha256") -Default "") -ne $profileHash) { $issues.Add([pscustomobject]@{ Code = "profile-mismatch"; Message = "Output belongs to another exact saved profile." }) }
    $manifestOutput = [string](Get-WzhkPropertyValue -Object $manifest -Name "outputDirectory" -Default "")
    if ([string]::IsNullOrWhiteSpace($manifestOutput) -or [IO.Path]::GetFullPath($manifestOutput).TrimEnd('\') -ine $outputFull) { $issues.Add([pscustomobject]@{ Code = "output-path-mismatch"; Message = "Render manifest belongs to another output directory." }) }

    $contract = Get-WzhkPropertyValue -Object $manifest -Name "frameContract"
    $timeline = Get-WzhkPropertyValue -Object $profile -Name "timeline" -Default $profile
    $sequence = Get-WzhkPropertyValue -Object $profile -Name "imageSequence"
    $resolution = Get-WzhkPropertyValue -Object $profile -Name "resolution"
    $checks = @(
        @("frameStart", (Get-WzhkPropertyValue $timeline "frameStart"), "frame-range-mismatch"),
        @("frameEnd", (Get-WzhkPropertyValue $timeline "frameEnd"), "frame-range-mismatch"),
        @("frameCount", (([int](Get-WzhkPropertyValue $timeline "frameEnd")) - ([int](Get-WzhkPropertyValue $timeline "frameStart")) + 1), "frame-range-mismatch"),
        @("fps", (Get-WzhkPropertyValue $timeline "fps"), "fps-mismatch"),
        @("width", (Get-WzhkPropertyValue $resolution "width"), "resolution-mismatch"),
        @("height", (Get-WzhkPropertyValue $resolution "height"), "resolution-mismatch"),
        @("filenamePattern", (Get-WzhkPropertyValue $sequence "filenamePattern"), "format-mismatch"),
        @("format", (Get-WzhkPropertyValue $sequence "format"), "format-mismatch"),
        @("bitDepth", (Get-WzhkPropertyValue $sequence "bitDepth"), "format-mismatch"),
        @("colorMode", (Get-WzhkPropertyValue $sequence "colorMode"), "format-mismatch")
    )
    if ([string](Get-WzhkPropertyValue -Object $profile -Name "schemaVersion" -Default "") -eq $script:ProfileSchemaVersion) {
        $profileOutput = Get-WzhkPropertyValue -Object $profile -Name "output"
        $checks += @(
            @("pixelAspectX", (Get-WzhkPropertyValue $resolution "pixelAspectX" -Default 1.0), "resolution-mismatch"),
            @("pixelAspectY", (Get-WzhkPropertyValue $resolution "pixelAspectY" -Default 1.0), "resolution-mismatch"),
            @("framesSubdirectory", (Get-WzhkPropertyValue $profileOutput "framesSubdirectory" -Default "frames"), "format-mismatch")
        )
    }
    foreach ($check in $checks) {
        $actual = Get-WzhkPropertyValue -Object $contract -Name ([string]$check[0])
        if ([string]$actual -cne [string]$check[1]) {
            $issues.Add([pscustomobject]@{ Code = [string]$check[2]; Message = [string]::Format("Output {0} does not match the profile.", $check[0]) })
        }
    }
    return [pscustomobject]@{
        Compatible = ($issues.Count -eq 0)
        Status = $(if ($issues.Count -eq 0) { "resume-compatible" } else { "rejected" })
        Issues = $issues.ToArray()
        ManifestPath = $manifestPath
    }
}

function Add-WzhkComparableProfileValue {
    param(
        [Parameter(Mandatory = $true)][Collections.Generic.Dictionary[string, string]]$Map,
        [AllowNull()][object]$Value,
        [string]$Path = ""
    )

    $root = if ([string]::IsNullOrWhiteSpace($Path)) { "" } else { $Path.Split('.')[0] }
    if ($root -in @("timestamps", "createdAt", "updatedAt", "profileSha256", "integrity", "validation", "warnings", "estimates", "authorization")) { return }
    if ($null -eq $Value) {
        if (-not [string]::IsNullOrWhiteSpace($Path)) { $Map[$Path] = "<null>" }
        return
    }
    if ($Value -is [Collections.IDictionary]) {
        foreach ($key in @($Value.Keys | Sort-Object)) {
            $childPath = if ([string]::IsNullOrWhiteSpace($Path)) { [string]$key } else { $Path + "." + [string]$key }
            Add-WzhkComparableProfileValue -Map $Map -Value $Value[$key] -Path $childPath
        }
        return
    }
    if ($Value -is [pscustomobject]) {
        foreach ($property in @($Value.PSObject.Properties | Sort-Object Name)) {
            $childPath = if ([string]::IsNullOrWhiteSpace($Path)) { $property.Name } else { $Path + "." + $property.Name }
            Add-WzhkComparableProfileValue -Map $Map -Value $property.Value -Path $childPath
        }
        return
    }
    if ($Value -is [Collections.IEnumerable] -and $Value -isnot [string]) {
        $items = @($Value)
        $Map[$Path + ".Count"] = [string]$items.Count
        for ($index = 0; $index -lt $items.Count; $index++) {
            Add-WzhkComparableProfileValue -Map $Map -Value $items[$index] -Path ($Path + "[" + $index + "]")
        }
        return
    }
    if ($Value -is [bool]) { $Map[$Path] = ([bool]$Value).ToString().ToLowerInvariant(); return }
    if ($Value -is [double] -or $Value -is [single] -or $Value -is [decimal]) {
        $Map[$Path] = ([double]$Value).ToString("R", [Globalization.CultureInfo]::InvariantCulture)
        return
    }
    $Map[$Path] = [string]$Value
}

function Compare-WzhkRenderProfiles {
    param(
        [Parameter(Mandatory = $true)][object]$Left,
        [Parameter(Mandatory = $true)][object]$Right
    )
    $leftMap = New-Object 'Collections.Generic.Dictionary[string,string]' ([StringComparer]::Ordinal)
    $rightMap = New-Object 'Collections.Generic.Dictionary[string,string]' ([StringComparer]::Ordinal)
    Add-WzhkComparableProfileValue -Map $leftMap -Value $Left
    Add-WzhkComparableProfileValue -Map $rightMap -Value $Right
    $paths = @(@($leftMap.Keys) + @($rightMap.Keys) | Sort-Object -Unique)
    $differences = New-Object System.Collections.Generic.List[object]
    foreach ($path in $paths) {
        $leftValue = if ($leftMap.ContainsKey($path)) { $leftMap[$path] } else { "<missing>" }
        $rightValue = if ($rightMap.ContainsKey($path)) { $rightMap[$path] } else { "<missing>" }
        if ([string]$leftValue -cne [string]$rightValue) { $differences.Add([pscustomobject]@{ Path = $path; Left = $leftValue; Right = $rightValue }) }
    }
    return $differences.ToArray()
}

Export-ModuleMember -Function `
    ConvertTo-WzhkSafeProfileName, `
    ConvertTo-WzhkProfileSlug, `
    Get-WzhkFileSha256, `
    Get-WzhkCanonicalProfileJson, `
    Get-WzhkProfileContentHash, `
    Test-WzhkOutputDirectoryPattern, `
    Get-WzhkRenderProfileTemplates, `
    Get-WzhkRenderProfileTemplate, `
    New-WzhkRenderProfile, `
    Normalize-WzhkRenderProfile, `
    Test-WzhkRenderProfile, `
    Set-WzhkProfileValue, `
    Set-WzhkProfileAuthorizationPending, `
    Save-WzhkRenderProfile, `
    Import-WzhkRenderProfile, `
    Resolve-WzhkRecommendedProfilePointer, `
    Get-WzhkSavedRenderProfiles, `
    Copy-WzhkRenderProfile, `
    Rename-WzhkRenderProfile, `
    Remove-WzhkRenderProfile, `
    Get-WzhkProfileAuthorizationToken, `
    New-WzhkProfileAuthorizationRequest, `
    New-WzhkProfileAuthorizationRecord, `
    Test-WzhkProfileAuthorizationRecord, `
    Get-WzhkOutputDirectoryInspection, `
    New-WzhkUniqueRenderSubfolder, `
    Test-WzhkOutputCompatibility, `
    Compare-WzhkRenderProfiles
