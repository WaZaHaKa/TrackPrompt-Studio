Set-StrictMode -Version Latest

function Get-WzhkHashPrefix {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$Length = 12
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return "MISSING"
    }

    $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($hash.Length -le $Length) {
        return $hash
    }

    return $hash.Substring(0, $Length)
}

function Get-WzhkJsonPathValue {
    param(
        [AllowNull()][object]$Root,
        [object[]]$CandidatePaths
    )

    foreach ($path in $CandidatePaths) {
        $current = $Root
        $found = $true

        foreach ($segment in @($path)) {
            if ($null -eq $current) {
                $found = $false
                break
            }

            $property = $current.PSObject.Properties[[string]$segment]
            if ($null -eq $property) {
                $found = $false
                break
            }

            $current = $property.Value
        }

        if ($found -and $null -ne $current) {
            return $current
        }
    }

    return $null
}

function Find-WzhkJsonScalar {
    param(
        [AllowNull()][object]$Node,
        [string[]]$Names,
        [int]$Depth = 0
    )

    if ($null -eq $Node -or $Depth -gt 12) {
        return $null
    }

    if ($Node -is [string] -or $Node -is [ValueType]) {
        return $null
    }

    if ($Node -is [System.Collections.IEnumerable] -and -not ($Node -is [pscustomobject])) {
        foreach ($item in $Node) {
            $value = Find-WzhkJsonScalar -Node $item -Names $Names -Depth ($Depth + 1)
            if ($null -ne $value) {
                return $value
            }
        }
        return $null
    }

    foreach ($property in $Node.PSObject.Properties) {
        if ($Names -contains $property.Name) {
            if (
                $property.Value -is [string] -or
                $property.Value -is [ValueType]
            ) {
                return $property.Value
            }
        }
    }

    foreach ($property in $Node.PSObject.Properties) {
        $value = Find-WzhkJsonScalar -Node $property.Value -Names $Names -Depth ($Depth + 1)
        if ($null -ne $value) {
            return $value
        }
    }

    return $null
}

