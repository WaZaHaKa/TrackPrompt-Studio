[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ApprovedScenePath,
    [Parameter(Mandatory = $true)][string]$RenderProfilePath,
    [Parameter(Mandatory = $true)][string]$ProductionDirectory,
    [Parameter(Mandatory = $true)][string]$MediaPath,
    [Parameter(Mandatory = $true)][string]$AudioPath,
    [Parameter(Mandatory = $true)][string]$EncodeManifestPath,
    [Parameter(Mandatory = $true)][ValidateSet("Master", "Delivery")][string]$OutputKind,
    [string]$ReportPath = "",
    [string]$FfprobeExecutable = "",
    [string]$PythonExecutable = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

function Resolve-Ffprobe {
    if (-not [string]::IsNullOrWhiteSpace($FfprobeExecutable)) {
        return Resolve-RequiredFile -Path $FfprobeExecutable -Label "ffprobe executable"
    }
    $command = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "ffprobe was not found. Supply -FfprobeExecutable explicitly."
    }
    if ($command.Source -like "*\WindowsApps\*") {
        throw "ffprobe resolved to a WindowsApps alias. Supply the real executable path explicitly."
    }
    return $command.Source
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
$resolvedMedia = Resolve-RequiredFile -Path $MediaPath -Label "Final media"
$resolvedAudio = Resolve-RequiredFile -Path $AudioPath -Label "Approved audio"
$resolvedEncodeManifest = Resolve-RequiredFile -Path $EncodeManifestPath -Label "Encode manifest"
$resolvedFfprobe = Resolve-Ffprobe
if (-not [IO.Path]::IsPathRooted($ProductionDirectory)) {
    throw "Production directory must be an absolute path."
}
$resolvedProduction = [IO.Path]::GetFullPath($ProductionDirectory)
$kind = $OutputKind.ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    $reportName = "verify-{0}-{1}-{2}.json" -f `
        $kind,
        (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ"),
        [Guid]::NewGuid().ToString("N").Substring(0, 8)
    $resolvedReport = Join-Path (Join-Path $resolvedProduction "qa") $reportName
}
else {
    if (-not [IO.Path]::IsPathRooted($ReportPath)) {
        throw "Report path must be absolute."
    }
    $resolvedReport = [IO.Path]::GetFullPath($ReportPath)
}
if (Test-Path -LiteralPath $resolvedReport) {
    throw "Verification report already exists; refusing to overwrite it."
}

$verification = Invoke-JsonTool `
    -ArgumentList @(
        "verify-final",
        "--profile", $resolvedProfile,
        "--scene", $resolvedScene,
        "--output", $resolvedProduction,
        "--media", $resolvedMedia,
        "--audio", $resolvedAudio,
        "--ffprobe", $resolvedFfprobe,
        "--encode-manifest", $resolvedEncodeManifest,
        "--kind", $kind,
        "--json-output", $resolvedReport
    ) `
    -Description "Final structural verification"
$verification | Add-Member -NotePropertyName report -NotePropertyValue $resolvedReport
$verification | ConvertTo-Json -Depth 40
