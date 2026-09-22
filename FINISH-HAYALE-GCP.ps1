#requires -Version 7.0
[CmdletBinding()]
param([switch]$Check, [switch]$SetupOnly, [switch]$Smoke, [switch]$NoOpen)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    $repoRoot = [IO.Path]::GetFullPath($PSScriptRoot)
    $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'The installed Python 3.12 runtime is missing. Restore the existing Python installation.'
    }
    $env:TRACKPROMPT_GCP_AUTH_CONFIG = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'TrackPrompt\hayale-gcp-20260922\auth.json'))
    $env:PYTHONPATH = Join-Path $repoRoot 'backend'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    $arguments = @('-B', (Join-Path $repoRoot 'tools\finish-hayale-gcp.py'), '--repository', $repoRoot)
    if ($Check) { $arguments += '--check' }
    if ($SetupOnly) { $arguments += '--setup-only' }
    if ($Smoke) { $arguments += '--smoke' }
    if ($NoOpen) { $arguments += '--no-open' }
    & $python @arguments
    exit $LASTEXITCODE
} catch {
    Write-Host ('Hayale stopped: ' + $_.Exception.Message)
    exit 2
}