function Get-WzhkProfileInfo {
    param([Parameter(Mandatory = $true)][string]$Path)

    $json = $null
    try {
        $json = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        return [pscustomobject]@{
            Path = $Path
            ValidJson = $false
            Name = [IO.Path]::GetFileNameWithoutExtension($Path)
            DisplayName = [IO.Path]::GetFileNameWithoutExtension($Path)
            ProfileId = ""
            SchemaVersion = "unknown"
            Width = $null
            Height = $null
            Fps = $null
            FrameStart = 1
            FrameEnd = 13029
            ChunkSize = $null
            Format = "unknown"
            Hash = Get-WzhkHashPrefix -Path $Path
            ProfileSha256 = ""
            SceneHash = ""
            Quality = "unknown"
            ModifiedAt = $null
            Slug = "unknown-profile"
        }
    }

    $width = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("render", "width"),
        @("resolution", "width"),
        @("video", "width"),
        @("width")
    )
    if ($null -eq $width) {
        $width = Find-WzhkJsonScalar -Node $json -Names @("width", "resolutionX", "resolution_x")
    }

    $height = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("render", "height"),
        @("resolution", "height"),
        @("video", "height"),
        @("height")
    )
    if ($null -eq $height) {
        $height = Find-WzhkJsonScalar -Node $json -Names @("height", "resolutionY", "resolution_y")
    }

    $fps = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("render", "fps"),
        @("timeline", "fps"),
        @("video", "fps"),
        @("fps")
    )
    if ($null -eq $fps) {
        $fps = Find-WzhkJsonScalar -Node $json -Names @("fps", "frameRate", "frame_rate")
    }

    $frameStart = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("frameRange", "start"),
        @("timeline", "frameStart"),
        @("frameStart"),
        @("startFrame")
    )
    if ($null -eq $frameStart) {
        $frameStart = Find-WzhkJsonScalar -Node $json -Names @("frameStart", "startFrame", "start_frame")
    }

    $frameEnd = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("frameRange", "end"),
        @("timeline", "frameEnd"),
        @("frameEnd"),
        @("endFrame")
    )
    if ($null -eq $frameEnd) {
        $frameEnd = Find-WzhkJsonScalar -Node $json -Names @("frameEnd", "endFrame", "end_frame")
    }

    $chunkSize = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("render", "chunkSize"),
        @("production", "framesPerChunk"),
        @("production", "chunkSize"),
        @("chunking", "framesPerChunk"),
        @("chunkSize")
    )
    if ($null -eq $chunkSize) {
        $chunkSize = Find-WzhkJsonScalar -Node $json -Names @("chunkSize", "chunk_size")
    }

    $format = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("render", "imageFormat"),
        @("output", "imageFormat"),
        @("imageSequence", "format"),
        @("imageFormat"),
        @("format")
    )
    if ($null -eq $format) {
        $format = Find-WzhkJsonScalar -Node $json -Names @("imageFormat", "image_format", "fileFormat")
    }

    $profileId = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("profileId"),
        @("id")
    )
    $displayName = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("displayName"),
        @("name"),
        @("label"),
        @("profileId"),
        @("id")
    )

    $schemaVersion = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(@("schemaVersion"))
    $profileSha256 = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("profileSha256"),
        @("integrity", "canonicalSha256")
    )
    $sceneHash = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("approvedScene", "sha256"),
        @("approvedSceneSha256"),
        @("sceneSha256")
    )
    $sceneManifestHash = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("approvedScene", "manifestSha256"),
        @("sourceIdentities", "sceneManifestSha256")
    )
    $audioHash = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(@("audio", "sha256"))
    $audioPath = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(@("audio", "path"))
    $quality = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("qualityMode"),
        @("render", "qualityMode")
    )
    $modifiedAt = Get-WzhkJsonPathValue -Root $json -CandidatePaths @(
        @("updatedAtUtc"),
        @("modifiedAt"),
        @("updatedAt")
    )

    if ($null -eq $frameStart) { $frameStart = 1 }
    if ($null -eq $frameEnd) { $frameEnd = 13029 }
    if ($null -eq $format) { $format = "unknown" }
    if ($null -eq $displayName) { $displayName = [IO.Path]::GetFileNameWithoutExtension($Path) }
    if ($null -eq $profileId) { $profileId = [IO.Path]::GetFileNameWithoutExtension($Path) }
    if ($null -eq $schemaVersion) { $schemaVersion = "unknown" }
    if ($null -eq $profileSha256) { $profileSha256 = "" }
    if ($null -eq $sceneHash) { $sceneHash = "" }
    if ($null -eq $sceneManifestHash) { $sceneManifestHash = "" }
    if ($null -eq $audioHash) { $audioHash = "" }
    if ($null -eq $audioPath) { $audioPath = "" }
    if ($null -eq $quality) { $quality = "unknown" }

    $slug = "custom"
    if ([int]$width -eq 3840 -and [int]$height -eq 2160) {
        $slug = "4k-30-sdr"
    }
    elseif ([int]$width -eq 2560 -and [int]$height -eq 1440) {
        $slug = "1440p-30-sdr"
    }
    elseif ([int]$width -eq 1920 -and [int]$height -eq 1080) {
        $slug = "1080p-30-sdr"
    }
    elseif ($null -ne $width -and $null -ne $height) {
        $slug = [string]::Format("{0}x{1}", $width, $height).ToLowerInvariant()
    }

    return [pscustomobject]@{
        Path = $Path
        ValidJson = $true
        Name = [string]$displayName
        DisplayName = [string]$displayName
        ProfileId = [string]$profileId
        SchemaVersion = [string]$schemaVersion
        Width = $width
        Height = $height
        Fps = $fps
        FrameStart = [int]$frameStart
        FrameEnd = [int]$frameEnd
        ChunkSize = $chunkSize
        Format = [string]$format
        Hash = Get-WzhkHashPrefix -Path $Path
        ProfileSha256 = [string]$profileSha256
        SceneHash = [string]$sceneHash
        SceneManifestHash = [string]$sceneManifestHash
        AudioSha256 = [string]$audioHash
        AudioPath = [string]$audioPath
        Quality = [string]$quality
        ModifiedAt = $modifiedAt
        Slug = $slug
    }
}

function Find-WzhkSceneManifest {
    param([string]$ScenePath)

    $directory = Split-Path -Parent $ScenePath
    $base = [IO.Path]::GetFileNameWithoutExtension($ScenePath)
    $exact = Join-Path $directory ($base + ".manifest.json")

    if (Test-Path -LiteralPath $exact -PathType Leaf) {
        return $exact
    }

    return Get-ChildItem -LiteralPath $directory -File -Filter "*.json" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "manifest" } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -ExpandProperty FullName -First 1
}

