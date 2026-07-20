Set-StrictMode -Version Latest

function ConvertTo-WzhkQuotedArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ($Value -notmatch '[\s"]') {
        return $Value
    }

    return '"' + $Value.Replace('"', '\"') + '"'
}

function Start-WzhkRenderWatcher {
    param(
        [Parameter(Mandatory = $true)][string]$WatcherScript,
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [int]$TotalFrames = 13029,
        [int]$RefreshSeconds = 6,
        [double]$Fps = 30.0,
        [int]$FrameStart = 1,
        [string]$ProfilePath = "",
        [switch]$NoBrowser,
        [switch]$OpenOutputWhenComplete
    )

    if (-not (Test-Path -LiteralPath $WatcherScript -PathType Leaf)) {
        return $null
    }

    $powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $arguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $WatcherScript,
        "-OutputDirectory", $OutputDirectory,
        "-TotalFrames", [string]$TotalFrames,
        "-RefreshSeconds", [string]$RefreshSeconds,
        "-Fps", [string]$Fps,
        "-FrameStart", [string]$FrameStart
    )

    if (-not [string]::IsNullOrWhiteSpace($ProfilePath)) {
        $arguments += @("-ProfilePath", $ProfilePath)
    }
    if ($NoBrowser) {
        $arguments += "-NoBrowser"
    }
    if ($OpenOutputWhenComplete) {
        $arguments += "-OpenOutputWhenComplete"
    }

    $argumentString = ($arguments | ForEach-Object { ConvertTo-WzhkQuotedArgument -Value ([string]$_) }) -join " "
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $powerShell
    $startInfo.Arguments = $argumentString
    $startInfo.UseShellExecute = $true
    $startInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Normal

    return [Diagnostics.Process]::Start($startInfo)
}

function Invoke-WzhkRenderMode {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("Preflight", "DryRun", "Production")]
        [string]$Mode,

        [Parameter(Mandatory = $true)][string]$RenderScript,
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [string]$AuthorizationToken = "",
        [string]$WatcherScript = "",
        [int]$TotalFrames = 13029,
        [double]$Fps = 30.0,
        [int]$FrameStart = 1,
        [int]$WatcherRefreshSeconds = 6,
        [switch]$OpenOutputWhenComplete,
        [switch]$AutoWatcher
    )

    # The production renderer owns output initialization and refuses non-empty,
    # unmanaged directories. Keep the wrapper log in TEMP so this layer never
    # creates output/logs before the exact profile manifest is initialized.
    $logsDirectory = [System.IO.Path]::GetTempPath()

    $launcherLog = Join-Path $logsDirectory ([string]::Format(
        "wzhk-control-center-{0}.log",
        (Get-Date -Format "yyyyMMdd-HHmmss")
    ))
    $powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source

    $arguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $RenderScript,
        "-ApprovedScenePath", $ScenePath,
        "-RenderProfilePath", $ProfilePath,
        "-OutputDirectory", $OutputDirectory
    )

    if ($Mode -eq "Preflight") {
        $arguments += "-Preflight"
    }
    elseif ($Mode -eq "DryRun") {
        $arguments += "-DryRun"
    }
    else {
        if ([string]::IsNullOrWhiteSpace($AuthorizationToken)) {
            throw "Production mode requires an exact authorization token."
        }

        $arguments += @("-AuthorizationToken", $AuthorizationToken)

        if ($AutoWatcher -and -not [string]::IsNullOrWhiteSpace($WatcherScript)) {
            $null = Start-WzhkRenderWatcher `
                -WatcherScript $WatcherScript `
                -OutputDirectory $OutputDirectory `
                -TotalFrames $TotalFrames `
                -RefreshSeconds $WatcherRefreshSeconds `
                -Fps $Fps `
                -FrameStart $FrameStart `
                -ProfilePath $ProfilePath `
                -OpenOutputWhenComplete:$OpenOutputWhenComplete
        }
    }

    $lines = New-Object System.Collections.Generic.List[string]
    $exitCode = 1

    try {
        & $powerShell @arguments 2>&1 |
            ForEach-Object {
                $line = [string]$_
                $lines.Add($line)
                Add-Content -LiteralPath $launcherLog -Value $line -Encoding UTF8
                Write-WzhkFrameLine -Text ("  " + $line) -Color White
            }

        $exitCode = $LASTEXITCODE
    }
    catch {
        $line = $_.Exception.Message
        $lines.Add($line)
        Add-Content -LiteralPath $launcherLog -Value $line -Encoding UTF8
        Write-WzhkFrameLine -Text ("  ERROR: " + $line) -Color Red
        $exitCode = 1
    }

    return [pscustomobject]@{
        Ok = ($exitCode -eq 0)
        ExitCode = $exitCode
        LogPath = $launcherLog
        Lines = $lines.ToArray()
    }
}

