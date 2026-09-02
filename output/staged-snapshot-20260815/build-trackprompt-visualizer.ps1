#requires -Version 5.1
<#
.SYNOPSIS
    Exports a TrackPrompt Blender cue sheet, builds the .blend, and renders a bounded preview.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass `
      -File .\build-trackprompt-visualizer.ps1 `
      -JobId "b0f7efd1-8267-4294-b5ce-6ebc84f0c9ef" `
      -AudioPath "C:\Users\theon\OneDrive\Desktop\Gratis Project\DJ WaZaHaKa - Trip to Andromeda.wav"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string]$JobId,

    [Parameter(Mandatory = $true)]
    [string]$AudioPath,

    [string]$BlenderExe = "",

    [string]$ApiBase = "http://127.0.0.1:8000",

    [ValidateSet(24, 25, 30, 50, 60)]
    [int]$Fps = 30,

    [ValidateSet("compact", "balanced", "detailed")]
    [string]$CurveDetail = "balanced",

    [ValidateSet("abstract-geometry", "space-journey")]
    [string]$VisualizerPreset = "abstract-geometry",

    [string]$VisualizerConfigPath = "",

    [int]$Seed = 84291,

    [int]$PreviewWidth = 1280,

    [int]$PreviewHeight = 720,

    [switch]$SkipPreview
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$visualizerPresetWasExplicit = $PSBoundParameters.ContainsKey("VisualizerPreset")
$seedWasExplicit = $PSBoundParameters.ContainsKey("Seed")

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description,
        [string]$DisplayCommand = ""
    )

    if ([string]::IsNullOrWhiteSpace($DisplayCommand)) {
        Write-Host "> $Executable $($ArgumentList -join ' ')" -ForegroundColor DarkGray
    }
    else {
        Write-Host "> $DisplayCommand" -ForegroundColor DarkGray
    }

    $previousPreference = $ErrorActionPreference

    try {
        # Windows PowerShell 5.1 can promote ordinary native stderr progress
        # into terminating ErrorRecord objects. Native exit status is the source
        # of truth here.
        $ErrorActionPreference = "Continue"
        & $Executable @ArgumentList
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode."
    }
}

function Resolve-BlenderExecutable {
    param([string]$ExplicitPath)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        return (Resolve-Path -LiteralPath $ExplicitPath).Path
    }

    $command = Get-Command blender.exe -ErrorAction SilentlyContinue

    if ($null -ne $command) {
        return $command.Source
    }

    $roots = @(
        "$env:ProgramFiles\Blender Foundation",
        "${env:ProgramFiles(x86)}\Blender Foundation",
        "$env:LOCALAPPDATA\Programs\Blender Foundation",
        "C:\Program Files\Steam\steamapps\common",
        "C:\Program Files (x86)\Steam\steamapps\common"
    )

    $matches = @(
        foreach ($root in $roots) {
            if (
                -not [string]::IsNullOrWhiteSpace($root) -and
                (Test-Path -LiteralPath $root -PathType Container)
            ) {
                Get-ChildItem `
                    -LiteralPath $root `
                    -Filter "blender.exe" `
                    -File `
                    -Recurse `
                    -ErrorAction SilentlyContinue
            }
        }
    )

    $match = $matches |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if ($null -eq $match) {
        throw "Blender.exe was not found. Pass -BlenderExe with its absolute path."
    }

    return $match.FullName
}