function Get-WzhkSceneInfo {
    param([Parameter(Mandatory = $true)][string]$Path)

    $manifestPath = Find-WzhkSceneManifest -ScenePath $Path
    $manifest = $null

    if (-not [string]::IsNullOrWhiteSpace($manifestPath)) {
        try {
            $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        }
        catch {
            $manifest = $null
        }
    }

    $collections = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(@("scene", "collections"), @("collections"))
    $collectionCount = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(@("scene", "collectionCount"), @("collectionCount"))
    if ($null -eq $collectionCount -and $null -ne $collections) { $collectionCount = @($collections).Count }

    return [pscustomobject]@{
        Path = $Path
        Hash = Get-WzhkHashPrefix -Path $Path
        ManifestPath = $manifestPath
        ObjectCount = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(@("scene", "objectCount"), @("objectCount"))
        MaterialCount = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(@("scene", "materialCount"), @("materialCount"))
        CollectionCount = $collectionCount
        FCurveCount = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(@("scene", "fCurveCount"), @("fCurveCount"))
        MacroStateCount = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(
            @("scene", "presetSummary", "macroStateCount"),
            @("presetSummary", "macroStateCount"),
            @("macroStateCount")
        )
        AudioBusCurveCount = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(
            @("scene", "audioBusFCurveCount"),
            @("audioBusFCurveCount")
        )
        FrameStart = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(@("scene", "frameStart"), @("frameStart"))
        FrameEnd = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(@("scene", "frameEnd"), @("frameEnd"))
        Fps = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(@("scene", "fps"), @("fps"))
        BlenderVersion = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(
            @("scene", "blenderVersion"),
            @("blenderVersion")
        )
        ActiveCamera = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(
            @("scene", "activeCamera"),
            @("scene", "camera"),
            @("activeCamera"),
            @("camera")
        )
        Preset = Get-WzhkJsonPathValue -Root $manifest -CandidatePaths @(
            @("scene", "preset"),
            @("scene", "presetSummary", "preset"),
            @("preset")
        )
        ManifestValid = ($null -ne $manifest)
    }
}

function Get-WzhkPrepCandidates {
    param([Parameter(Mandatory = $true)][string]$TestOutputRoot)

    $candidateMap = @{}
    foreach ($directory in @(Get-ChildItem -LiteralPath $TestOutputRoot -Directory -ErrorAction SilentlyContinue)) {
        if ($directory.Name -like "final-render-prep-*" -or $directory.Name -like "space-journey-*") {
            $candidateMap[$directory.FullName.ToUpperInvariant()] = $directory
        }
    }

    foreach ($approvedDirectory in @(
        Get-ChildItem -LiteralPath $TestOutputRoot -Directory -Recurse -Filter "approved-candidate" -ErrorAction SilentlyContinue
    )) {
        $parent = $approvedDirectory.Parent
        if ($null -ne $parent) {
            $candidateMap[$parent.FullName.ToUpperInvariant()] = $parent
        }
    }

    return @($candidateMap.Values | Sort-Object LastWriteTimeUtc -Descending)
}

function Find-WzhkApprovedScene {
    param([Parameter(Mandatory = $true)][string]$PrepPath)

    $approvedDirectory = Join-Path $PrepPath "approved-candidate"
    $searchRoot = if (Test-Path -LiteralPath $approvedDirectory -PathType Container) {
        $approvedDirectory
    }
    else {
        $PrepPath
    }

    $scenes = @(
        Get-ChildItem -LiteralPath $searchRoot -Recurse -File -Filter "*.blend" -ErrorAction SilentlyContinue |
            Sort-Object @{
                Expression = {
                    if ($_.Name -match "final-candidate") { 0 }
                    elseif ($_.Name -match "space-journey") { 1 }
                    else { 2 }
                }
                Ascending = $true
            }, @{
                Expression = { $_.LastWriteTimeUtc }
                Descending = $true
            }
    )

    if ($scenes.Count -eq 0) {
        return $null
    }

    return $scenes[0].FullName
}

