[CmdletBinding()]
param(
    [ValidateSet('Prepare', 'Create', 'Status', 'Recover', 'Resume')][string]$Action = 'Prepare',
    [ValidateSet('quality', 'low-memory')][string]$Profile = 'quality',
    [switch]$NewRun,
    [switch]$Silent,
    [string]$RunId
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$runner = Join-Path $repoRoot 'video-projects\hayale-flowers-promo\v2.1\run.py'
$python = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'AGENTS.md')) -or -not (Test-Path -LiteralPath $runner)) {
    throw 'Run this launcher from the existing TrackPrompt-Studio repository.'
}
if (-not (Test-Path -LiteralPath $python)) { throw 'Existing backend Python is missing. No installation was attempted.' }
if ($Action -ne 'Create' -and ($NewRun -or $Silent)) { throw '-NewRun and -Silent require -Action Create.' }
if ($Action -in @('Recover','Resume') -and $RunId -notmatch '^[a-f0-9]{12}$') { throw 'Recover/Resume require the existing twelve-character -RunId.' }
if ($Action -eq 'Create' -and $env:TRACKPROMPT_HAYALE_PREPARE_ONLY -eq '1') {
    throw 'This session is preparation-only; local creation is disabled.'
}
$arguments = @('-B', $runner, $Action.ToLowerInvariant(), '--profile', $Profile)
if ($NewRun) { $arguments += '--new-run' }
if ($Silent) { $arguments += '--silent' }
if ($RunId) { $arguments += @('--run-id', $RunId) }
Push-Location -LiteralPath $repoRoot
try {
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Hayale V2.1 stopped (exit $LASTEXITCODE). Read the message above; all previous attempts were retained." }
} finally { Pop-Location }