function Resolve-FfmpegExecutable {
    $command = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue

    if ($null -ne $command) {
        return $command.Source
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe",
        "$env:USERPROFILE\scoop\shims\ffmpeg.exe",
        "C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        "C:\ffmpeg\bin\ffmpeg.exe",
        "C:\Program Files\ffmpeg\bin\ffmpeg.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}

function New-VisualizerConfigRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Preset,
        [Parameter(Mandatory = $true)][int]$SeedValue,
        [string]$ConfigPath = "",
        [bool]$PresetWasExplicit = $false,
        [bool]$SeedWasExplicit = $false
    )

    $payload = [pscustomobject]@{}

    if (-not [string]::IsNullOrWhiteSpace($ConfigPath)) {
        if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
            throw "VisualizerConfigPath does not identify an existing file."
        }

        $resolvedPath = (Resolve-Path -LiteralPath $ConfigPath).Path
        $item = Get-Item -LiteralPath $resolvedPath

        if ($item.Extension -ne ".json" -or $item.Length -le 0 -or $item.Length -gt 65536) {
            throw "VisualizerConfigPath must be a non-empty .json file no larger than 64 KiB."
        }

        try {
            $payload = Get-Content -LiteralPath $resolvedPath -Raw | ConvertFrom-Json
        }
        catch {
            throw "VisualizerConfigPath does not contain valid JSON."
        }

        if (
            $null -eq $payload -or
            $payload -is [System.Array] -or
            $payload -is [string] -or
            $payload -is [ValueType]
        ) {
            throw "VisualizerConfigPath must contain a JSON object."
        }

        foreach ($property in @($payload.PSObject.Properties)) {
            if ($property.Name -notin @(
                "schemaVersion", "preset", "parameters", "seed",
                "defaultedParameters", "warnings"
            )) {
                throw "VisualizerConfigPath contains an unsupported top-level field: $($property.Name)"
            }
        }
    }

    $schemaVersion = if ($null -ne $payload.PSObject.Properties["schemaVersion"]) {
        [string]$payload.schemaVersion
    }
    else {
        "1.0.0"
    }

    if ($schemaVersion -ne "1.0.0") {
        throw "Visualizer configuration schemaVersion must be 1.0.0."
    }

    $effectivePreset = $Preset

    if ($null -ne $payload.PSObject.Properties["preset"]) {
        $filePreset = [string]$payload.preset

        if ($filePreset -notin @("abstract-geometry", "space-journey")) {
            throw "Visualizer configuration contains an unsupported preset."
        }

        if ($PresetWasExplicit -and $filePreset -ne $Preset) {
            throw "VisualizerPreset conflicts with the preset in VisualizerConfigPath."
        }

        $effectivePreset = $filePreset
    }

    $effectiveSeed = $SeedValue

    if ($null -ne $payload.PSObject.Properties["seed"]) {
        $fileSeed = $payload.seed

        if (
            -not ($fileSeed -is [int] -or $fileSeed -is [long]) -or
            [long]$fileSeed -lt 0 -or
            [long]$fileSeed -gt 2147483647
        ) {
            throw "Visualizer configuration seed must be an integer between 0 and 2147483647."
        }

        if ($SeedWasExplicit -and [int]$fileSeed -ne $SeedValue) {
            throw "Seed conflicts with the seed in VisualizerConfigPath."
        }

        $effectiveSeed = [int]$fileSeed
    }

    $parameters = if ($null -ne $payload.PSObject.Properties["parameters"]) {
        $payload.parameters
    }
    else {
        [pscustomobject]@{}
    }

    if (
        $null -eq $parameters -or
        $parameters -is [System.Array] -or
        $parameters -is [string] -or
        $parameters -is [ValueType]
    ) {
        throw "Visualizer configuration parameters must be a JSON object."
    }

    return [ordered]@{
        schemaVersion = "1.0.0"
        preset = $effectivePreset
        parameters = $parameters
        seed = $effectiveSeed
    }
}

function ConvertTo-CanonicalJsonValue {
    param($Value, [int]$Depth = 0)

    if ($Depth -gt 64) {
        throw "Visualizer configuration exceeds the safe nesting limit."
    }

    if ($null -eq $Value -or $Value -is [string] -or $Value -is [ValueType]) {
        return $Value
    }

    if ($Value -is [System.Collections.IDictionary]) {
        $result = [ordered]@{}

        foreach ($key in @($Value.Keys | Sort-Object)) {
            $result[[string]$key] = ConvertTo-CanonicalJsonValue `
                -Value $Value[$key] `
                -Depth ($Depth + 1)
        }

        return $result
    }

    if ($Value -is [System.Collections.IEnumerable]) {
        return @(
            foreach ($item in $Value) {
                ConvertTo-CanonicalJsonValue -Value $item -Depth ($Depth + 1)
            }
        )
    }

    $result = [ordered]@{}

    foreach ($property in @($Value.PSObject.Properties | Sort-Object Name)) {
        $result[[string]$property.Name] = ConvertTo-CanonicalJsonValue `
            -Value $property.Value `
            -Depth ($Depth + 1)
    }

    return $result
}

