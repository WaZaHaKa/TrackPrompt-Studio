[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageDirectory,
    [Parameter(Mandatory = $true)][string]$BlenderExecutable,
    [string]$ChunkId = "",
    [int]$Start = 0,
    [int]$End = 0,
    [string]$WorkerId = $env:COMPUTERNAME,
    [string]$OutputDirectory = "",
    [string]$PythonExecutable = "",
    [ValidateRange(1, 86400)][double]$RenderTimeoutSeconds = 21600,
    [ValidateRange(1024, 67108864)][int]$MaxLogBytes = 4194304
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$package = [IO.Path]::GetFullPath($PackageDirectory)
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    foreach ($candidate in @(
        (Join-Path $PSScriptRoot "backend\.venv\Scripts\python.exe"),
        (Join-Path $package "python\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $PythonExecutable = $candidate; break }
    }
    if ([string]::IsNullOrWhiteSpace($PythonExecutable)) { $PythonExecutable = (Get-Command python.exe -ErrorAction Stop).Source }
}
$worker = Join-Path $package "render_trackprompt_worker.py"
if (-not (Test-Path -LiteralPath $worker -PathType Leaf)) { throw "Remote worker entrypoint is missing from the package." }
$arguments = @(
    "--package-directory", $package,
    "--blender", [IO.Path]::GetFullPath($BlenderExecutable),
    "--worker-id", $WorkerId,
    "--render-timeout-seconds", [string]$RenderTimeoutSeconds,
    "--max-log-bytes", [string]$MaxLogBytes
)
if (-not [string]::IsNullOrWhiteSpace($ChunkId)) { $arguments += @("--chunk-id", $ChunkId) }
elseif ($Start -gt 0 -and $End -ge $Start) { $arguments += @("--start", [string]$Start, "--end", [string]$End) }
else { throw "Supply ChunkId or a valid Start/End range." }
if (-not [string]::IsNullOrWhiteSpace($OutputDirectory)) { $arguments += @("--output-directory", [IO.Path]::GetFullPath($OutputDirectory)) }
& $PythonExecutable $worker @arguments
exit $LASTEXITCODE
