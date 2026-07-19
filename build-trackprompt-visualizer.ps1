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

    [int]$Seed = 84291,

    [int]$PreviewWidth = 1280,

    [int]$PreviewHeight = 720,

    [switch]$SkipPreview
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Write-Host "> $Executable $($ArgumentList -join ' ')" -ForegroundColor DarkGray

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

    $outputDirectory = Join-Path $RepoRoot "test-output\private-real-track"
    $previewDirectory = Join-Path $outputDirectory "preview"
    $cuePath = Join-Path $outputDirectory "visual-cues.json"
    $blendPath = Join-Path $outputDirectory "trip-to-andromeda-abstract.blend"

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

    Write-Step "Building the Blender scene"

    Invoke-NativeChecked `
        -Executable $resolvedBlender `
        -ArgumentList @(
            "--background",
            "--python", $buildScript,
            "--",
            "--cues", $cuePath,
            "--audio", $resolvedAudio,
            "--preset", "abstract-geometry",
            "--seed", [string]$Seed,
            "--output", $blendPath
        ) `
        -Description "Blender visualizer build"

    if (-not (Test-Path -LiteralPath $blendPath -PathType Leaf)) {
        throw "Blender returned success but did not create $blendPath."
    }

    Write-Host "Created: $blendPath" -ForegroundColor Green

    if (-not $SkipPreview) {
        Write-Step "Rendering the bounded preview"

        $renderArguments = @(
            "--background",
            $blendPath,
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

        Write-Host "Preview directory: $previewDirectory" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "Completed successfully." -ForegroundColor Green
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
