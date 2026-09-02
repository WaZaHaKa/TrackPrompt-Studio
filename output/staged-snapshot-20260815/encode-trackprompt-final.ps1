[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ApprovedScenePath,
    [Parameter(Mandatory = $true)][string]$RenderProfilePath,
    [Parameter(Mandatory = $true)][string]$ProductionDirectory,
    [Parameter(Mandatory = $true)][string]$AudioPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][ValidateSet("Master", "Delivery")][string]$OutputKind,
    [switch]$DryRun,
    [switch]$Preflight,
    [ValidateRange(1, 32)][int]$FrameScanWorkers = 4,
    [string]$FfmpegExecutable = "",
    [string]$FfprobeExecutable = "",
    [string]$PythonExecutable = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($DryRun -and $Preflight) {
    throw "-DryRun and -Preflight are mutually exclusive."
}

$repositoryRoot = $PSScriptRoot
$toolPath = Join-Path $repositoryRoot "tools\final_render_tooling.py"

function Resolve-RequiredFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not [IO.Path]::IsPathRooted($Path)) {
        throw "$Label must be an absolute path."
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label does not exist: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Resolve-Python {
    if (-not [string]::IsNullOrWhiteSpace($PythonExecutable)) {
        return Resolve-RequiredFile -Path $PythonExecutable -Label "Python executable"
    }
    $repositoryPython = Join-Path $repositoryRoot "backend\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $repositoryPython -PathType Leaf) {
        return (Resolve-Path -LiteralPath $repositoryPython).Path
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Python was not found. Supply -PythonExecutable explicitly."
    }
    return $command.Source
}

function Resolve-NativeExecutable {
    param(
        [string]$RequestedPath,
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$SiblingOf = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        return Resolve-RequiredFile -Path $RequestedPath -Label "$Name executable"
    }
    if (-not [string]::IsNullOrWhiteSpace($SiblingOf)) {
        $sibling = Join-Path ([IO.Path]::GetDirectoryName($SiblingOf)) "$Name.exe"
        if (Test-Path -LiteralPath $sibling -PathType Leaf) {
            return (Resolve-Path -LiteralPath $sibling).Path
        }
    }
    $command = Get-Command "$Name.exe" -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "$Name was not found. Supply its executable path explicitly."
    }
    $resolved = $command.Source
    if ($resolved -like "*\WindowsApps\*") {
        throw "$Name resolved to a WindowsApps alias. Supply the real executable path explicitly."
    }
    return $resolved
}

