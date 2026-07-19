#requires -Version 5.1
<#
.SYNOPSIS
    Runs the complete local TrackPrompt-to-Blender visualizer workflow.

.DESCRIPTION
    This script:

      1. Validates or starts the TrackPrompt full-GPU Docker stack.
      2. Reads the live OpenAPI contract for POST /api/analyses.
      3. Uploads the supplied audio through the real TrackPrompt API.
      4. Saves the returned job ID immediately.
      5. Polls the job until completion or failure.
      6. Exports the completed analysis and Blender visual cue sheet.
      7. Validates the cue sheet and continuous master curve.
      8. Builds an abstract-geometry Blender scene.
      9. Renders the bounded Blender preview unless -SkipPreview is supplied.
     10. Writes a run manifest containing all generated artifact paths.

    The script uses the same local API as the browser UI. It does not upload
    audio to any external service and never deletes the TrackPrompt job unless
    -DeleteJobAfterSuccess is explicitly supplied. -BuildStack always rebuilds
    and recreates the current backend and frontend. Otherwise the default
    -AutoRebuildStaleBackend behavior checks both visual-cue OpenAPI operations
    and, only when needed, performs one backend-only build/recreation without
    removing named data or model-cache volumes. Pass
    -AutoRebuildStaleBackend:$false to disable that bounded repair.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass `
      -File .\run-trackprompt-to-blender.ps1 `
      -AudioPath "C:\Music\track.wav" `
      -ConfirmPermission `
      -ConfirmLyricsConsent

.EXAMPLE
    # Reuse the current images and skip lyrics:
    .\run-trackprompt-to-blender.ps1 `
      -AudioPath "C:\Music\track.wav" `
      -ConfirmPermission `
      -EnableLyrics:$false

.EXAMPLE
    # Rebuild the Docker services from current source before running:
    .\run-trackprompt-to-blender.ps1 `
      -AudioPath "C:\Music\track.wav" `
      -ConfirmPermission `
      -ConfirmLyricsConsent `
      -BuildStack

.NOTES
    Compatible with Windows PowerShell 5.1.

    Output layout:
      test-output\system-runs\<timestamp>\
        job-id.txt
        upload-response.json
        health-before.json
        health.json
        health-after.json
        capabilities.json
        openapi-before.json
        openapi-after.json
        job-final.json
        analysis.json
        analysis.md
        visual-cues.json
        cue-summary.json
        trackprompt-abstract.blend
        trackprompt-abstract.manifest.json
        blender-build-result.json
        preview\
          preview-manifest.json
          trackprompt-preview.mp4
        run-manifest.json

      test-output\last-trackprompt-job-id.txt
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AudioPath,

    [ValidateSet("fast", "deep")]
    [string]$Mode = "deep",

    [bool]$EnableGenre = $true,

    [bool]$EnableLyrics = $true,

    [bool]$DeriveAbstractThemes = $false,

    [switch]$ConfirmPermission,

    [switch]$ConfirmLyricsConsent,

    [bool]$StartStack = $true,

    [switch]$BuildStack,

    [bool]$AutoRebuildStaleBackend = $true,

    [string]$ApiBase = "http://127.0.0.1:8000",

    [string]$BlenderExe = "",

    [ValidateSet(24, 25, 30, 50, 60)]
    [int]$Fps = 30,

    [ValidateSet("compact", "balanced", "detailed")]
    [string]$CurveDetail = "balanced",

    [int]$Seed = 84291,

    [int]$AnalysisTimeoutMinutes = 45,

    [int]$PollSeconds = 5,

    [int]$PreviewWidth = 640,

    [int]$PreviewHeight = 360,

    [switch]$SkipPreview,

    [switch]$DeleteJobAfterSuccess,

    [hashtable]$AdditionalUploadFields = @{}
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:RepoRoot = ""
$script:ComposePrefix = @()
$script:RunDirectory = ""
$script:RunManifest = [ordered]@{}
$script:JobId = $null
$script:StackWasStarted = $false
$script:BackendRepairAttempted = $false
$script:LastApiHealthObservation = $null

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host "OK: $Message" -ForegroundColor Green
}

function Write-WarningMessage {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host "WARNING: $Message" -ForegroundColor Yellow
}

function Write-Utf8File {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    $json = $Value | ConvertTo-Json -Depth 100
    Write-Utf8File -Path $Path -Content ($json + [Environment]::NewLine)
}

function Save-RunManifest {
    if (
        [string]::IsNullOrWhiteSpace($script:RunDirectory) -or
        -not (Test-Path -LiteralPath $script:RunDirectory -PathType Container)
    ) {
        return
    }

    $script:RunManifest["updatedAt"] = (
        Get-Date
    ).ToUniversalTime().ToString("o")

    Write-JsonFile `
        -Path (Join-Path $script:RunDirectory "run-manifest.json") `
        -Value $script:RunManifest
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description,
        [string[]]$SensitiveValues = @()
    )

    $displayArguments = @(
        foreach ($argument in $ArgumentList) {
            if ($SensitiveValues -contains $argument) {
                "<redacted-local-input>"
            }
            else {
                $argument
            }
        }
    )

    $displayExecutable = if ([IO.Path]::IsPathRooted($Executable)) {
        [IO.Path]::GetFileName($Executable)
    }
    else {
        $Executable
    }

    Write-Host "> $displayExecutable $($displayArguments -join ' ')" -ForegroundColor DarkGray

    $previousPreference = $ErrorActionPreference

    try {
        # Native programs often use stderr for ordinary progress. Windows
        # PowerShell 5.1 must not turn that progress into a terminating error.
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

function Invoke-NativeCaptured {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description,
        [switch]$AllowFailure,
        [switch]$Quiet
    )

    if (-not $Quiet) {
        Write-Host "> $Executable $($ArgumentList -join ' ')" -ForegroundColor DarkGray
    }

    $previousPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"
        $captured = @(& $Executable @ArgumentList 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    $lines = @($captured | ForEach-Object { [string]$_ })

    if (-not $Quiet) {
        foreach ($line in $lines) {
            Write-Host $line
        }
    }

    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "$Description failed with exit code $exitCode."
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Lines = $lines
        Text = ($lines -join [Environment]::NewLine)
    }
}

function Assert-NativeJsonSuccess {
    param(
        [Parameter(Mandatory = $true)]$NativeResult,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $jsonResult = $null

    for ($index = $NativeResult.Lines.Count - 1; $index -ge 0; $index--) {
        $line = ([string]$NativeResult.Lines[$index]).Trim()

        if (-not ($line.StartsWith("{") -and $line.EndsWith("}"))) {
            continue
        }

        try {
            $candidate = $line | ConvertFrom-Json

            if ($null -ne $candidate.PSObject.Properties["ok"]) {
                $jsonResult = $candidate
                break
            }
        }
        catch {
            continue
        }
    }

    if ($null -eq $jsonResult) {
        throw "$Description returned no structured completion result."
    }

    if ($jsonResult.ok -ne $true) {
        $errorCode = Get-DirectPropertyValue `
            -Node (Get-DirectPropertyValue -Node $jsonResult -Names @("error")) `
            -Names @("code")
        throw "$Description reported failure: $errorCode"
    }

    return $jsonResult
}

function Invoke-ComposeChecked {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Invoke-NativeChecked `
        -Executable "docker" `
        -ArgumentList ($script:ComposePrefix + $Arguments) `
        -Description $Description
}

function Invoke-ComposeCaptured {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description,
        [switch]$AllowFailure,
        [switch]$Quiet
    )

    return Invoke-NativeCaptured `
        -Executable "docker" `
        -ArgumentList ($script:ComposePrefix + $Arguments) `
        -Description $Description `
        -AllowFailure:$AllowFailure `
        -Quiet:$Quiet
}

function Wait-ComposeServiceHealthy {
    param(
        [Parameter(Mandatory = $true)][string]$Service,
        [int]$Attempts = 90
    )

    $lastStatus = "container-not-found"

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $containerResult = Invoke-ComposeCaptured `
            -Arguments @("ps", "-q", $Service) `
            -Description "Compose $Service container lookup" `
            -AllowFailure `
            -Quiet
        $containerId = $containerResult.Lines |
            Where-Object { $_.Trim() -match '^[0-9a-f]{12,64}$' } |
            Select-Object -Last 1

        if (-not [string]::IsNullOrWhiteSpace([string]$containerId)) {
            $inspection = Invoke-NativeCaptured `
                -Executable "docker" `
                -ArgumentList @(
                    "inspect",
                    "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                    ([string]$containerId).Trim()
                ) `
                -Description "Docker $Service health inspection" `
                -AllowFailure `
                -Quiet
            $status = $inspection.Lines |
                ForEach-Object { $_.Trim().ToLowerInvariant() } |
                Where-Object { $_ -match '^(healthy|starting|unhealthy|running|exited|created|dead|paused|restarting)$' } |
                Select-Object -Last 1

            if (-not [string]::IsNullOrWhiteSpace([string]$status)) {
                $lastStatus = [string]$status
            }

            if ($lastStatus -eq "healthy") {
                return
            }
        }

        Start-Sleep -Seconds 2
    }

    throw "Compose service '$Service' did not become healthy. Last status: $lastStatus"
}

function Test-ApiHealth {
    param([int]$TimeoutSeconds = 5)

    try {
        $health = Invoke-RestMethod `
            -Method Get `
            -Uri "$ApiBase/api/health" `
            -TimeoutSec $TimeoutSeconds
        $script:LastApiHealthObservation = $health
        $statusProperty = $health.PSObject.Properties["status"]

        if (
            $null -ne $statusProperty -and
            ([string]$statusProperty.Value).Trim().ToLowerInvariant() -eq "ok"
        ) {
            return $health
        }

        return $null
    }
    catch {
        $script:LastApiHealthObservation = $null
        return $null
    }
}

function Wait-ApiHealth {
    param([int]$Attempts = 90)

    $lastObservation = "No health response was received."

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $health = Invoke-RestMethod `
                -Method Get `
                -Uri "$ApiBase/api/health" `
                -TimeoutSec 5
            $script:LastApiHealthObservation = $health
            $statusProperty = $health.PSObject.Properties["status"]
            $status = if ($null -ne $statusProperty) {
                ([string]$statusProperty.Value).Trim().ToLowerInvariant()
            }
            else {
                "missing"
            }

            if ($status -eq "ok") {
                return $health
            }

            $lastObservation = "The API responded with health status '$status'."
        }
        catch {
            $script:LastApiHealthObservation = $null
            $lastObservation = $_.Exception.Message
        }

        Start-Sleep -Seconds 2
    }

    throw "TrackPrompt did not become healthy at $ApiBase. Last observation: $lastObservation"
}