function Test-JsonSemanticMatch {
    param(
        [Parameter(Mandatory = $true)]$Actual,
        [Parameter(Mandatory = $true)]$Expected
    )

    $actualJson = ConvertTo-CanonicalJsonValue -Value $Actual |
        ConvertTo-Json -Depth 20 -Compress
    $expectedJson = ConvertTo-CanonicalJsonValue -Value $Expected |
        ConvertTo-Json -Depth 20 -Compress
    return $actualJson -ceq $expectedJson
}

function Assert-FreshNonEmptyFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][DateTime]$StartedAt,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description was not created."
    }

    $item = Get-Item -LiteralPath $Path

    if ($item.Length -le 0 -or $item.LastWriteTimeUtc -lt $StartedAt) {
        throw "$Description is empty or predates the current Blender invocation."
    }

    return $item.FullName
}

function Read-FreshJsonArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][DateTime]$StartedAt,
        [Parameter(Mandatory = $true)][string]$Description
    )

    [void](Assert-FreshNonEmptyFile `
        -Path $Path `
        -StartedAt $StartedAt `
        -Description $Description)

    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        throw "$Description is not valid JSON."
    }
}

function Assert-CurrentBuildArtifacts {
    param(
        [Parameter(Mandatory = $true)][string]$BlendPath,
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)]$ExpectedConfig,
        [Parameter(Mandatory = $true)][string]$ExpectedPreset,
        [Parameter(Mandatory = $true)][int]$ExpectedSeed,
        [Parameter(Mandatory = $true)][DateTime]$StartedAt
    )

    $resolvedBlend = Assert-FreshNonEmptyFile `
        -Path $BlendPath `
        -StartedAt $StartedAt `
        -Description "Blender scene"
    $manifest = Read-FreshJsonArtifact `
        -Path $ManifestPath `
        -StartedAt $StartedAt `
        -Description "Blender scene manifest"

    if (
        $manifest.ok -ne $true -or
        [string]$manifest.schemaVersion -ne "1.0.0" -or
        [string]$manifest.preset -ne $ExpectedPreset -or
        [int]$manifest.seed -ne $ExpectedSeed -or
        $null -eq $manifest.visualizerConfig -or
        -not (Test-JsonSemanticMatch `
            -Actual $manifest.visualizerConfig `
            -Expected $ExpectedConfig) -or
        $null -eq $manifest.scene -or
        $manifest.scene.ok -ne $true -or
        [string]$manifest.scene.preset -ne $ExpectedPreset -or
        [int]$manifest.scene.seed -ne $ExpectedSeed -or
        -not [IO.Path]::GetFullPath([string]$manifest.scene.outputFile).Equals(
            [IO.Path]::GetFullPath($resolvedBlend),
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "The current Blender scene manifest does not match the resolved visualizer configuration."
    }

    $checks = @($manifest.checks.PSObject.Properties)

    if ($checks.Count -lt 10 -or @($checks | Where-Object { $_.Value -ne $true }).Count -gt 0) {
        throw "The current Blender scene manifest contains failed or missing contract checks."
    }

    return $manifest
}

function Assert-CurrentPreviewArtifacts {
    param(
        [Parameter(Mandatory = $true)][string]$PreviewDirectory,
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)]$ExpectedConfig,
        [Parameter(Mandatory = $true)][string]$ExpectedPreset,
        [Parameter(Mandatory = $true)][string]$ExpectedClipName,
        [Parameter(Mandatory = $true)][int]$ExpectedWidth,
        [Parameter(Mandatory = $true)][int]$ExpectedHeight,
        [Parameter(Mandatory = $true)][int]$ExpectedFps,
        [Parameter(Mandatory = $true)][DateTime]$StartedAt
    )

    $manifest = Read-FreshJsonArtifact `
        -Path $ManifestPath `
        -StartedAt $StartedAt `
        -Description "Blender preview manifest"

    if (
        $manifest.ok -ne $true -or
        [string]$manifest.preset -ne $ExpectedPreset -or
        $null -eq $manifest.visualizerConfig -or
        -not (Test-JsonSemanticMatch `
            -Actual $manifest.visualizerConfig `
            -Expected $ExpectedConfig) -or
        [int]$manifest.render.width -ne $ExpectedWidth -or
        [int]$manifest.render.height -ne $ExpectedHeight -or
        [math]::Abs([double]$manifest.render.fps - $ExpectedFps) -gt 0.01 -or
        $manifest.clip.ok -ne $true -or
        $manifest.clip.verification.ok -ne $true -or
        [IO.Path]::GetFileName([string]$manifest.clip.clip) -cne $ExpectedClipName
    ) {
        throw "The current Blender preview manifest does not match the requested render."
    }

    $checks = @($manifest.checks.PSObject.Properties)

    if ($checks.Count -lt 10 -or @($checks | Where-Object { $_.Value -ne $true }).Count -gt 0) {
        throw "The current Blender preview manifest contains failed or missing contract checks."
    }

    $previewRoot = (Resolve-Path -LiteralPath $PreviewDirectory).Path.TrimEnd('\', '/')
    $artifacts = @($manifest.stills.stillFrames) + @([string]$manifest.clip.clip)

    if (@($manifest.stills.stillFrames).Count -lt 1) {
        throw "The current Blender preview manifest contains no stills."
    }

    foreach ($artifact in $artifacts) {
        $resolvedArtifact = Assert-FreshNonEmptyFile `
            -Path ([string]$artifact) `
            -StartedAt $StartedAt `
            -Description "Blender preview artifact"

        if (-not $resolvedArtifact.StartsWith(
            $previewRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "The preview manifest references an artifact outside its preset output directory."
        }
    }

    if ($ExpectedPreset -eq "space-journey") {
        $expectedRoles = @(
            "opening", "early-development", "main-groove",
            "breakdown", "peak", "outro"
        )
        $entries = @($manifest.stills.stills)

        if (
            $entries.Count -ne 6 -or
            @($manifest.stills.stillFrames).Count -ne 6 -or
            @($entries | ForEach-Object { $_.role }).Count -ne 6
        ) {
            throw "The Space Journey preview must contain six role-labelled stills."
        }

        for ($index = 0; $index -lt 6; $index++) {
            if ([string]$entries[$index].role -cne $expectedRoles[$index]) {
                throw "The Space Journey preview still roles are incomplete or out of order."
            }
        }

        $verification = $manifest.clip.verification

        if (
            [string]$verification.videoCodec -cne "h264" -or
            [string]$verification.audioCodec -cne "aac" -or
            [int]$verification.width -ne $ExpectedWidth -or
            [int]$verification.height -ne $ExpectedHeight -or
            [math]::Abs([double]$verification.fps - $ExpectedFps) -gt 0.01 -or
            $verification.audioMatchesRequest -ne $true -or
            [double]$manifest.clip.durationSeconds -gt 10.1
        ) {
            throw "The Space Journey preview lacks verified bounded H.264/AAC evidence."
        }
    }

    return $manifest
}

try {
    $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

    if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
        $RepoRoot = (Get-Location).Path
    }

    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
    Set-Location -LiteralPath $RepoRoot

    $resolvedAudio = (Resolve-Path -LiteralPath $AudioPath).Path
    $resolvedBlender = Resolve-BlenderExecutable -ExplicitPath $BlenderExe

    $buildScript = Join-Path $RepoRoot "blender\build_visualizer.py"
    $renderScript = Join-Path $RepoRoot "blender\render_preview.py"

    foreach ($requiredFile in @($buildScript, $renderScript)) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Required repository file is missing: $requiredFile"
        }
    }

    $visualizerConfigRequest = New-VisualizerConfigRequest `
        -Preset $VisualizerPreset `
        -SeedValue $Seed `
        -ConfigPath $VisualizerConfigPath `
        -PresetWasExplicit $visualizerPresetWasExplicit `
        -SeedWasExplicit $seedWasExplicit
    $VisualizerPreset = [string]$visualizerConfigRequest.preset
    $Seed = [int]$visualizerConfigRequest.seed

    $blendFileName = if ($VisualizerPreset -eq "space-journey") {
        "trip-to-andromeda-space-journey.blend"
    }
    else {
        "trip-to-andromeda-abstract.blend"
    }
    $previewClipName = if ($VisualizerPreset -eq "space-journey") {
        "space-journey-preview.mp4"
    }
    else {
        "trackprompt-preview.mp4"
    }

    $legacyOutputDirectory = Join-Path $RepoRoot "test-output\private-real-track"
    $outputDirectory = if ($VisualizerPreset -eq "space-journey") {
        Join-Path $legacyOutputDirectory "space-journey"
    }
    else {
        $legacyOutputDirectory
    }
    $previewDirectory = Join-Path $outputDirectory "preview"
    $cuePath = Join-Path $outputDirectory "visual-cues.json"
    $blendPath = Join-Path $outputDirectory $blendFileName
    $resolvedConfigPath = Join-Path $outputDirectory "visualizer-config.resolved.json"

    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    New-Item -ItemType Directory -Force -Path $previewDirectory | Out-Null

    Write-Step "Checking Blender"

    Invoke-NativeChecked `
        -Executable $resolvedBlender `
        -ArgumentList @("--version") `
        -Description "Blender version check"

    Write-Step "Checking the TrackPrompt backend and job"

    try {
        $health = Invoke-RestMethod `
            -Method Get `
            -Uri "$ApiBase/api/health" `
            -TimeoutSec 10
    }
    catch {
        throw @"
The TrackPrompt API is not reachable at $ApiBase.

Start it with:
  docker compose -f compose.yaml -f compose.full-gpu.yaml up -d
"@
    }

    Write-Host ($health | ConvertTo-Json -Depth 10)

    try {
        $resolvedVisualizerConfig = Invoke-RestMethod `
            -Method Post `
            -Uri "$ApiBase/api/visualizer/config/resolve" `
            -ContentType "application/json" `
            -Body ($visualizerConfigRequest | ConvertTo-Json -Depth 20 -Compress) `
            -TimeoutSec 30
    }
    catch {
        throw @"
The backend could not resolve the $VisualizerPreset visualizer configuration.

Make sure the API build exposes POST /api/visualizer/config/resolve and that
VisualizerConfigPath uses schema 1.0.0.
"@
    }

    $expectedConfigFields = @(
        "schemaVersion", "preset", "parameters", "seed",
        "defaultedParameters", "warnings"
    )
    $actualConfigFields = @($resolvedVisualizerConfig.PSObject.Properties.Name)

    if (
        $actualConfigFields.Count -ne $expectedConfigFields.Count -or
        @($actualConfigFields | Where-Object { $_ -notin $expectedConfigFields }).Count -gt 0 -or
        [string]$resolvedVisualizerConfig.schemaVersion -ne "1.0.0" -or
        [string]$resolvedVisualizerConfig.preset -ne $VisualizerPreset -or
        [int]$resolvedVisualizerConfig.seed -ne $Seed -or
        $null -eq $resolvedVisualizerConfig.parameters -or
        $null -eq $resolvedVisualizerConfig.defaultedParameters -or
        $null -eq $resolvedVisualizerConfig.warnings
    ) {
        throw "The backend returned an invalid or mismatched resolved visualizer configuration."
    }

    [IO.File]::WriteAllText(
        $resolvedConfigPath,
        ($resolvedVisualizerConfig | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )

    Write-Host "Visualizer preset: $VisualizerPreset" -ForegroundColor Green
    Write-Host "Resolved config: $resolvedConfigPath" -ForegroundColor Green

    try {
        $job = Invoke-RestMethod `
            -Method Get `
            -Uri "$ApiBase/api/analyses/$JobId" `
            -TimeoutSec 20
    }
    catch {
        throw @"
The analysis job $JobId is not available through the local API.

It may have expired or been deleted. Re-run the track in Deep mode with the
visual-feature-capable build, then use the new job ID.
"@
    }

    Write-Host "Job found: $JobId" -ForegroundColor Green

    Write-Step "Exporting the real Blender cue sheet"

    $query = @(
        "fps=$Fps",
        "includeBeats=true",
        "includeOnsets=true",
        "includeStemEvidence=true",
        "includeCurves=true",
        "curveDetail=$CurveDetail"
    ) -join "&"

    $cueUri = "$ApiBase/api/analyses/$JobId/visual-cues/export?$query"

    try {
        Invoke-WebRequest `
            -UseBasicParsing `
            -Uri $cueUri `
            -OutFile $cuePath `
            -TimeoutSec 120
    }
    catch {
        Remove-Item -LiteralPath $cuePath -Force -ErrorAction SilentlyContinue

        throw @"
Cue-sheet export failed.

If the API reports visual_features_unavailable, the job predates the private
20 Hz visual-feature artifact or that artifact has expired. Re-analyze the WAV
and export from the new completed job. The public analysis JSON alone cannot
reconstruct the Deep stem curves.
"@
    }

    if (-not (Test-Path -LiteralPath $cuePath -PathType Leaf)) {
        throw "The cue export did not create $cuePath."
    }

    try {
        $cue = Get-Content -LiteralPath $cuePath -Raw | ConvertFrom-Json
    }
    catch {
        throw "The downloaded cue sheet is not valid JSON."
    }

    if ($null -eq $cue.timeline -or [int]$cue.timeline.frameEnd -lt 1) {
        throw "The cue sheet has an invalid timeline."
    }

    if ($null -eq $cue.curves) {
        throw "The cue sheet contains no curves."
    }

    $curveNames = @($cue.curves.PSObject.Properties.Name)

    if ($curveNames -notcontains "masterEnergy") {
        throw "The required masterEnergy curve is missing."
    }

    $beatCount = @($cue.beats).Count
    $onsetCount = @($cue.onsets).Count
    $sectionCount = @($cue.sections).Count
    $transitionCount = @($cue.transitions).Count

    Write-Host "Cue schema: $($cue.schemaVersion)" -ForegroundColor Green
    Write-Host "Frame end: $($cue.timeline.frameEnd)" -ForegroundColor Green
    Write-Host "Beats: $beatCount"
    Write-Host "Onsets: $onsetCount"
    Write-Host "Sections: $sectionCount"
    Write-Host "Transitions: $transitionCount"
    Write-Host "Curves: $($curveNames -join ', ')"

    Write-Step "Building the Blender $VisualizerPreset scene"

    $sceneManifestPath = [IO.Path]::ChangeExtension($blendPath, ".manifest.json")
    $buildStartedAt = [DateTime]::UtcNow

    Invoke-NativeChecked `
        -Executable $resolvedBlender `
        -ArgumentList @(
            "--background",
            "--python-exit-code", "1",
            "--python", $buildScript,
            "--",
            "--cues", $cuePath,
            "--audio", $resolvedAudio,
            "--preset", $VisualizerPreset,
            "--seed", [string]$Seed,
            "--config", $resolvedConfigPath,
            "--output", $blendPath
        ) `
        -Description "Blender visualizer build" `
        -DisplayCommand "blender.exe (scene build; local audio input redacted)"

    [void](Assert-CurrentBuildArtifacts `
        -BlendPath $blendPath `
        -ManifestPath $sceneManifestPath `
        -ExpectedConfig $resolvedVisualizerConfig `
        -ExpectedPreset $VisualizerPreset `
        -ExpectedSeed $Seed `
        -StartedAt $buildStartedAt)

    Write-Host "Created and validated: $blendPath" -ForegroundColor Green

    if (-not $SkipPreview) {
        Write-Step "Rendering the bounded preview"

        $previewStartedAt = [DateTime]::UtcNow

        $renderArguments = @(
            "--background",
            $blendPath,
            "--python-exit-code", "1",
            "--python", $renderScript,
            "--",
            "--output", $previewDirectory,
            "--width", [string]$PreviewWidth,
            "--height", [string]$PreviewHeight
        )

        $ffmpegExe = Resolve-FfmpegExecutable

        if ($null -ne $ffmpegExe) {
            Write-Host "Using FFmpeg fallback: $ffmpegExe"
            $renderArguments += @("--ffmpeg", $ffmpegExe)
        }
        else {
            Write-Warning "FFmpeg.exe was not found on the host. Blender will attempt its native preview path; still renders may succeed even if movie encoding is unavailable."
        }

        Invoke-NativeChecked `
            -Executable $resolvedBlender `
            -ArgumentList $renderArguments `
            -Description "Blender preview render"

        $previewManifestPath = Join-Path $previewDirectory "preview-manifest.json"
        [void](Assert-CurrentPreviewArtifacts `
            -PreviewDirectory $previewDirectory `
            -ManifestPath $previewManifestPath `
            -ExpectedConfig $resolvedVisualizerConfig `
            -ExpectedPreset $VisualizerPreset `
            -ExpectedClipName $previewClipName `
            -ExpectedWidth $PreviewWidth `
            -ExpectedHeight $PreviewHeight `
            -ExpectedFps $Fps `
            -StartedAt $previewStartedAt)

        Write-Host "Preview validated: $previewDirectory" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "Completed successfully." -ForegroundColor Green
    Write-Host "Preset: $VisualizerPreset"
    Write-Host "Config: $resolvedConfigPath"
    Write-Host "Cue sheet: $cuePath"
    Write-Host "Blend file: $blendPath"

    if (-not $SkipPreview) {
        Write-Host "Preview: $previewDirectory"
    }
}
catch {
    Write-Host ""
    Write-Host "Visualizer workflow failed:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