function Invoke-JsonTool {
    param(
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $captured = @(& $script:ResolvedPython $script:ToolPath @ArgumentList 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $lines = @($captured | ForEach-Object { [string]$_ })
    $jsonLine = @($lines | Where-Object { $_.TrimStart().StartsWith("{") } | Select-Object -Last 1)
    $payload = $null
    if ($jsonLine.Count -eq 1) {
        try {
            $payload = $jsonLine[0] | ConvertFrom-Json
        }
        catch {
            $payload = $null
        }
    }
    if ($exitCode -ne 0) {
        $reason = if ($null -ne $payload -and $null -ne $payload.error) {
            "$($payload.error.code): $($payload.error.message)"
        }
        else {
            ($lines -join [Environment]::NewLine)
        }
        throw "$Description failed with exit code $exitCode. $reason"
    }
    if ($null -eq $payload) {
        throw "$Description did not return structured JSON."
    }
    return $payload
}

$script:ResolvedPython = Resolve-Python
$script:ToolPath = Resolve-RequiredFile -Path $toolPath -Label "Final render tooling"
$resolvedScene = Resolve-RequiredFile -Path $ApprovedScenePath -Label "Approved scene"
$resolvedProfile = Resolve-RequiredFile -Path $RenderProfilePath -Label "Render profile"
$profileData = Get-Content -LiteralPath $resolvedProfile -Raw | ConvertFrom-Json -ErrorAction Stop
$framesSubdirectory = "frames"
if ($null -ne $profileData.PSObject.Properties["output"] -and $null -ne $profileData.output.PSObject.Properties["framesSubdirectory"]) {
    $framesSubdirectory = [string]$profileData.output.framesSubdirectory
}
if ($framesSubdirectory -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' -or $framesSubdirectory -in @(".", "..")) {
    throw "output.framesSubdirectory must be one safe directory name."
}
$resolvedAudio = Resolve-RequiredFile -Path $AudioPath -Label "Approved audio"
$resolvedFfmpeg = Resolve-NativeExecutable -RequestedPath $FfmpegExecutable -Name "ffmpeg"
$resolvedFfprobe = Resolve-NativeExecutable -RequestedPath $FfprobeExecutable -Name "ffprobe" -SiblingOf $resolvedFfmpeg

if (-not [IO.Path]::IsPathRooted($ProductionDirectory)) {
    throw "Production directory must be an absolute path."
}
if (-not [IO.Path]::IsPathRooted($OutputPath)) {
    throw "Output path must be an absolute path."
}
$resolvedProduction = [IO.Path]::GetFullPath($ProductionDirectory)
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
$kind = $OutputKind.ToLowerInvariant()

$preflightResult = Invoke-JsonTool `
    -ArgumentList @(
        "encode-preflight",
        "--profile", $resolvedProfile,
        "--scene", $resolvedScene,
        "--output", $resolvedProduction,
        "--audio", $resolvedAudio,
        "--ffprobe", $resolvedFfprobe,
        "--destination", $resolvedOutput,
        "--kind", $kind,
        "--workers", [string]$FrameScanWorkers
    ) `
    -Description "Final encoding preflight"

$outputDirectory = [IO.Path]::GetDirectoryName($resolvedOutput)
$outputFileName = [IO.Path]::GetFileNameWithoutExtension($resolvedOutput)
$outputExtension = [IO.Path]::GetExtension($resolvedOutput)
$temporaryOutput = Join-Path $outputDirectory (
    ".{0}.partial-{1}{2}" -f $outputFileName, [Guid]::NewGuid().ToString("N"), $outputExtension
)
$encodeArgumentsPayload = Invoke-JsonTool `
    -ArgumentList @(
        "encode-arguments",
        "--profile", $resolvedProfile,
        "--frames", (Join-Path $resolvedProduction $framesSubdirectory),
        "--audio", $resolvedAudio,
        "--temporary-output", $temporaryOutput,
        "--kind", $kind
    ) `
    -Description "Encoding command construction"
$encodeArguments = @($encodeArgumentsPayload.arguments | ForEach-Object { [string]$_ })

if ($DryRun -or $Preflight) {
    $result = [ordered]@{
        ok = $true
        mode = if ($Preflight) { "preflight" } else { "dry-run" }
        outputKind = $kind
        preflight = $preflightResult
        executable = $resolvedFfmpeg
        arguments = $encodeArguments
        note = "The complete encode was not started."
    }
    if ($Preflight) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $version = @(& $resolvedFfmpeg -version 2>&1)
            $versionExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($versionExitCode -ne 0) {
            throw "FFmpeg version preflight failed with exit code $versionExitCode."
        }
        $result["ffmpegVersion"] = [string]$version[0]
    }
    $result | ConvertTo-Json -Depth 30
    exit 0
}

$mutex = [Threading.Mutex]::new($false, "Local\TrackPromptFinalRenderGpu")
$ownsMutex = $false
try {
    try {
        $ownsMutex = $mutex.WaitOne(0)
    }
    catch [Threading.AbandonedMutexException] {
        $ownsMutex = $true
        Write-Warning "Recovered an abandoned TrackPrompt final-render mutex after a prior process ended."
    }
    if (-not $ownsMutex) {
        throw "A TrackPrompt final render or encode currently owns the production mutex."
    }

$logsDirectory = Join-Path $resolvedProduction "logs"
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$stdoutLog = Join-Path $logsDirectory ("encode_{0}_{1}.stdout.log" -f $kind, $timestamp)
$stderrLog = Join-Path $logsDirectory ("encode_{0}_{1}.stderr.log" -f $kind, $timestamp)
Write-Host "Encoding the verified frame sequence to a temporary $kind output." -ForegroundColor Cyan
$previousPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    & $resolvedFfmpeg @encodeArguments 1> $stdoutLog 2> $stderrLog
    $ffmpegExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousPreference
}
if ($ffmpegExitCode -ne 0) {
    throw "FFmpeg failed with exit code $ffmpegExitCode. Temporary output and logs were preserved."
}

$finalized = Invoke-JsonTool `
    -ArgumentList @(
        "finalize-encode",
        "--profile", $resolvedProfile,
        "--scene", $resolvedScene,
        "--output", $resolvedProduction,
        "--temporary-media", $temporaryOutput,
        "--destination", $resolvedOutput,
        "--audio", $resolvedAudio,
        "--ffprobe", $resolvedFfprobe,
        "--kind", $kind,
        "--workers", [string]$FrameScanWorkers
    ) `
    -Description "Atomic encoded-media verification and finalization"
$finalized | Add-Member -NotePropertyName stdoutLog -NotePropertyValue $stdoutLog
$finalized | Add-Member -NotePropertyName stderrLog -NotePropertyValue $stderrLog
$finalized | ConvertTo-Json -Depth 30
}
finally {
    if ($ownsMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