function Get-WzhkProfileCandidates {
    param([Parameter(Mandatory = $true)][string]$PrepPath)

    $paths = @(
        Get-ChildItem -LiteralPath $PrepPath -Recurse -File -Filter "*.json" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -match "render-profile" -and
                $_.Name -notmatch "manifest"
            }
    )

    $profiles = New-Object System.Collections.Generic.List[object]
    foreach ($path in $paths) {
        $profiles.Add((Get-WzhkProfileInfo -Path $path.FullName))
    }

    return @(
        $profiles |
            Sort-Object @{
                Expression = {
                    if ([IO.Path]::GetFileName($_.Path) -eq "render-profile.final.json") { 0 }
                    elseif ($_.Slug -eq "4k-30-sdr") { 1 }
                    elseif ($_.Slug -eq "1440p-30-sdr") { 2 }
                    else { 3 }
                }
            }, Path
    )
}

function Find-WzhkAuthorizationToken {
    param(
        [Parameter(Mandatory = $true)][string]$PrepPath,
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [Parameter(Mandatory = $true)][string]$ProfilePath
    )

    $sceneHash = Get-WzhkHashPrefix -Path $ScenePath
    $profileHash = Get-WzhkHashPrefix -Path $ProfilePath
    $pattern = 'AUTHORIZE FULL RENDER:\s*[^|\r\n"]+\|\s*[^|\r\n"]+\|\s*[^|\r\n"]+\|\s*SCENE\s+[A-Fa-f0-9]{12}\s*\|\s*PROFILE\s+[A-Fa-f0-9]{12}'

    $documents = @(
        Get-ChildItem -LiteralPath $PrepPath -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension.ToLowerInvariant() -in @(".md", ".txt", ".json") }
    )

    foreach ($document in $documents) {
        try {
            $text = Get-Content -LiteralPath $document.FullName -Raw -ErrorAction Stop
            foreach ($match in [regex]::Matches($text, $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
                $token = $match.Value.Trim()
                if (
                    $token.ToUpperInvariant().Contains(("SCENE " + $sceneHash)) -and
                    $token.ToUpperInvariant().Contains(("PROFILE " + $profileHash))
                ) {
                    return $token
                }
            }
        }
        catch {
            # Ignore unreadable report files and continue.
        }
    }

    return ""
}

function Get-WzhkOutputStats {
    param(
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [int]$TotalFrames = 13029
    )

    if (-not (Test-Path -LiteralPath $OutputPath -PathType Container)) {
        return [pscustomobject]@{
            Published = 0
            Inflight = 0
            LatestFrame = 0
            Percent = 0.0
        }
    }

    $publishedMap = @{}
    $publishedRoot = Join-Path $OutputPath "frames"
    if (Test-Path -LiteralPath $publishedRoot -PathType Container) {
        foreach ($file in Get-ChildItem -LiteralPath $publishedRoot -Recurse -File -ErrorAction SilentlyContinue) {
            if ($file.BaseName -match '^frame_(\d{6})$') {
                $publishedMap[[int]$Matches[1]] = $true
            }
        }
    }

    $inflightMap = @{}
    $checkpoints = Join-Path $OutputPath "checkpoints"
    if (Test-Path -LiteralPath $checkpoints -PathType Container) {
        foreach ($file in Get-ChildItem -LiteralPath $checkpoints -Recurse -File -ErrorAction SilentlyContinue) {
            if ($file.BaseName -match '^frame_(\d{6})$') {
                $inflightMap[[int]$Matches[1]] = $true
            }
        }
    }

    $all = @{}
    foreach ($key in $publishedMap.Keys) { $all[$key] = $true }
    foreach ($key in $inflightMap.Keys) { $all[$key] = $true }

    $latest = if ($all.Keys.Count -gt 0) {
        [int](@($all.Keys | Sort-Object)[-1])
    }
    else {
        0
    }

    return [pscustomobject]@{
        Published = $publishedMap.Keys.Count
        Inflight = $inflightMap.Keys.Count
        LatestFrame = $latest
        Percent = [math]::Min(100.0, (100.0 * $publishedMap.Keys.Count / $TotalFrames))
    }
}