function Get-LiveOpenApi {
    return Invoke-RestMethod `
        -Method Get `
        -Uri "$ApiBase/openapi.json" `
        -TimeoutSec 30
}

function Test-OpenApiOperation {
    param(
        [Parameter(Mandatory = $true)]$OpenApi,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Method
    )

    if ($null -eq $OpenApi -or $null -eq $OpenApi.paths) {
        return $false
    }

    $pathProperty = $OpenApi.paths.PSObject.Properties[$Path]

    if ($null -eq $pathProperty) {
        return $false
    }

    return $null -ne $pathProperty.Value.PSObject.Properties[
        $Method.ToLowerInvariant()
    ]
}

function Get-VisualizerRouteStatus {
    param([Parameter(Mandatory = $true)]$OpenApi)

    $postPath = "/api/analyses/{job_id}/visual-cues"
    $exportPath = "/api/analyses/{job_id}/visual-cues/export"
    $postAvailable = Test-OpenApiOperation `
        -OpenApi $OpenApi `
        -Path $postPath `
        -Method "post"
    $exportAvailable = Test-OpenApiOperation `
        -OpenApi $OpenApi `
        -Path $exportPath `
        -Method "get"

    return [pscustomobject]@{
        PostVisualCues = $postAvailable
        GetVisualCuesExport = $exportAvailable
        Complete = ($postAvailable -and $exportAvailable)
    }
}

function Write-ApiObservation {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        $Value,
        [string]$ErrorMessage = ""
    )

    if ($null -ne $Value) {
        Write-JsonFile -Path $Path -Value $Value
        return
    }

    Write-JsonFile `
        -Path $Path `
        -Value ([ordered]@{
            available = $false
            observedAt = (Get-Date).ToUniversalTime().ToString("o")
            error = $ErrorMessage
        })
}

function Save-FailureDiagnostics {
    if (
        [string]::IsNullOrWhiteSpace($script:RunDirectory) -or
        -not (Test-Path -LiteralPath $script:RunDirectory -PathType Container)
    ) {
        return
    }

    $diagnosticsDirectory = Join-Path $script:RunDirectory "diagnostics"
    New-Item -ItemType Directory -Force -Path $diagnosticsDirectory | Out-Null

    foreach ($snapshot in @(
        [pscustomobject]@{
            Name = "health-at-failure.json"
            RootName = "health.json"
            OutputName = "health"
            Uri = "$ApiBase/api/health"
        },
        [pscustomobject]@{
            Name = "capabilities-at-failure.json"
            RootName = "capabilities.json"
            OutputName = "capabilities"
            Uri = "$ApiBase/api/capabilities"
        },
        [pscustomobject]@{
            Name = "openapi-at-failure.json"
            RootName = "openapi-after.json"
            OutputName = "openApiAfter"
            Uri = "$ApiBase/openapi.json"
        }
    )) {
        $rootSnapshotPath = Join-Path $script:RunDirectory $snapshot.RootName

        try {
            $value = Invoke-RestMethod -Method Get -Uri $snapshot.Uri -TimeoutSec 10
            Write-JsonFile `
                -Path (Join-Path $diagnosticsDirectory $snapshot.Name) `
                -Value $value

            if (-not (Test-Path -LiteralPath $rootSnapshotPath -PathType Leaf)) {
                Write-JsonFile -Path $rootSnapshotPath -Value $value
            }
        }
        catch {
            $unavailable = [ordered]@{
                available = $false
                error = $_.Exception.Message
            }
            Write-JsonFile `
                -Path (Join-Path $diagnosticsDirectory $snapshot.Name) `
                -Value $unavailable

            if (-not (Test-Path -LiteralPath $rootSnapshotPath -PathType Leaf)) {
                Write-JsonFile -Path $rootSnapshotPath -Value $unavailable
            }
        }

        $script:RunManifest["outputs"][$snapshot.OutputName] = $rootSnapshotPath
    }

    foreach ($beforeSnapshot in @(
        [pscustomobject]@{ Name = "health-before.json"; OutputName = "healthBefore" },
        [pscustomobject]@{ Name = "openapi-before.json"; OutputName = "openApiBefore" }
    )) {
        $beforePath = Join-Path $script:RunDirectory $beforeSnapshot.Name

        if (-not (Test-Path -LiteralPath $beforePath -PathType Leaf)) {
            Write-ApiObservation `
                -Path $beforePath `
                -Value $null `
                -ErrorMessage "The workflow failed before this snapshot could be collected."
        }

        $script:RunManifest["outputs"][$beforeSnapshot.OutputName] = $beforePath
    }

    if ($script:ComposePrefix.Count -gt 0 -and (Get-Command docker -ErrorAction SilentlyContinue)) {
        foreach ($diagnostic in @(
            [pscustomobject]@{
                Name = "compose-ps.txt"
                Arguments = @("ps", "--all")
                Description = "Compose status"
            },
            [pscustomobject]@{
                Name = "backend-logs.txt"
                Arguments = @("logs", "--no-color", "--tail", "250", "backend")
                Description = "Backend logs"
            }
        )) {
            try {
                $result = Invoke-ComposeCaptured `
                    -Arguments $diagnostic.Arguments `
                    -Description $diagnostic.Description `
                    -AllowFailure `
                    -Quiet
                Write-Utf8File `
                    -Path (Join-Path $diagnosticsDirectory $diagnostic.Name) `
                    -Content ($result.Text + [Environment]::NewLine)
            }
            catch {
                Write-Utf8File `
                    -Path (Join-Path $diagnosticsDirectory $diagnostic.Name) `
                    -Content ("Diagnostic failed: $($_.Exception.Message)" + [Environment]::NewLine)
            }
        }
    }

    $script:RunManifest["outputs"]["diagnosticsDirectory"] = $diagnosticsDirectory
    Save-RunManifest
}