function Open-WzhkOutput {
    param(
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [switch]$OpenLatestFrame
    )

    if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
        throw "Output directory does not exist: $OutputDirectory"
    }

    Start-Process explorer.exe -ArgumentList @("`"$OutputDirectory`"")

    if ($OpenLatestFrame) {
        $latest = Get-ChildItem -LiteralPath $OutputDirectory -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.BaseName -match '^frame_\d{6}$' -and
                $_.Extension.ToLowerInvariant() -in @(".png", ".jpg", ".jpeg")
            } |
            Sort-Object Name |
            Select-Object -Last 1

        if ($null -ne $latest) {
            Start-Process $latest.FullName
        }
    }
}

function Get-WzhkStopRequestPath {
    param([Parameter(Mandatory = $true)][string]$OutputDirectory)
    return Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) "control\stop-after-current-chunk.request.json"
}

function Request-WzhkStopAfterCurrentChunk {
    param(
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [Parameter(Mandatory = $true)][string]$ProfilePath,
        [Parameter(Mandatory = $true)][string]$ScenePath
    )
    foreach ($file in @($ProfilePath, $ScenePath)) { if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Stop request identity file does not exist: $file" } }
    $output = [IO.Path]::GetFullPath($OutputDirectory)
    $manifestPath = Join-Path $output "manifests\render-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "Stop requests require an initialized renderer-owned output manifest." }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $profileHash = (Get-FileHash -LiteralPath $ProfilePath -Algorithm SHA256).Hash.ToUpperInvariant()
    $sceneHash = (Get-FileHash -LiteralPath $ScenePath -Algorithm SHA256).Hash.ToUpperInvariant()
    if ([string]$manifest.renderProfile.sha256 -ne $profileHash -or [string]$manifest.scene.sha256 -ne $sceneHash) { throw "Selected output does not match the exact scene and saved profile." }
    $request = [pscustomobject][ordered]@{
        schemaVersion = "1.0.0"
        kind = "trackprompt-stop-after-current-chunk-request"
        status = "requested"
        requestedAt = (Get-Date).ToUniversalTime().ToString("o")
        outputDirectory = $output
        profileSha256 = $profileHash
        sceneSha256 = $sceneHash
        behavior = "validate and publish the current chunk, then exit before starting the next chunk"
    }
    $path = Get-WzhkStopRequestPath -OutputDirectory $output
    $directory = [IO.Path]::GetDirectoryName($path)
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { $null = New-Item -ItemType Directory -Path $directory }
    $temporary = Join-Path $directory (".stop-request." + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [IO.File]::WriteAllText($temporary, (($request | ConvertTo-Json -Depth 20) + "`n"), (New-Object Text.UTF8Encoding($false)))
        if (Test-Path -LiteralPath $path -PathType Leaf) { [IO.File]::Replace($temporary, $path, ($temporary + ".bak"), $true) }
        else { [IO.File]::Move($temporary, $path) }
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
        if (Test-Path -LiteralPath ($temporary + ".bak") -PathType Leaf) { Remove-Item -LiteralPath ($temporary + ".bak") -Force -ErrorAction SilentlyContinue }
    }
    return [pscustomobject]@{ Requested = $true; Path = $path; Request = $request }
}

function Cancel-WzhkStopAfterCurrentChunk {
    param(
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [switch]$OperatorConfirmed
    )
    if (-not $OperatorConfirmed) { throw "Cancelling a stop request requires explicit operator confirmation." }
    $path = Get-WzhkStopRequestPath -OutputDirectory $OutputDirectory
    if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
    return [pscustomobject]@{ Cancelled = $true; Path = $path }
}

Export-ModuleMember -Function `
    Start-WzhkRenderWatcher, `
    Invoke-WzhkRenderMode, `
    Open-WzhkOutput, `
    Get-WzhkStopRequestPath, `
    Request-WzhkStopAfterCurrentChunk, `
    Cancel-WzhkStopAfterCurrentChunk
