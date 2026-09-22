# Read-only inspection. Creates a NEW private report directory, never edits the run.
[CmdletBinding()]
param(
  [string]$RepoRoot = 'C:\Users\theon\GitHub\TrackPrompt-Studio',
  [string]$RunId = '344ab3d2444c',
  [string]$ComfyUrl = '',
  [string]$OutputRoot = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($RunId -notmatch '^[a-z0-9][a-z0-9._-]{0,127}$') { throw 'Invalid run ID.' }
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$RunRoot = Join-Path $RepoRoot ".trackprompt-data\hayale-v21\runs\$RunId"
if (-not (Test-Path -LiteralPath $RunRoot -PathType Container)) { throw 'Run directory was not found. Nothing was modified.' }
if (-not $OutputRoot) { $OutputRoot = Join-Path $RepoRoot '.trackprompt-data\wnp-incidents' }
$ReportDir = Join-Path $OutputRoot ((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $ReportDir | Out-Null
function Save-Json([string]$Name, $Value) {
  $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $ReportDir $Name) -Encoding utf8NoBOM
}
$files = @(Get-ChildItem -LiteralPath $RunRoot -Recurse -File -ErrorAction Stop | ForEach-Object {
  [pscustomobject]@{ relative_path=[System.IO.Path]::GetRelativePath($RunRoot,$_.FullName); bytes=$_.Length; modified_utc=$_.LastWriteTimeUtc.ToString('o'); reparse=[bool]($_.Attributes -band [IO.FileAttributes]::ReparsePoint) }
})
Save-Json 'file-inventory.json' $files
$repoState = & git -C $RepoRoot status --porcelain=v1 --branch
$head = & git -C $RepoRoot rev-parse HEAD
Save-Json 'git.json' @{ head=@($head); status=@($repoState); note='Uncommitted local changes remain authoritative; no checkout/reset performed.' }
# Do not export entire workflows, environment variables, prompts, or arbitrary receipts.
$markers=@()
Get-ChildItem -LiteralPath $RunRoot -Recurse -File | Where-Object { $_.Name -in @('submission.json','raw-verified.json') } | ForEach-Object {
  $markers += [pscustomobject]@{ relative_path=[System.IO.Path]::GetRelativePath($RunRoot,$_.FullName); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(); bytes=$_.Length }
}
Save-Json 'marker-hashes.json' $markers
# Two process/resource samples. PID presence and a single GPU sample do not prove job activity.
foreach ($sample in 1..2) {
  $processes=@(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and ($_.CommandLine.Contains($RunId) -or $_.CommandLine.Contains('TrackPrompt') -or $_.CommandLine.Contains('ComfyUI'))
  } | Select-Object ProcessId,ParentProcessId,Name,CreationDate,ExecutablePath,CommandLine)
  Save-Json "processes-private-$sample.json" $processes
  $processIds = @($processes | ForEach-Object { $_.ProcessId })
  $cpuSamples = @()
  foreach ($processId in $processIds) {
    try { $cpuSamples += Get-Process -Id $processId -ErrorAction Stop | Select-Object Id,CPU,WorkingSet64,StartTime }
    catch { $cpuSamples += [pscustomobject]@{ Id=$processId; available=$false } }
  }
  Save-Json "cpu-$sample.json" $cpuSamples
  if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    & nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used,memory.total,power.draw --format=csv,noheader | Set-Content -LiteralPath (Join-Path $ReportDir "gpu-$sample.csv") -Encoding utf8NoBOM
  }
  if ($sample -eq 1) { Start-Sleep -Seconds 5 }
}
try { Save-Json 'listeners.json' @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.OwningProcess -in $processIds } | Select-Object LocalAddress,LocalPort,OwningProcess) }
catch { Save-Json 'listeners.json' @{ available=$false; reason='Listener inventory unavailable with current permissions.' } }
if ($ComfyUrl) {
  $uri=[uri]$ComfyUrl
  if ($uri.Scheme -ne 'http' -or $uri.Host -notin @('127.0.0.1','[::1]','::1') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') { throw 'ComfyUrl must be a numeric loopback origin. Read its actual port from this run; do not guess.' }
  foreach ($route in @('queue','history')) {
    try {
      $response=Invoke-WebRequest -Uri ($ComfyUrl.TrimEnd('/')+'/'+$route) -Method Get -TimeoutSec 10 -MaximumRedirection 0
      if ($response.RawContentLength -gt 4194304) { throw 'Provider response exceeds private capture bound.' }
      $response.Content | Set-Content -LiteralPath (Join-Path $ReportDir "comfy-$route-private.json") -Encoding utf8NoBOM
    } catch { Save-Json "comfy-$route-error.json" @{ available=$false; reason='Read-only provider inspection failed; do not interpret as an empty queue.' } }
  }
}
Save-Json 'SUMMARY.json' @{ run=$RunId; observed_utc=(Get-Date).ToUniversalTime().ToString('o'); report_directory=$ReportDir; run_modified=$false; jobs_submitted=0; processes_stopped=0; private_report=$true; warning='Raw command lines and optional provider dumps are LOCAL PRIVATE evidence. Redact before sending to any cloud model or WNP.' }
Write-Host "Private incident report: $ReportDir"
Write-Host 'No run state, media, provider queue, or process was changed.'