function Resolve-BlenderExecutable {
    param([string]$ExplicitPath)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        if (-not (Test-Path -LiteralPath $ExplicitPath -PathType Leaf)) {
            throw "Blender executable was not found: $ExplicitPath"
        }

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

    $candidates = New-Object System.Collections.Generic.List[string]

    if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
        [void]$candidates.Add($command.Source)
    }

    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe",
        "$env:USERPROFILE\scoop\shims\ffmpeg.exe",
        "C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        "C:\ffmpeg\bin\ffmpeg.exe",
        "C:\Program Files\ffmpeg\bin\ffmpeg.exe"
    )) {
        if (-not [string]::IsNullOrWhiteSpace($candidate)) {
            [void]$candidates.Add($candidate)
        }
    }

    $wingetPackages = Join-Path `
        $env:LOCALAPPDATA `
        "Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"

    if (Test-Path -LiteralPath $wingetPackages -PathType Container) {
        foreach ($packageDirectory in @(
            Get-ChildItem `
                -LiteralPath $wingetPackages `
                -Directory `
                -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending
        )) {
            $packageBinary = Join-Path $packageDirectory.FullName "bin\ffmpeg.exe"

            if (Test-Path -LiteralPath $packageBinary -PathType Leaf) {
                [void]$candidates.Add($packageBinary)
            }
        }

        foreach ($match in @(
            Get-ChildItem `
                -LiteralPath $wingetPackages `
                -Filter "ffmpeg.exe" `
                -File `
                -Recurse `
                -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending
        )) {
            [void]$candidates.Add($match.FullName)
        }
    }

    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)

    foreach ($candidate in $candidates) {
        if (
            [string]::IsNullOrWhiteSpace($candidate) -or
            -not (Test-Path -LiteralPath $candidate -PathType Leaf)
        ) {
            continue
        }

        $resolved = (Resolve-Path -LiteralPath $candidate).Path

        if (-not $seen.Add($resolved)) {
            continue
        }

        try {
            $probe = Invoke-NativeCaptured `
                -Executable $resolved `
                -ArgumentList @("-version") `
                -Description "FFmpeg executable validation" `
                -AllowFailure `
                -Quiet

            if ($probe.ExitCode -eq 0) {
                return $resolved
            }
        }
        catch {
            # Broken app aliases can resolve through Get-Command but still fail
            # at process creation. Continue to the real package binary.
        }
    }

    return $null
}

function Get-DirectPropertyValue {
    param(
        $Node,
        [Parameter(Mandatory = $true)][string[]]$Names
    )

    if ($null -eq $Node) {
        return $null
    }

    foreach ($name in $Names) {
        $property = $Node.PSObject.Properties[$name]

        if ($null -ne $property) {
            return $property.Value
        }
    }

    return $null
}

function Test-GuidString {
    param($Value)

    if ($null -eq $Value) {
        return $false
    }

    $parsed = [Guid]::Empty
    return [Guid]::TryParse([string]$Value, [ref]$parsed)
}

function Get-LifecycleNodes {
    param(
        [Parameter(Mandatory = $true)]$Root,
        [int]$MaximumDepth = 5
    )

    $result = New-Object System.Collections.ArrayList
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue([pscustomobject]@{ Node = $Root; Depth = 0 })
    $containerNames = @(
        "job",
        "lifecycle",
        "metadata",
        "data",
        "response",
        "payload"
    )

    while ($queue.Count -gt 0) {
        $item = $queue.Dequeue()
        $node = $item.Node

        if ($null -eq $node) {
            continue
        }

        [void]$result.Add($node)

        if ([int]$item.Depth -ge $MaximumDepth) {
            continue
        }

        foreach ($containerName in $containerNames) {
            $nested = Get-DirectPropertyValue -Node $node -Names @($containerName)

            if (
                $null -ne $nested -and
                -not ($nested -is [string]) -and
                -not ($nested -is [ValueType])
            ) {
                $queue.Enqueue([pscustomobject]@{
                    Node = $nested
                    Depth = ([int]$item.Depth + 1)
                })
            }
        }
    }

    return @($result)
}

function Get-LifecyclePropertyValue {
    param(
        [Parameter(Mandatory = $true)]$Root,
        [Parameter(Mandatory = $true)][string[]]$Names
    )

    foreach ($node in @(Get-LifecycleNodes -Root $Root)) {
        $value = Get-DirectPropertyValue -Node $node -Names $Names

        if ($null -ne $value) {
            return $value
        }
    }

    return $null
}

function Find-JobId {
    param($Response)

    $preferred = Get-DirectPropertyValue `
        -Node $Response `
        -Names @("jobId", "job_id", "id")

    if (Test-GuidString -Value $preferred) {
        return ([Guid]$preferred).ToString()
    }

    foreach ($containerName in @("job", "lifecycle", "metadata", "data")) {
        $container = Get-DirectPropertyValue `
            -Node $Response `
            -Names @($containerName)

        if ($null -eq $container) {
            continue
        }

        $candidate = Get-DirectPropertyValue `
            -Node $container `
            -Names @("jobId", "job_id", "id")

        if (Test-GuidString -Value $candidate) {
            return ([Guid]$candidate).ToString()
        }
    }

    $json = $Response | ConvertTo-Json -Depth 100
    $matches = [regex]::Matches(
        $json,
        '(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b'
    )

    $unique = @(
        $matches |
            ForEach-Object { $_.Value.ToLowerInvariant() } |
            Select-Object -Unique
    )

    if ($unique.Count -eq 1) {
        return $unique[0]
    }

    throw "The upload response did not contain one unambiguous job UUID."
}

function Get-JobState {
    param($JobResponse)

    # Prefer explicit lifecycle fields at any known wrapper depth. A generic
    # wrapper can itself have status="ok", which must not hide nested state.
    $directState = Get-LifecyclePropertyValue `
        -Root $JobResponse `
        -Names @("state", "jobState", "job_state")

    if ($null -ne $directState) {
        return ([string]$directState).Trim().ToLowerInvariant()
    }

    foreach ($node in @(Get-LifecycleNodes -Root $JobResponse)) {
        $status = Get-DirectPropertyValue -Node $node -Names @("status")

        if ($null -eq $status -or $status -is [System.Management.Automation.PSCustomObject]) {
            continue
        }

        $normalized = ([string]$status).Trim().ToLowerInvariant()

        if ($normalized -match '^(queued|pending|validating|decoding|analyzing|analyzing_core|separating_stems|analyzing_deep|transcribing_lyrics|deriving_lyrical_themes|tagging_genre|generating_prompt|generating_candidates|validating_candidates|running|processing|completed|complete|completed_with_warnings|succeeded|success|done|failed|error|cancelled|canceled|expired|deleted)$') {
            return $normalized
        }
    }

    $analysis = Get-DirectPropertyValue -Node $JobResponse -Names @("analysis", "result")

    if ($null -ne $analysis) {
        return "completed"
    }

    return "unknown"
}

function Get-JobProgress {
    param($JobResponse)

    return Get-LifecyclePropertyValue `
        -Root $JobResponse `
        -Names @("progress", "progressPercent", "progress_percent")
}

function Get-JobStage {
    param($JobResponse)

    return Get-LifecyclePropertyValue `
        -Root $JobResponse `
        -Names @("stage", "stageName", "stage_name", "message")
}

function Resolve-JsonPointer {
    param(
        [Parameter(Mandatory = $true)]$Root,
        [Parameter(Mandatory = $true)][string]$Reference
    )

    if (-not $Reference.StartsWith("#/")) {
        throw "Only local OpenAPI references are supported: $Reference"
    }

    $current = $Root
    $segments = $Reference.Substring(2).Split("/")

    foreach ($rawSegment in $segments) {
        $segment = $rawSegment.Replace("~1", "/").Replace("~0", "~")
        $property = $current.PSObject.Properties[$segment]

        if ($null -eq $property) {
            throw "OpenAPI reference segment was not found: $segment"
        }

        $current = $property.Value
    }

    return $current
}

function Merge-OpenApiSchema {
    param(
        [Parameter(Mandatory = $true)]$OpenApi,
        [Parameter(Mandatory = $true)]$Schema
    )

    $properties = [ordered]@{}
    $required = New-Object System.Collections.Generic.List[string]
    $visitedReferences = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)

    function Add-Schema {
        param($Node, [int]$Depth = 0)

        if ($null -eq $Node) {
            return
        }

        if ($Depth -gt 24) {
            throw "OpenAPI schema composition exceeded the safe recursion limit."
        }

        $refProperty = $Node.PSObject.Properties['$ref']

        if ($null -ne $refProperty) {
            $reference = [string]$refProperty.Value

            if ($visitedReferences.Add($reference)) {
                Add-Schema `
                    (Resolve-JsonPointer -Root $OpenApi -Reference $reference) `
                    ($Depth + 1)
            }

            return
        }

        foreach ($compositionName in @("allOf", "anyOf", "oneOf")) {
            $composition = $Node.PSObject.Properties[$compositionName]

            if ($null -ne $composition) {
                foreach ($part in @($composition.Value)) {
                    Add-Schema $part ($Depth + 1)
                }
            }
        }

        $propertiesProperty = $Node.PSObject.Properties["properties"]

        if ($null -ne $propertiesProperty) {
            foreach ($property in $propertiesProperty.Value.PSObject.Properties) {
                $properties[$property.Name] = $property.Value
            }
        }

        $requiredProperty = $Node.PSObject.Properties["required"]

        if ($null -ne $requiredProperty) {
            foreach ($name in @($requiredProperty.Value)) {
                if (-not $required.Contains([string]$name)) {
                    [void]$required.Add([string]$name)
                }
            }
        }
    }

    Add-Schema $Schema

    return [pscustomobject]@{
        Properties = $properties
        Required = @($required)
    }
}

function Get-UploadContract {
    param([Parameter(Mandatory = $true)]$OpenApi)

    $pathProperty = $OpenApi.paths.PSObject.Properties["/api/analyses"]

    if ($null -eq $pathProperty -or $null -eq $pathProperty.Value.post) {
        throw "OpenAPI does not expose POST /api/analyses."
    }

    $post = $pathProperty.Value.post
    $requestBody = Get-DirectPropertyValue -Node $post -Names @("requestBody")

    if ($null -eq $requestBody) {
        throw "POST /api/analyses has no OpenAPI request body."
    }

    $requestBodyReference = $requestBody.PSObject.Properties['$ref']

    if ($null -ne $requestBodyReference) {
        $requestBody = Resolve-JsonPointer `
            -Root $OpenApi `
            -Reference ([string]$requestBodyReference.Value)
    }

    $content = Get-DirectPropertyValue -Node $requestBody -Names @("content")

    if ($null -eq $content) {
        throw "POST /api/analyses has no OpenAPI request content."
    }

    $multipartProperty = $content.PSObject.Properties |
        Where-Object {
            ([string]$_.Name).Split(";", 2)[0].Trim().ToLowerInvariant() -eq
                "multipart/form-data"
        } |
        Select-Object -First 1

    if ($null -eq $multipartProperty) {
        throw "POST /api/analyses is not described as multipart/form-data."
    }

    $schema = Get-DirectPropertyValue `
        -Node $multipartProperty.Value `
        -Names @("schema")

    if ($null -eq $schema) {
        throw "POST /api/analyses multipart content has no schema."
    }

    return Merge-OpenApiSchema `
        -OpenApi $OpenApi `
        -Schema $schema
}

function Get-SchemaPrimitiveType {
    param(
        [Parameter(Mandatory = $true)]$OpenApi,
        $Schema,
        [int]$Depth = 0
    )

    if ($null -eq $Schema) {
        return $null
    }

    if ($Depth -gt 24) {
        throw "OpenAPI property schema exceeded the safe recursion limit."
    }

    $refProperty = $Schema.PSObject.Properties['$ref']

    if ($null -ne $refProperty) {
        return Get-SchemaPrimitiveType `
            -OpenApi $OpenApi `
            -Schema (Resolve-JsonPointer -Root $OpenApi -Reference ([string]$refProperty.Value)) `
            -Depth ($Depth + 1)
    }

    $typeProperty = $Schema.PSObject.Properties["type"]

    if ($null -ne $typeProperty -and [string]$typeProperty.Value -ne "null") {
        return [string]$typeProperty.Value
    }

    foreach ($compositionName in @("anyOf", "oneOf", "allOf")) {
        $composition = $Schema.PSObject.Properties[$compositionName]

        if ($null -eq $composition) {
            continue
        }

        foreach ($part in @($composition.Value)) {
            $resolvedType = Get-SchemaPrimitiveType `
                -OpenApi $OpenApi `
                -Schema $part `
                -Depth ($Depth + 1)

            if (-not [string]::IsNullOrWhiteSpace($resolvedType)) {
                return $resolvedType
            }
        }
    }

    return $null
}

function Get-SchemaFormat {
    param(
        [Parameter(Mandatory = $true)]$OpenApi,
        $Schema,
        [int]$Depth = 0
    )

    if ($null -eq $Schema) {
        return $null
    }

    if ($Depth -gt 24) {
        throw "OpenAPI property schema exceeded the safe recursion limit."
    }

    $refProperty = $Schema.PSObject.Properties['$ref']

    if ($null -ne $refProperty) {
        return Get-SchemaFormat `
            -OpenApi $OpenApi `
            -Schema (Resolve-JsonPointer -Root $OpenApi -Reference ([string]$refProperty.Value)) `
            -Depth ($Depth + 1)
    }

    $formatProperty = $Schema.PSObject.Properties["format"]

    if ($null -ne $formatProperty) {
        return ([string]$formatProperty.Value).ToLowerInvariant()
    }

    foreach ($compositionName in @("anyOf", "oneOf", "allOf")) {
        $composition = $Schema.PSObject.Properties[$compositionName]

        if ($null -eq $composition) {
            continue
        }

        foreach ($part in @($composition.Value)) {
            $resolvedFormat = Get-SchemaFormat `
                -OpenApi $OpenApi `
                -Schema $part `
                -Depth ($Depth + 1)

            if (-not [string]::IsNullOrWhiteSpace($resolvedFormat)) {
                return $resolvedFormat
            }
        }
    }

    return $null
}

function Get-SchemaDefault {
    param(
        [Parameter(Mandatory = $true)]$OpenApi,
        $Schema,
        [int]$Depth = 0
    )

    if ($null -eq $Schema) {
        return $null
    }

    if ($Depth -gt 24) {
        throw "OpenAPI property schema exceeded the safe recursion limit."
    }

    $refProperty = $Schema.PSObject.Properties['$ref']

    if ($null -ne $refProperty) {
        return Get-SchemaDefault `
            -OpenApi $OpenApi `
            -Schema (Resolve-JsonPointer -Root $OpenApi -Reference ([string]$refProperty.Value)) `
            -Depth ($Depth + 1)
    }

    $defaultProperty = $Schema.PSObject.Properties["default"]

    if ($null -ne $defaultProperty) {
        return $defaultProperty.Value
    }

    foreach ($compositionName in @("anyOf", "oneOf", "allOf")) {
        $composition = $Schema.PSObject.Properties[$compositionName]

        if ($null -eq $composition) {
            continue
        }

        foreach ($part in @($composition.Value)) {
            $value = Get-SchemaDefault `
                -OpenApi $OpenApi `
                -Schema $part `
                -Depth ($Depth + 1)

            if ($null -ne $value) {
                return $value
            }
        }
    }

    return $null
}

function Convert-FormValue {
    param($Value)

    if ($Value -is [bool]) {
        return $Value.ToString().ToLowerInvariant()
    }

    return [string]$Value
}

function New-UploadFieldPlan {
    param(
        [Parameter(Mandatory = $true)]$OpenApi,
        [Parameter(Mandatory = $true)]$Contract,
        [Parameter(Mandatory = $true)][hashtable]$Overrides
    )

    $formFields = [ordered]@{}
    $fileCandidates = New-Object System.Collections.Generic.List[string]
    $unresolvedRequired = New-Object System.Collections.Generic.List[string]
    $knownProperties = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)

    foreach ($propertyName in @($Contract.Properties.Keys)) {
        [void]$knownProperties.Add([string]$propertyName)
    }

    foreach ($overrideName in @($Overrides.Keys)) {
        if (-not $knownProperties.Contains([string]$overrideName)) {
            throw "AdditionalUploadFields contains a field that is absent from the live OpenAPI contract: $overrideName"
        }
    }

    foreach ($entry in $Contract.Properties.GetEnumerator()) {
        $name = [string]$entry.Key
        $schema = $entry.Value
        $normalized = ($name -replace '[^A-Za-z0-9]', '').ToLowerInvariant()
        $format = Get-SchemaFormat -OpenApi $OpenApi -Schema $schema
        $primitiveType = Get-SchemaPrimitiveType -OpenApi $OpenApi -Schema $schema

        if (
            $format -eq "binary" -or
            (
                $primitiveType -eq "string" -and
                $normalized -in @("file", "audio", "audiofile", "upload", "media", "sourcefile")
            )
        ) {
            [void]$fileCandidates.Add($name)
            continue
        }

        if ($Overrides.ContainsKey($name)) {
            $formFields[$name] = Convert-FormValue $Overrides[$name]
            continue
        }

        $valueWasAssigned = $true
        $value = $null

        if ($normalized -match 'lyric.*(consent|permission|confirm|right)') {
            $value = [bool]($EnableLyrics -and $ConfirmLyricsConsent)
        }
        elseif (
            $normalized.Contains("permission") -or
            $normalized.Contains("authorized") -or
            $normalized.Contains("authorised") -or
            $normalized.Contains("ownership") -or
            $normalized.Contains("rightstouse") -or
            $normalized.Contains("confirmrights")
        ) {
            $value = [bool]$ConfirmPermission
        }
        elseif (
            $normalized -eq "mode" -or
            $normalized.Contains("analysismode") -or
            $normalized.Contains("requestedmode")
        ) {
            $value = $Mode
        }
        elseif (
            $normalized.Contains("abstracttheme") -or
            $normalized.Contains("derivetheme") -or
            ($normalized.Contains("derive") -and $normalized.Contains("lyric") -and $normalized.Contains("theme"))
        ) {
            $value = $DeriveAbstractThemes
        }
        elseif ($normalized.Contains("lyric") -or $normalized.Contains("transcript")) {
            $value = $EnableLyrics
        }
        elseif (
            $normalized.Contains("genre") -or
            $normalized.Contains("musictag") -or
            $normalized.Contains("tagger")
        ) {
            $value = $EnableGenre
        }
        elseif (
            $normalized.Contains("visual") -or
            $normalized.Contains("cuefeature")
        ) {
            $value = $true
        }
        elseif ($normalized.Contains("demucs") -or $normalized.Contains("deep")) {
            $value = ($Mode -eq "deep")
        }
        elseif ($normalized.Contains("network") -or $normalized.Contains("remote")) {
            $value = $false
        }
        else {
            $defaultValue = Get-SchemaDefault -OpenApi $OpenApi -Schema $schema

            if ($null -ne $defaultValue) {
                # Omit optional defaults so the backend remains authoritative.
                $valueWasAssigned = $false
            }
            elseif ($Contract.Required -contains $name) {
                [void]$unresolvedRequired.Add($name)
                $valueWasAssigned = $false
            }
            else {
                $valueWasAssigned = $false
            }
        }

        if ($valueWasAssigned) {
            $formFields[$name] = Convert-FormValue $value
        }
    }

    if ($fileCandidates.Count -eq 0) {
        throw "The OpenAPI upload contract did not identify a binary audio field."
    }

    $preferredFileCandidates = @(
        $fileCandidates |
            Where-Object {
                (($_ -replace '[^A-Za-z0-9]', '').ToLowerInvariant()) -in
                    @("file", "audio", "audiofile", "sourcefile")
            }
    )

    if ($fileCandidates.Count -gt 1 -and $preferredFileCandidates.Count -ne 1) {
        throw "The OpenAPI upload contract contains multiple ambiguous binary fields: $($fileCandidates -join ', ')"
    }

    $fileField = if ($preferredFileCandidates.Count -eq 1) {
        [string]$preferredFileCandidates[0]
    }
    else {
        [string]$fileCandidates[0]
    }

    if ($Overrides.ContainsKey($fileField)) {
        throw "AdditionalUploadFields cannot override the binary audio field: $fileField"
    }

    if ($unresolvedRequired.Count -gt 0) {
        throw @"
The current upload contract contains required fields that the script could not
map automatically:

  $($unresolvedRequired -join ", ")

Supply them with:
  -AdditionalUploadFields @{ fieldName = 'value' }
"@
    }

    return [pscustomobject]@{
        FileField = $fileField
        FormFields = $formFields
    }
}

function Invoke-CurlUpload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$FileField,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)]$FormFields,
        [Parameter(Mandatory = $true)][string]$ResponsePath
    )

    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
        throw "curl.exe is required for multipart upload in Windows PowerShell 5.1."
    }

    $arguments = @(
        "--silent",
        "--show-error",
        "--location",
        "--request", "POST",
        "--header", "Accept: application/json",
        "--output", $ResponsePath,
        "--write-out", "%{http_code}",
        "--form", "$FileField=@$FilePath"
    )

    foreach ($entry in $FormFields.GetEnumerator()) {
        $arguments += @(
            "--form",
            "$($entry.Key)=$($entry.Value)"
        )
    }

    $result = Invoke-NativeCaptured `
        -Executable "curl.exe" `
        -ArgumentList ($arguments + @($Url)) `
        -Description "TrackPrompt audio upload" `
        -AllowFailure `
        -Quiet

    $httpCode = $result.Lines |
        Where-Object { $_.Trim() -match '^\d{3}$' } |
        Select-Object -Last 1

    $body = if (Test-Path -LiteralPath $ResponsePath -PathType Leaf) {
        Get-Content -LiteralPath $ResponsePath -Raw
    }
    else {
        ""
    }

    if (
        $result.ExitCode -ne 0 -or
        [string]::IsNullOrWhiteSpace([string]$httpCode) -or
        [int]$httpCode -lt 200 -or
        [int]$httpCode -ge 300
    ) {
        throw @"
TrackPrompt upload failed.

curl exit code: $($result.ExitCode)
HTTP status: $httpCode
Response:
$body
"@
    }

    if ([string]::IsNullOrWhiteSpace($body)) {
        throw "TrackPrompt accepted the upload but returned an empty response."
    }

    try {
        return $body | ConvertFrom-Json
    }
    catch {
        throw "TrackPrompt upload returned invalid JSON: $body"
    }
}

function Wait-AnalysisCompletion {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][datetime]$Deadline
    )

    $lastDisplay = ""

    while ((Get-Date).ToUniversalTime() -lt $Deadline) {
        try {
            $job = Invoke-RestMethod `
                -Method Get `
                -Uri "$ApiBase/api/analyses/$Id" `
                -TimeoutSec 20
        }
        catch {
            throw "Failed to read TrackPrompt job $Id. $($_.Exception.Message)"
        }

        Write-JsonFile `
            -Path (Join-Path $script:RunDirectory "job-latest.json") `
            -Value $job

        $state = Get-JobState -JobResponse $job
        $progress = Get-JobProgress -JobResponse $job
        $stage = Get-JobStage -JobResponse $job

        $display = "$state|$progress|$stage"

        if ($display -ne $lastDisplay) {
            $progressText = if ($null -ne $progress) {
                " $progress%"
            }
            else {
                ""
            }

            $stageText = if (-not [string]::IsNullOrWhiteSpace([string]$stage)) {
                " - $stage"
            }
            else {
                ""
            }

            Write-Host "TrackPrompt: $state$progressText$stageText"
            $lastDisplay = $display
        }

        switch -Regex ($state) {
            '^(completed|complete|completed_with_warnings|succeeded|success|done)$' {
                return $job
            }

            '^(failed|error)$' {
                $errorValue = Get-LifecyclePropertyValue `
                    -Root $job `
                    -Names @("error", "failure", "detail", "message")

                if ($null -ne $errorValue -and -not ($errorValue -is [string])) {
                    $errorValue = $errorValue | ConvertTo-Json -Depth 20 -Compress
                }

                throw "TrackPrompt analysis failed. $errorValue"
            }

            '^(cancelled|canceled|expired|deleted)$' {
                throw "TrackPrompt job entered terminal state: $state"
            }
        }

        Start-Sleep -Seconds $PollSeconds
    }

    throw "TrackPrompt analysis exceeded the $AnalysisTimeoutMinutes-minute timeout."
}

function Download-ApiArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Description,
        [switch]$Optional
    )

    try {
        Invoke-WebRequest `
            -UseBasicParsing `
            -Method Get `
            -Uri $Uri `
            -OutFile $Destination `
            -TimeoutSec 180
    }
    catch {
        Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue

        if ($Optional) {
            Write-WarningMessage "$Description could not be downloaded: $($_.Exception.Message)"
            return $false
        }

        throw "$Description download failed. $($_.Exception.Message)"
    }

    if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
        if ($Optional) {
            return $false
        }

        throw "$Description download returned no file."
    }

    return $true
}

function Test-FiniteNumber {
    param($Value)

    if (-not ($Value -is [ValueType])) {
        return $false
    }

    try {
        $number = [double]$Value
        return -not ([double]::IsNaN($number) -or [double]::IsInfinity($number))
    }
    catch {
        return $false
    }
}

function Assert-PublicCueValue {
    param(
        $Value,
        [string]$PropertyName = "",
        [int]$Depth = 0
    )

    if ($Depth -gt 64) {
        throw "The exported cue sheet exceeds the safe nesting limit."
    }

    $normalizedName = ($PropertyName -replace '[^A-Za-z0-9]', '').ToLowerInvariant()
    $privateNames = @(
        "displayname",
        "filename",
        "privatemetadata",
        "waveformpeaks",
        "lyrics",
        "transcript",
        "promptpackage",
        "sourceaudiopath",
        "uploadpath",
        "stempath",
        "modelcachepath"
    )

    if (-not [string]::IsNullOrWhiteSpace($normalizedName) -and $normalizedName -in $privateNames) {
        throw "The exported cue sheet contains a private field: $PropertyName"
    }

    if ($Value -is [string]) {
        $text = [string]$Value
        $normalized = $text.Replace("\", "/").ToLowerInvariant()

        if (
            $text -match '(?i)\b[a-z]:[\\/]' -or
            $normalized.StartsWith("/data/") -or
            $normalized.StartsWith("/home/") -or
            $normalized.StartsWith("/users/")
        ) {
            throw "The exported cue sheet contains a private filesystem path."
        }

        return
    }

    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($key in $Value.Keys) {
            Assert-PublicCueValue `
                -Value $Value[$key] `
                -PropertyName ([string]$key) `
                -Depth ($Depth + 1)
        }

        return
    }

    if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
        foreach ($item in $Value) {
            Assert-PublicCueValue -Value $item -Depth ($Depth + 1)
        }

        return
    }

    if (
        $null -ne $Value -and
        -not ($Value -is [ValueType]) -and
        @($Value.PSObject.Properties).Count -gt 0
    ) {
        foreach ($property in $Value.PSObject.Properties) {
            Assert-PublicCueValue `
                -Value $property.Value `
                -PropertyName $property.Name `
                -Depth ($Depth + 1)
        }
    }
}

function Assert-VisualCueSheet {
    param(
        [Parameter(Mandatory = $true)]$Cue,
        [Parameter(Mandatory = $true)][string]$ExpectedJobId,
        [Parameter(Mandatory = $true)][int]$ExpectedFps
    )

    Assert-PublicCueValue -Value $Cue

    if ([string]$Cue.schemaVersion -ne "1.1.0") {
        throw "The exported cue sheet uses an unsupported schema version."
    }

    if (
        $null -eq $Cue.source -or
        -not (Test-GuidString -Value $Cue.source.jobId) -or
        ([Guid]$Cue.source.jobId).ToString() -ne ([Guid]$ExpectedJobId).ToString()
    ) {
        throw "The exported cue sheet does not identify the current TrackPrompt job."
    }

    if (
        $null -eq $Cue.timeline -or
        -not (Test-FiniteNumber -Value $Cue.timeline.durationSeconds) -or
        [double]$Cue.timeline.durationSeconds -le 0 -or
        [int]$Cue.timeline.fps -ne $ExpectedFps -or
        [int]$Cue.timeline.frameStart -lt 1 -or
        [int]$Cue.timeline.frameEnd -lt [int]$Cue.timeline.frameStart
    ) {
        throw "The exported cue sheet has an invalid timeline."
    }

    $frameStart = [int]$Cue.timeline.frameStart
    $frameEnd = [int]$Cue.timeline.frameEnd

    foreach ($eventCollectionName in @("beats", "onsets")) {
        $eventsProperty = $Cue.PSObject.Properties[$eventCollectionName]

        if ($null -eq $eventsProperty) {
            throw "The exported cue sheet is missing $eventCollectionName."
        }

        $lastTime = -1.0

        foreach ($event in @($eventsProperty.Value)) {
            if (
                $null -eq $event -or
                -not (Test-FiniteNumber -Value $event.timeSeconds) -or
                -not (Test-FiniteNumber -Value $event.frame) -or
                [double]$event.timeSeconds -lt $lastTime -or
                [int]$event.frame -lt $frameStart -or
                [int]$event.frame -gt $frameEnd
            ) {
                throw "The exported cue sheet contains an invalid $eventCollectionName event."
            }

            $lastTime = [double]$event.timeSeconds
        }
    }

    $sections = @($Cue.sections)

    if ($sections.Count -lt 1) {
        throw "The exported cue sheet contains no sections."
    }

    $previousEnd = -1.0

    for ($sectionIndex = 0; $sectionIndex -lt $sections.Count; $sectionIndex++) {
        $section = $sections[$sectionIndex]

        if (
            $null -eq $section -or
            -not (Test-FiniteNumber -Value $section.startSeconds) -or
            -not (Test-FiniteNumber -Value $section.endSeconds) -or
            -not (Test-FiniteNumber -Value $section.startFrame) -or
            -not (Test-FiniteNumber -Value $section.endFrame) -or
            [double]$section.endSeconds -le [double]$section.startSeconds -or
            [double]$section.startSeconds -lt ($previousEnd - 0.000001) -or
            [int]$section.startFrame -lt $frameStart -or
            [int]$section.endFrame -lt [int]$section.startFrame -or
            [int]$section.endFrame -gt $frameEnd
        ) {
            throw "The exported cue sheet contains an invalid section range."
        }

        if ($sectionIndex -eq 0 -and [int]$section.startFrame -ne $frameStart) {
            throw "The exported cue sections do not begin at the timeline start."
        }

        $previousEnd = [double]$section.endSeconds
    }

    if ([int]$sections[-1].endFrame -ne $frameEnd) {
        throw "The exported cue sections do not end at the timeline end."
    }

    $transitionsProperty = $Cue.PSObject.Properties["transitions"]

    if ($null -eq $transitionsProperty) {
        throw "The exported cue sheet is missing transitions."
    }

    foreach ($transition in @($transitionsProperty.Value)) {
        if (
            $null -eq $transition -or
            -not (Test-FiniteNumber -Value $transition.timeSeconds) -or
            -not (Test-FiniteNumber -Value $transition.frame) -or
            [int]$transition.frame -lt $frameStart -or
            [int]$transition.frame -gt $frameEnd
        ) {
            throw "The exported cue sheet contains an invalid transition."
        }
    }

    if ($null -eq $Cue.curves) {
        throw "The exported cue sheet has no continuous curves."
    }

    $curveNames = @($Cue.curves.PSObject.Properties.Name)

    if ($curveNames -notcontains "masterEnergy") {
        throw "The exported cue sheet is missing the required masterEnergy curve."
    }

    foreach ($curveProperty in $Cue.curves.PSObject.Properties) {
        $points = @($curveProperty.Value.points)

        if ($points.Count -lt 2 -or $points.Count -gt 5000) {
            throw "Cue curve $($curveProperty.Name) has an invalid point count."
        }

        $previousFrame = $frameStart - 1

        foreach ($point in $points) {
            if (
                $null -eq $point -or
                @($point).Count -ne 2 -or
                -not (Test-FiniteNumber -Value $point[0]) -or
                -not (Test-FiniteNumber -Value $point[1])
            ) {
                throw "Cue curve $($curveProperty.Name) has an invalid point."
            }

            $frame = [int]$point[0]
            $value = [double]$point[1]

            if (
                $frame -le $previousFrame -or
                $frame -lt $frameStart -or
                $frame -gt $frameEnd -or
                $value -lt 0.0 -or
                $value -gt 1.0
            ) {
                throw "Cue curve $($curveProperty.Name) has an out-of-range or unordered point."
            }

            $previousFrame = $frame
        }
    }

    return [ordered]@{
        schemaVersion = $Cue.schemaVersion
        fps = $Cue.timeline.fps
        frameStart = $frameStart
        frameEnd = $frameEnd
        beats = @($Cue.beats).Count
        onsets = @($Cue.onsets).Count
        sections = $sections.Count
        transitions = @($Cue.transitions).Count
        curves = $curveNames
    }
}

function Assert-NonEmptyFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (
        -not (Test-Path -LiteralPath $Path -PathType Leaf) -or
        (Get-Item -LiteralPath $Path).Length -le 0
    ) {
        throw "$Description was not created as a non-empty file: $Path"
    }
}

function Assert-ManifestSuccess {
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $okProperty = $Manifest.PSObject.Properties["ok"]

    if ($null -eq $okProperty -or $okProperty.Value -ne $true) {
        throw "$Description does not declare top-level ok=true."
    }
}

function Assert-BlenderBuildArtifacts {
    param(
        [Parameter(Mandatory = $true)][string]$BlendPath,
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)][string]$ExpectedCueSchema,
        [Parameter(Mandatory = $true)][int]$ExpectedSeed
    )

    Assert-NonEmptyFile -Path $BlendPath -Description "Blender scene"
    Assert-NonEmptyFile -Path $ManifestPath -Description "Blender scene manifest"

    try {
        $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    }
    catch {
        throw "The Blender scene manifest is invalid JSON."
    }

    Assert-ManifestSuccess `
        -Manifest $manifest `
        -Description "The Blender scene manifest"

    if (
        [string]$manifest.schemaVersion -ne "1.0.0" -or
        [string]$manifest.preset -ne "abstract-geometry" -or
        [int]$manifest.seed -ne $ExpectedSeed -or
        [string]$manifest.cueSheetSchemaVersion -ne $ExpectedCueSchema -or
        $null -eq $manifest.scene -or
        $manifest.scene.ok -ne $true -or
        $manifest.scene.audioStripPresent -ne $true
    ) {
        throw "The Blender scene manifest does not confirm a complete TrackPrompt build."
    }

    foreach ($checkName in @(
        "frameRange",
        "fps",
        "activeCamera",
        "collections",
        "audioBus",
        "audioBusControls",
        "audioBusFCurves",
        "sceneFCurves",
        "audioStrip",
        "outputFile"
    )) {
        $checkProperty = if ($null -ne $manifest.checks) {
            $manifest.checks.PSObject.Properties[$checkName]
        }
        else {
            $null
        }

        if ($null -eq $checkProperty -or $checkProperty.Value -ne $true) {
            throw "The Blender scene manifest failed or omitted contract check $checkName."
        }
    }

    $resolvedBlend = (Resolve-Path -LiteralPath $BlendPath).Path
    $manifestOutput = [string]$manifest.scene.outputFile

    if (
        [string]::IsNullOrWhiteSpace($manifestOutput) -or
        -not [IO.Path]::GetFullPath($manifestOutput).Equals(
            $resolvedBlend,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "The Blender scene manifest output '$manifestOutput' does not match '$resolvedBlend'."
    }

    $requiredCollections = @(
        "TP_WORLD",
        "TP_CAMERAS",
        "TP_LIGHTS",
        "TP_PRIMARY_GEOMETRY",
        "TP_RINGS",
        "TP_SHARDS",
        "TP_VOCAL_ELEMENTS",
        "TP_BACKGROUND",
        "TP_DEBUG"
    )
    $requiredControls = @(
        "master_energy",
        "drum_energy",
        "bass_energy",
        "vocal_energy",
        "other_energy",
        "low_band",
        "mid_band",
        "high_band",
        "brightness",
        "transient_activity"
    )

    foreach ($collectionName in $requiredCollections) {
        if (@($manifest.scene.collections) -notcontains $collectionName) {
            throw "The Blender scene manifest is missing required collection $collectionName."
        }
    }

    foreach ($controlName in $requiredControls) {
        if (@($manifest.scene.controlProperties) -notcontains $controlName) {
            throw "The Blender scene manifest is missing required control $controlName."
        }
    }

    if (
        [string]::IsNullOrWhiteSpace([string]$manifest.scene.activeCamera) -or
        [int]$manifest.scene.frameStart -lt 1 -or
        [int]$manifest.scene.frameEnd -lt [int]$manifest.scene.frameStart -or
        [int]$manifest.scene.objectCount -lt 1 -or
        [int]$manifest.scene.materialCount -lt 1 -or
        [int]$manifest.scene.fCurveCount -lt 1
    ) {
        throw "The Blender scene manifest is missing required scene diagnostics."
    }

    return $manifest
}