function Get-WzhkOutputCandidates {
    param(
        [Parameter(Mandatory = $true)][string]$FinalOutputRoot,
        [string]$ProfileSlug = ""
    )

    $directories = @(
        Get-ChildItem -LiteralPath $FinalOutputRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending
    )

    if (-not [string]::IsNullOrWhiteSpace($ProfileSlug) -and $ProfileSlug -ne "custom") {
        $matching = @($directories | Where-Object { $_.Name -like ("*" + $ProfileSlug + "*") })
        if ($matching.Count -gt 0) {
            return $matching
        }
    }

    return $directories
}

function New-WzhkOutputPath {
    param(
        [Parameter(Mandatory = $true)][string]$FinalOutputRoot,
        [Parameter(Mandatory = $true)][string]$ProfileSlug,
        [string]$ProjectSlug = "trip-to-andromeda",
        [string]$PresetSlug = "space-journey",
        [string]$ResolutionSlug = "",
        [string]$DirectoryPattern = "{project}-{preset}-{resolution}-{timestamp}"
    )

    function ConvertTo-WzhkOutputSegment {
        param([string]$Value, [string]$Fallback)
        $safe = ([string]$Value).Trim().ToLowerInvariant() -replace '[^a-z0-9]+', '-'
        $safe = $safe.Trim('-').Trim('.')
        if ([string]::IsNullOrWhiteSpace($safe) -or $safe -in @(".", "..")) { return $Fallback }
        return $safe
    }

    $safeProfile = ConvertTo-WzhkOutputSegment -Value $ProfileSlug -Fallback "custom"
    $safeProject = ConvertTo-WzhkOutputSegment -Value $ProjectSlug -Fallback "trackprompt"
    $safePreset = ConvertTo-WzhkOutputSegment -Value $PresetSlug -Fallback "custom"
    $safeResolution = ConvertTo-WzhkOutputSegment -Value $(if ([string]::IsNullOrWhiteSpace($ResolutionSlug)) { $safeProfile } else { $ResolutionSlug }) -Fallback $safeProfile
    $patternValidator = Get-Command Test-WzhkOutputDirectoryPattern -ErrorAction SilentlyContinue
    if ($null -ne $patternValidator) {
        $patternValidation = Test-WzhkOutputDirectoryPattern -Pattern $DirectoryPattern
        if (-not $patternValidation.Valid) { throw $patternValidation.Message }
    }
    else {
        if ([string]::IsNullOrWhiteSpace($DirectoryPattern) -or $DirectoryPattern.Length -gt 120) { throw "Output directory pattern must contain 1 through 120 characters." }
        $patternRemainder = $DirectoryPattern
        foreach ($token in @("{project}", "{preset}", "{resolution}", "{profile}", "{timestamp}")) { $patternRemainder = $patternRemainder.Replace($token, "") }
        $sample = $DirectoryPattern.Replace("{project}", "project").Replace("{preset}", "preset").Replace("{resolution}", "resolution").Replace("{profile}", "profile").Replace("{timestamp}", "20260720-120000")
        if ($patternRemainder -match '[{}]' -or $patternRemainder -notmatch '^[A-Za-z0-9._-]*$' -or $DirectoryPattern -match '[\\/:]' -or $DirectoryPattern -notmatch '\{timestamp\}' -or $sample -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$' -or $sample.EndsWith('.')) {
            throw "Output directory pattern contains an unsafe literal, unknown token, lacks {timestamp}, or does not expand to a safe directory name."
        }
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $directoryName = $DirectoryPattern.Replace("{project}", $safeProject).Replace("{preset}", $safePreset).Replace("{resolution}", $safeResolution).Replace("{profile}", $safeProfile).Replace("{timestamp}", $stamp)
    if ($directoryName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$' -or $directoryName.EndsWith('.') -or $directoryName -in @(".", "..")) { throw "Expanded output directory name is unsafe or too long." }
    return Join-Path $FinalOutputRoot $directoryName
}

Export-ModuleMember -Function `
    Get-WzhkHashPrefix, `
    Get-WzhkProfileInfo, `
    Get-WzhkSceneInfo, `
    Get-WzhkPrepCandidates, `
    Find-WzhkApprovedScene, `
    Get-WzhkProfileCandidates, `
    Find-WzhkAuthorizationToken, `
    Get-WzhkOutputStats, `
    Get-WzhkOutputCandidates, `
    New-WzhkOutputPath
