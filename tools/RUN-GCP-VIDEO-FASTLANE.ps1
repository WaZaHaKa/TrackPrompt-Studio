[CmdletBinding()]
param(
    [string]$RepositoryRoot,
    [ValidateRange(1024, 65535)][int]$PreferredPort = 8765,
    [switch]$NoBrowser,
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Split-Path -Parent $PSScriptRoot
}
$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$launcher = Join-Path $RepositoryRoot 'WZHK-Media-Launcher.cmd'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Mission Control launcher is missing: $launcher"
}

Write-Host 'Validating the existing Mission Control host before opening video generation...'
& $launcher -ValidateOnly -PreferredPort $PreferredPort
if ($LASTEXITCODE -ne 0) { throw 'Mission Control validation failed.' }
if ($ValidateOnly) {
    Write-Host 'Video fast-lane host validation passed. No service or cloud request was started.' -ForegroundColor Green
    return
}

$arguments = @('-NoBrowser', '-PreferredPort', "$PreferredPort")
& $launcher @arguments
if ($LASTEXITCODE -ne 0) { throw 'Mission Control could not start.' }

$descriptor = Join-Path $RepositoryRoot '.trackprompt-data\mission-control\instance.json'
if (-not (Test-Path -LiteralPath $descriptor -PathType Leaf)) {
    throw 'Mission Control started without publishing its loopback instance descriptor.'
}
$instance = Get-Content -LiteralPath $descriptor -Raw | ConvertFrom-Json
$url = "http://127.0.0.1:$($instance.port)/?section=video"
Write-Host ''
Write-Host 'Mission Control video generation is ready.' -ForegroundColor Green
Write-Host "Open: $url"
Write-Host 'Planning and GCP readiness checks are non-billable. A paid smoke request remains locked behind the exact displayed one-time confirmation phrase.'
if (-not $NoBrowser) {
    Start-Process -FilePath $url | Out-Null
}
