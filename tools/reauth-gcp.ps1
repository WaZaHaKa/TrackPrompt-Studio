# TrackPrompt operator entry point. Windows PowerShell 5.1 and PowerShell 7+.
[CmdletBinding()]
param(
    [ValidateSet('Local', 'Cloud', 'Reauth')][string]$Mode = 'Local',
    [switch]$NonInteractive,
    [string]$ConfigPath,
    [string]$Account,
    [string]$ProjectId,
    [string]$Bucket,
    [string]$Region,
    [string]$ModelId,
    [string]$GcloudExecutable,
    [string]$PythonExecutable
)

# Script scope keeps caller StrictMode and preference settings intact when invoked with &.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$exitCode = 2
$savedPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$savedEncoding = [Environment]::GetEnvironmentVariable('PYTHONIOENCODING', 'Process')
$savedGcloud = [Environment]::GetEnvironmentVariable('TRACKPROMPT_GCLOUD_EXECUTABLE', 'Process')
try {
    if ($PSVersionTable.PSVersion -lt [Version]'5.1') {
        throw 'PowerShell 5.1 or newer is required.'
    }
    $repoRoot = Split-Path -Parent $PSScriptRoot
    if (-not $PythonExecutable) {
        $PythonExecutable = [Environment]::GetEnvironmentVariable('TRACKPROMPT_PYTHON_EXECUTABLE', 'Process')
    }
    if (-not $PythonExecutable -and $env:LOCALAPPDATA) {
        $PythonExecutable = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    }
    if (-not $PythonExecutable -or -not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        throw 'A stable Python 3.11-3.13 installation is required; supply -PythonExecutable.'
    }
    $env:PYTHONPATH = Join-Path $repoRoot 'backend'
    $env:PYTHONIOENCODING = 'utf-8'
    $runtimeCheck = & $PythonExecutable -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 2)' 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11-3.13 is required.' }
    if ($GcloudExecutable) { $env:TRACKPROMPT_GCLOUD_EXECUTABLE = $GcloudExecutable }
    $arguments = @('-m', 'app.video_generation.gcp_auth', '--mode', $Mode.ToLowerInvariant())
    if ($NonInteractive) { $arguments += '--noninteractive' }
    $options = @{
        '--config' = $ConfigPath; '--account' = $Account; '--project' = $ProjectId;
        '--bucket' = $Bucket; '--region' = $Region; '--model' = $ModelId
    }
    foreach ($key in $options.Keys) {
        if ($options[$key]) { $arguments += @($key, [string]$options[$key]) }
    }
    & $PythonExecutable @arguments
    $exitCode = $LASTEXITCODE
} catch {
    # Do not expose exception details, paths, OAuth URLs, codes, or CLI output.
    $result = @{ ok = $false; networkContacted = $false; checks = @(); error = @{
        code = 'auth_launcher_failed'; message = 'Authentication launcher could not run.';
        remedy = 'Use PowerShell 5.1 or newer and supply -PythonExecutable pointing to a stable Python 3.11-3.13 installation.'; retryable = $false
    } }
    $result | ConvertTo-Json -Depth 5 -Compress
} finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $savedPythonPath, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', $savedEncoding, 'Process')
    [Environment]::SetEnvironmentVariable('TRACKPROMPT_GCLOUD_EXECUTABLE', $savedGcloud, 'Process')
}
exit $exitCode