function Assert-PreviewArtifacts {
    param(
        [Parameter(Mandatory = $true)][string]$PreviewDirectory,
        [Parameter(Mandatory = $true)][string]$ManifestPath
    )

    Assert-NonEmptyFile -Path $ManifestPath -Description "Blender preview manifest"

    try {
        $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    }
    catch {
        throw "The Blender preview manifest is invalid JSON."
    }

    if (
        $manifest.ok -ne $true -or
        $null -eq $manifest.stills -or
        $manifest.stills.ok -ne $true -or
        $null -eq $manifest.clip -or
        $manifest.clip.ok -ne $true
    ) {
        throw "The Blender preview manifest reports an incomplete preview."
    }

    foreach ($checkName in @(
        "sceneFrameRange",
        "collections",
        "audioBus",
        "audioBusFCurves",
        "sceneFCurves",
        "audioStrip",
        "stills",
        "movie",
        "movieDuration",
        "audioMux"
    )) {
        $checkProperty = if ($null -ne $manifest.checks) {
            $manifest.checks.PSObject.Properties[$checkName]
        }
        else {
            $null
        }

        if ($null -eq $checkProperty -or $checkProperty.Value -ne $true) {
            throw "The Blender preview manifest failed or omitted contract check $checkName."
        }
    }

    if ($null -eq $manifest.clip.verification -or $manifest.clip.verification.ok -ne $true) {
        throw "The Blender preview clip was not verified with ffprobe."
    }

    $stills = @($manifest.stills.stillFrames)

    if ($stills.Count -lt 1) {
        throw "The Blender preview manifest contains no still frames."
    }

    $previewRoot = (Resolve-Path -LiteralPath $PreviewDirectory).Path.TrimEnd('\', '/')

    foreach ($artifactPath in @($stills + @([string]$manifest.clip.clip))) {
        Assert-NonEmptyFile -Path $artifactPath -Description "Blender preview artifact"
        $resolvedArtifact = (Resolve-Path -LiteralPath $artifactPath).Path

        if (
            -not $resolvedArtifact.StartsWith(
                $previewRoot + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "The Blender preview manifest references an artifact outside the run preview directory."
        }
    }

    return $manifest
}

function Assert-CanonicalRunOutputs {
    param(
        [Parameter(Mandatory = $true)]$Outputs,
        [Parameter(Mandatory = $true)][bool]$PreviewRequired
    )

    $requiredFiles = @(
        "runManifest",
        "healthBefore",
        "health",
        "healthAfter",
        "capabilities",
        "openApiBefore",
        "openApiAfter",
        "uploadContract",
        "uploadPlan",
        "uploadResponse",
        "jobId",
        "jobFinal",
        "analysisJson",
        "analysisMarkdown",
        "cueSheet",
        "cueSummary",
        "blend",
        "sceneManifest"
    )

    if ($PreviewRequired) {
        $requiredFiles += @("previewManifest", "previewClip")
    }

    foreach ($outputName in $requiredFiles) {
        $property = $Outputs.PSObject.Properties[$outputName]

        if ($null -eq $property -and $Outputs -is [System.Collections.IDictionary]) {
            if ($Outputs.Contains($outputName)) {
                $value = $Outputs[$outputName]
            }
            else {
                $value = $null
            }
        }
        elseif ($null -ne $property) {
            $value = $property.Value
        }
        else {
            $value = $null
        }

        if ([string]::IsNullOrWhiteSpace([string]$value)) {
            throw "The run manifest is missing the required output: $outputName"
        }

        Assert-NonEmptyFile `
            -Path ([string]$value) `
            -Description "Canonical run output '$outputName'"
    }

    if ($PreviewRequired) {
        $previewStills = @($Outputs["previewStills"])

        if ($previewStills.Count -lt 1) {
            throw "The run manifest contains no preview still outputs."
        }

        foreach ($stillPath in $previewStills) {
            Assert-NonEmptyFile `
                -Path ([string]$stillPath) `
                -Description "Canonical preview still"
        }
    }
}

try {
    if (-not $ConfirmPermission) {
        throw @"
Permission confirmation is required.

Re-run with -ConfirmPermission only when you have permission to analyze the
supplied recording.
"@
    }

    if ($EnableLyrics -and -not $ConfirmLyricsConsent) {
        throw @"
Lyrics analysis is enabled, but its separate consent was not supplied.

Either add:
  -ConfirmLyricsConsent

or disable it:
  -EnableLyrics:`$false
"@
    }

    if ($DeriveAbstractThemes -and -not $EnableLyrics) {
        throw "Abstract lyrical themes require -EnableLyrics:`$true."
    }

    if ($AnalysisTimeoutMinutes -lt 5 -or $AnalysisTimeoutMinutes -gt 180) {
        throw "AnalysisTimeoutMinutes must be between 5 and 180."
    }

    if ($PollSeconds -lt 2 -or $PollSeconds -gt 60) {
        throw "PollSeconds must be between 2 and 60."
    }

    $script:RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

    if ([string]::IsNullOrWhiteSpace($script:RepoRoot)) {
        $script:RepoRoot = (Get-Location).Path
    }

    $script:RepoRoot = (
        Resolve-Path -LiteralPath $script:RepoRoot
    ).Path

    Set-Location -LiteralPath $script:RepoRoot

    $composeBase = Join-Path $script:RepoRoot "compose.yaml"
    $composeFull = Join-Path $script:RepoRoot "compose.full-gpu.yaml"
    $buildScript = Join-Path $script:RepoRoot "blender\build_visualizer.py"
    $renderScript = Join-Path $script:RepoRoot "blender\render_preview.py"

    foreach ($requiredFile in @(
        $composeBase,
        $composeFull,
        $buildScript,
        $renderScript
    )) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Required repository file is missing: $requiredFile"
        }
    }

    if (-not (Test-Path -LiteralPath $AudioPath -PathType Leaf)) {
        throw "The supplied local audio file was not found."
    }

    $resolvedAudio = (Resolve-Path -LiteralPath $AudioPath).Path
    $resolvedBlender = Resolve-BlenderExecutable -ExplicitPath $BlenderExe

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $runSuffix = ([Guid]::NewGuid().ToString("N")).Substring(0, 8)
    $runsRoot = Join-Path $script:RepoRoot "test-output\system-runs"
    $script:RunDirectory = Join-Path $runsRoot "run-$stamp-$runSuffix"

    New-Item -ItemType Directory -Force -Path $script:RunDirectory | Out-Null

    $audioHash = (
        Get-FileHash -LiteralPath $resolvedAudio -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    $script:RunManifest = [ordered]@{
        schemaVersion = "1.0.0"
        startedAt = (Get-Date).ToUniversalTime().ToString("o")
        status = "starting"
        mode = $Mode
        enableGenre = $EnableGenre
        enableLyrics = $EnableLyrics
        deriveAbstractThemes = $DeriveAbstractThemes
        buildStack = [bool]$BuildStack
        autoRebuildStaleBackend = $AutoRebuildStaleBackend
        backendRepairAttempted = $false
        audioSha256 = $audioHash
        audioSizeBytes = (Get-Item -LiteralPath $resolvedAudio).Length
        jobId = $null
        outputs = [ordered]@{
            runDirectory = $script:RunDirectory
            runManifest = (Join-Path $script:RunDirectory "run-manifest.json")
        }
    }

    Save-RunManifest

    $script:ComposePrefix = @(
        "compose",
        "-f", $composeBase,
        "-f", $composeFull
    )

    Write-Step "Validating Docker Compose"

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is unavailable on PATH."
    }

    Invoke-ComposeChecked `
        -Arguments @("config", "--quiet") `
        -Description "TrackPrompt full-GPU Compose configuration validation"

    Write-Step "Validating Blender"

    Invoke-NativeChecked `
        -Executable $resolvedBlender `
        -ArgumentList @("--version") `
        -Description "Blender version validation"

    $script:RunManifest["blenderExecutable"] = [IO.Path]::GetFileName($resolvedBlender)
    Save-RunManifest

    Write-Step "Inspecting and preparing TrackPrompt"

    $healthBeforePath = Join-Path $script:RunDirectory "health-before.json"
    $openApiBeforePath = Join-Path $script:RunDirectory "openapi-before.json"
    $openApiAfterPath = Join-Path $script:RunDirectory "openapi-after.json"
    $health = Test-ApiHealth
    $healthBefore = $script:LastApiHealthObservation

    if ($null -ne $healthBefore) {
        Write-ApiObservation -Path $healthBeforePath -Value $healthBefore
    }
    else {
        Write-ApiObservation `
            -Path $healthBeforePath `
            -Value $null `
            -ErrorMessage "TrackPrompt was not reachable before stack preparation."
    }

    $openApiBefore = $null
    $openApiBeforeError = "TrackPrompt was not reachable before stack preparation."

    if ($null -ne $healthBefore) {
        try {
            $openApiBefore = Get-LiveOpenApi
            $openApiBeforeError = ""
        }
        catch {
            $openApiBeforeError = $_.Exception.Message
        }
    }

    Write-ApiObservation `
        -Path $openApiBeforePath `
        -Value $openApiBefore `
        -ErrorMessage $openApiBeforeError

    $script:RunManifest["outputs"]["healthBefore"] = $healthBeforePath
    $script:RunManifest["outputs"]["openApiBefore"] = $openApiBeforePath
    Save-RunManifest

    if ($BuildStack) {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "Docker is unavailable on PATH."
        }

        # Explicit -BuildStack always rebuilds and force-recreates both current
        # source images, even when the old backend is already healthy.
        Invoke-ComposeChecked `
            -Arguments @(
                "up",
                "-d",
                "--build",
                "--force-recreate",
                "backend",
                "frontend"
            ) `
            -Description "TrackPrompt backend and frontend rebuild"

        $script:StackWasStarted = $true
        $health = Wait-ApiHealth
        Wait-ComposeServiceHealthy -Service "frontend"
    }
    elseif ($null -eq $health) {
        if (-not $StartStack) {
            $observedStatus = if ($null -ne $healthBefore) {
                $statusProperty = $healthBefore.PSObject.Properties["status"]
                if ($null -ne $statusProperty) {
                    [string]$statusProperty.Value
                }
                else {
                    "missing"
                }
            }
            else {
                "unreachable"
            }
            throw "TrackPrompt is not healthy at $ApiBase (status: $observedStatus) and StartStack is disabled."
        }

        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "Docker is unavailable on PATH."
        }

        Invoke-ComposeChecked `
            -Arguments @("up", "-d", "backend", "frontend") `
            -Description "TrackPrompt full-GPU stack startup"

        $script:StackWasStarted = $true
        $health = Wait-ApiHealth
        Wait-ComposeServiceHealthy -Service "frontend"
    }

    $openApi = Get-LiveOpenApi
    $routeStatus = Get-VisualizerRouteStatus -OpenApi $openApi

    if (-not $routeStatus.Complete -and $AutoRebuildStaleBackend -and -not $BuildStack) {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "The backend OpenAPI contract is stale and Docker is unavailable for bounded repair."
        }

        Write-WarningMessage "The live backend is missing one or both visual-cue routes. Rebuilding and recreating only the backend once."
        $script:BackendRepairAttempted = $true
        $script:RunManifest["backendRepairAttempted"] = $true
        Save-RunManifest

        # This is intentionally the least-destructive repair: one service, no
        # volume removal, no cache removal, and no dependency recreation.
        Invoke-ComposeChecked `
            -Arguments @("up", "-d", "prompt-writer") `
            -Description "TrackPrompt prompt-writer availability"
        Wait-ComposeServiceHealthy -Service "prompt-writer"

        Invoke-ComposeChecked `
            -Arguments @(
                "up",
                "-d",
                "--build",
                "--force-recreate",
                "--no-deps",
                "backend"
            ) `
            -Description "One-time stale TrackPrompt backend repair"

        $health = Wait-ApiHealth
        Invoke-ComposeChecked `
            -Arguments @("up", "-d", "--no-deps", "frontend") `
            -Description "TrackPrompt frontend refresh after backend repair"
        Wait-ComposeServiceHealthy -Service "frontend"
        $openApi = Get-LiveOpenApi
        $routeStatus = Get-VisualizerRouteStatus -OpenApi $openApi
    }

    Write-ApiObservation -Path $openApiAfterPath -Value $openApi
    $script:RunManifest["visualCueRoutes"] = [ordered]@{
        post = $routeStatus.PostVisualCues
        exportGet = $routeStatus.GetVisualCuesExport
    }
    $script:RunManifest["outputs"]["openApiAfter"] = $openApiAfterPath
    Save-RunManifest

    if (-not $routeStatus.Complete) {
        $repairHint = if ($AutoRebuildStaleBackend) {
            "The bounded backend repair was unavailable or did not publish the current contract."
        }
        else {
            "AutoRebuildStaleBackend is disabled. Re-run with -BuildStack after reviewing the current stack."
        }

        throw @"
The live backend must expose both visual-cue operations:
  POST /api/analyses/{job_id}/visual-cues
  GET  /api/analyses/{job_id}/visual-cues/export

$repairHint
"@
    }

    $health = Wait-ApiHealth -Attempts 5
    Wait-ComposeServiceHealthy -Service "frontend" -Attempts 30
    $healthPath = Join-Path $script:RunDirectory "health.json"
    $healthAfterPath = Join-Path $script:RunDirectory "health-after.json"
    Write-JsonFile -Path $healthPath -Value $health
    Write-JsonFile -Path $healthAfterPath -Value $health

    $capabilities = Invoke-RestMethod `
        -Method Get `
        -Uri "$ApiBase/api/capabilities" `
        -TimeoutSec 20

    $capabilitiesPath = Join-Path $script:RunDirectory "capabilities.json"
    Write-JsonFile -Path $capabilitiesPath -Value $capabilities
    $script:RunManifest["outputs"]["health"] = $healthPath
    $script:RunManifest["outputs"]["healthAfter"] = $healthAfterPath
    $script:RunManifest["outputs"]["capabilities"] = $capabilitiesPath
    Save-RunManifest

    Write-Success "TrackPrompt API and both Blender cue routes are current"

    if ($Mode -eq "deep") {
        $deepAvailable = Get-DirectPropertyValue `
            -Node $health `
            -Names @("deepModeAvailable")

        if ($null -ne $deepAvailable -and -not [bool]$deepAvailable) {
            throw "Deep mode was requested, but /api/health reports it unavailable."
        }
    }

    if ($EnableGenre) {
        $genreAvailable = Get-DirectPropertyValue `
            -Node $health `
            -Names @("genreTaggerAvailable")

        if ($null -ne $genreAvailable -and -not [bool]$genreAvailable) {
            throw "Genre tagging was requested, but /api/health reports it unavailable."
        }
    }

    if ($EnableLyrics) {
        $lyricsAvailable = Get-DirectPropertyValue `
            -Node $health `
            -Names @("lyricsAdapterAvailable")

        if ($null -ne $lyricsAvailable -and -not [bool]$lyricsAvailable) {
            throw "Lyrics analysis was requested, but /api/health reports it unavailable."
        }
    }

    Write-Step "Discovering the live TrackPrompt upload contract"

    Write-JsonFile `
        -Path (Join-Path $script:RunDirectory "openapi-upload-contract.json") `
        -Value $openApi

    $uploadContract = Get-UploadContract -OpenApi $openApi
    $uploadPlan = New-UploadFieldPlan `
        -OpenApi $openApi `
        -Contract $uploadContract `
        -Overrides $AdditionalUploadFields

    $contractSummary = [ordered]@{
        fileField = $uploadPlan.FileField
        formFields = $uploadPlan.FormFields
        requiredFields = $uploadContract.Required
        allProperties = @($uploadContract.Properties.Keys)
    }

    Write-JsonFile `
        -Path (Join-Path $script:RunDirectory "resolved-upload-plan.json") `
        -Value $contractSummary

    $script:RunManifest["outputs"]["uploadContract"] = Join-Path `
        $script:RunDirectory `
        "openapi-upload-contract.json"
    $script:RunManifest["outputs"]["uploadPlan"] = Join-Path `
        $script:RunDirectory `
        "resolved-upload-plan.json"
    Save-RunManifest

    Write-Host "File field: $($uploadPlan.FileField)"
    Write-Host "Form fields:"

    foreach ($entry in $uploadPlan.FormFields.GetEnumerator()) {
        Write-Host "  $($entry.Key) = $($entry.Value)"
    }

    Write-Step "Uploading the track to TrackPrompt"

    $uploadResponsePath = Join-Path `
        $script:RunDirectory `
        "upload-response.json"

    $uploadResponse = Invoke-CurlUpload `
        -Url "$ApiBase/api/analyses" `
        -FileField $uploadPlan.FileField `
        -FilePath $resolvedAudio `
        -FormFields $uploadPlan.FormFields `
        -ResponsePath $uploadResponsePath

    $script:JobId = Find-JobId -Response $uploadResponse

    $jobIdPath = Join-Path $script:RunDirectory "job-id.txt"
    Write-Utf8File `
        -Path $jobIdPath `
        -Content ($script:JobId + [Environment]::NewLine)

    $testOutputRoot = Join-Path $script:RepoRoot "test-output"
    New-Item -ItemType Directory -Force -Path $testOutputRoot | Out-Null

    $lastJobIdPath = Join-Path $testOutputRoot "last-trackprompt-job-id.txt"
    Write-Utf8File `
        -Path $lastJobIdPath `
        -Content ($script:JobId + [Environment]::NewLine)

    $script:RunManifest["jobId"] = $script:JobId
    $script:RunManifest["status"] = "analysis-running"
    $script:RunManifest["outputs"]["uploadResponse"] = $uploadResponsePath
    $script:RunManifest["outputs"]["jobId"] = $jobIdPath
    $script:RunManifest["outputs"]["lastJobId"] = $lastJobIdPath
    Save-RunManifest

    Write-Success "Saved TrackPrompt job ID: $($script:JobId)"
    Write-Host "Job ID file: $(Join-Path $script:RunDirectory 'job-id.txt')"

    Write-Step "Waiting for TrackPrompt analysis completion"

    $deadline = (
        Get-Date
    ).ToUniversalTime().AddMinutes($AnalysisTimeoutMinutes)

    $finalJob = Wait-AnalysisCompletion `
        -Id $script:JobId `
        -Deadline $deadline

    $jobFinalPath = Join-Path $script:RunDirectory "job-final.json"
    Write-JsonFile `
        -Path $jobFinalPath `
        -Value $finalJob

    $script:RunManifest["status"] = "analysis-completed"
    $script:RunManifest["analysisTerminalState"] = Get-JobState -JobResponse $finalJob
    $script:RunManifest["outputs"]["jobFinal"] = $jobFinalPath
    Save-RunManifest

    Write-Success "TrackPrompt analysis completed"

    Write-Step "Exporting analysis artifacts"

    $analysisJsonPath = Join-Path $script:RunDirectory "analysis.json"
    $analysisMarkdownPath = Join-Path $script:RunDirectory "analysis.md"

    [void](Download-ApiArtifact `
        -Uri "$ApiBase/api/analyses/$($script:JobId)/export.json" `
        -Destination $analysisJsonPath `
        -Description "Analysis JSON")

    [void](Download-ApiArtifact `
        -Uri "$ApiBase/api/analyses/$($script:JobId)/export.md" `
        -Destination $analysisMarkdownPath `
        -Description "Analysis Markdown")

    Assert-NonEmptyFile -Path $analysisJsonPath -Description "Analysis JSON"
    Assert-NonEmptyFile -Path $analysisMarkdownPath -Description "Analysis Markdown"
    $script:RunManifest["outputs"]["analysisJson"] = $analysisJsonPath
    $script:RunManifest["outputs"]["analysisMarkdown"] = $analysisMarkdownPath
    Save-RunManifest

    Write-Step "Exporting the Blender visual cue sheet"

    $cuePath = Join-Path $script:RunDirectory "visual-cues.json"

    $query = @(
        "fps=$Fps",
        "includeBeats=true",
        "includeOnsets=true",
        "includeStemEvidence=true",
        "includeCurves=true",
        "curveDetail=$CurveDetail"
    ) -join "&"

    [void](Download-ApiArtifact `
        -Uri "$ApiBase/api/analyses/$($script:JobId)/visual-cues/export?$query" `
        -Destination $cuePath `
        -Description "Blender visual cue sheet")

    try {
        $cue = Get-Content -LiteralPath $cuePath -Raw | ConvertFrom-Json
    }
    catch {
        throw "The exported Blender cue sheet is invalid JSON."
    }

    $cueSummary = Assert-VisualCueSheet `
        -Cue $cue `
        -ExpectedJobId $script:JobId `
        -ExpectedFps $Fps

    $cueSummaryPath = Join-Path $script:RunDirectory "cue-summary.json"
    Write-JsonFile `
        -Path $cueSummaryPath `
        -Value $cueSummary

    $script:RunManifest["outputs"]["cueSheet"] = $cuePath
    $script:RunManifest["outputs"]["cueSummary"] = $cueSummaryPath
    Save-RunManifest

    Write-Host ($cueSummary | ConvertTo-Json -Depth 20)
    Write-Success "Blender cue sheet validated"

    Write-Step "Building the Blender abstract-geometry scene"

    $blendPath = Join-Path `
        $script:RunDirectory `
        "trackprompt-abstract.blend"

    Write-Host "> blender.exe (scene build; local audio input redacted)" -ForegroundColor DarkGray
    $buildNativeResult = Invoke-NativeCaptured `
        -Executable $resolvedBlender `
        -ArgumentList @(
            "--background",
            "--python-exit-code", "1",
            "--python", $buildScript,
            "--",
            "--cues", $cuePath,
            "--audio", $resolvedAudio,
            "--preset", "abstract-geometry",
            "--seed", [string]$Seed,
            "--output", $blendPath
        ) `
        -Description "Blender visualizer scene build" `
        -Quiet

    $buildResult = Assert-NativeJsonSuccess `
        -NativeResult $buildNativeResult `
        -Description "Blender visualizer scene build"
    $buildResultPath = Join-Path $script:RunDirectory "blender-build-result.json"
    Write-JsonFile -Path $buildResultPath -Value $buildResult

    $sceneManifestPath = [IO.Path]::ChangeExtension(
        $blendPath,
        ".manifest.json"
    )
    $sceneManifest = Assert-BlenderBuildArtifacts `
        -BlendPath $blendPath `
        -ManifestPath $sceneManifestPath `
        -ExpectedCueSchema ([string]$cue.schemaVersion) `
        -ExpectedSeed $Seed

    $script:RunManifest["outputs"]["blend"] = $blendPath
    $script:RunManifest["outputs"]["sceneManifest"] = $sceneManifestPath
    $script:RunManifest["outputs"]["blenderBuildResult"] = $buildResultPath
    $script:RunManifest["status"] = "blender-built"
    Save-RunManifest

    Write-Success "Created Blender scene: $blendPath"

    if (-not $SkipPreview) {
        Write-Step "Rendering the bounded Blender test preview"

        $previewDirectory = Join-Path $script:RunDirectory "preview"
        New-Item -ItemType Directory -Force -Path $previewDirectory | Out-Null

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
            $renderArguments += @("--ffmpeg", $ffmpegExe)
            $script:RunManifest["ffmpegExecutable"] = [IO.Path]::GetFileName($ffmpegExe)
        }
        else {
            Write-WarningMessage "Host FFmpeg was not found. Blender will use its native preview path; movie encoding may be unavailable."
        }

        $previewNativeResult = Invoke-NativeCaptured `
            -Executable $resolvedBlender `
            -ArgumentList $renderArguments `
            -Description "Blender preview render" `
            -Quiet

        $previewCommandResult = Assert-NativeJsonSuccess `
            -NativeResult $previewNativeResult `
            -Description "Blender preview render"
        $previewResultPath = Join-Path `
            $script:RunDirectory `
            "blender-preview-result.json"
        Write-JsonFile -Path $previewResultPath -Value $previewCommandResult

        $previewManifest = Join-Path $previewDirectory "preview-manifest.json"

        $previewResult = Assert-PreviewArtifacts `
            -PreviewDirectory $previewDirectory `
            -ManifestPath $previewManifest

        $script:RunManifest["outputs"]["previewDirectory"] = $previewDirectory
        $script:RunManifest["outputs"]["previewManifest"] = $previewManifest
        $script:RunManifest["outputs"]["blenderPreviewResult"] = $previewResultPath
        $script:RunManifest["outputs"]["previewStills"] = @(
            $previewResult.stills.stillFrames
        )
        $script:RunManifest["outputs"]["previewClip"] = [string]$previewResult.clip.clip
        $script:RunManifest["status"] = "preview-completed"
        Save-RunManifest

        Write-Success "Preview completed: $previewDirectory"
    }
    else {
        $script:RunManifest["status"] = "completed-without-preview"
        Save-RunManifest
    }

    Assert-CanonicalRunOutputs `
        -Outputs $script:RunManifest["outputs"] `
        -PreviewRequired (-not $SkipPreview)

    if ($DeleteJobAfterSuccess) {
        Write-Step "Deleting the TrackPrompt job after successful export"

        Invoke-RestMethod `
            -Method Delete `
            -Uri "$ApiBase/api/analyses/$($script:JobId)" `
            -TimeoutSec 30 |
            Out-Null

        $script:RunManifest["trackPromptJobDeleted"] = $true
        Save-RunManifest

        Write-Success "TrackPrompt job deleted"
    }
    else {
        $script:RunManifest["trackPromptJobDeleted"] = $false
        Save-RunManifest
    }

    $script:RunManifest["completedAt"] = (
        Get-Date
    ).ToUniversalTime().ToString("o")

    if ($SkipPreview) {
        $script:RunManifest["status"] = "completed-without-preview"
    }
    else {
        $script:RunManifest["status"] = "completed"
    }

    Save-RunManifest

    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Green
    Write-Host "TRACKPROMPT → BLENDER SYSTEM RUN COMPLETED" -ForegroundColor Green
    Write-Host "==================================================" -ForegroundColor Green
    Write-Host "Job ID:      $($script:JobId)"
    Write-Host "Run folder:  $($script:RunDirectory)"
    Write-Host "Cue sheet:   $cuePath"
    Write-Host "Blend file:  $blendPath"

    if (-not $SkipPreview) {
        Write-Host "Preview:     $(Join-Path $script:RunDirectory 'preview')"
    }

    Write-Host ""

    if ($DeleteJobAfterSuccess) {
        Write-Host "The TrackPrompt job was deleted after successful export." -ForegroundColor Cyan
    }
    else {
        Write-Host "The TrackPrompt job was preserved for inspection." -ForegroundColor Cyan
    }
}
catch {
    $workflowFailure = $_

    if ($script:RunManifest.Count -gt 0) {
        $script:RunManifest["status"] = "failed"
        $script:RunManifest["failedAt"] = (
            Get-Date
        ).ToUniversalTime().ToString("o")
        $script:RunManifest["errorType"] = $workflowFailure.Exception.GetType().Name
        $script:RunManifest["errorMessage"] = $workflowFailure.Exception.Message
        Save-RunManifest
    }

    try {
        Save-FailureDiagnostics
    }
    catch {
        Write-WarningMessage "Automatic failure diagnostics also encountered an error."
    }

    Write-Host ""
    Write-Host "Whole-system run failed:" -ForegroundColor Red
    Write-Host $workflowFailure.Exception.Message -ForegroundColor Red

    if (-not [string]::IsNullOrWhiteSpace($script:RunDirectory)) {
        Write-Host ""
        Write-Host "Partial run artifacts:" -ForegroundColor Yellow
        Write-Host $script:RunDirectory
    }

    exit 1
}
