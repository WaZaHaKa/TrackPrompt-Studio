[CmdletBinding()]
param(
    [ValidateSet('Audit', 'Preflight', 'Start')]
    [string]$Mode = 'Audit',

    [string]$RepositoryRoot,

    [ValidateSet('HorizontalOnly', 'HorizontalAndVertical')]
    [string]$OutputMatrix = 'HorizontalOnly',

    [string]$SourceAudioPath,

    [string]$SourceCuePath,

    [string[]]$SourceSearchRoot = @(),

    [switch]$OpenReportFolder
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Stage {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Resolve-RepoRoot {
    param([string]$Requested)
    if ($Requested) {
        return (Resolve-Path -LiteralPath $Requested).Path
    }
    return (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
}

function Resolve-Python {
    param([Parameter(Mandatory)][string]$Repo)
    $candidates = @(
        (Join-Path $Repo 'backend\.venv\Scripts\python.exe'),
        (Join-Path $Repo '.venv\Scripts\python.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw 'No Python interpreter was found. Expected backend\.venv\Scripts\python.exe or python.exe on PATH.'
}

function Invoke-ForensicAudit {
    param(
        [Parameter(Mandatory)][string]$Repo,
        [Parameter(Mandatory)][string]$Output,
        [string]$PreflightLog
    )
    $python = Resolve-Python -Repo $Repo
    $auditScript = Join-Path $Repo 'tools\andromeda_forensic_audit.py'
    if (-not (Test-Path -LiteralPath $auditScript -PathType Leaf)) {
        throw "Forensic Python tool was not found: $auditScript"
    }

    $matrix = if ($OutputMatrix -eq 'HorizontalAndVertical') { 'dual' } else { 'horizontal-only' }
    $arguments = @(
        $auditScript,
        '--repository-root', $Repo,
        '--output-root', $Output,
        '--desired-matrix', $matrix
    )
    if ($SourceAudioPath) { $arguments += @('--source-audio', $SourceAudioPath) }
    if ($SourceCuePath) { $arguments += @('--source-cue', $SourceCuePath) }
    foreach ($root in $SourceSearchRoot) {
        if ($root) { $arguments += @('--source-search-root', $root) }
    }
    if ($PreflightLog) { $arguments += @('--preflight-log', $PreflightLog) }

    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Forensic audit exited with code $LASTEXITCODE."
    }
}

$repo = Resolve-RepoRoot -Requested $RepositoryRoot
Set-Location -LiteralPath $repo
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outputRoot = Join-Path $repo "test-output\andromeda-forensic-$timestamp"
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

Write-Stage 'Running read-only production forensics'
Invoke-ForensicAudit -Repo $repo -Output $outputRoot

$reportPath = Join-Path $outputRoot 'andromeda-forensic-report.json'
$report = Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json

Write-Host "`nForensic status: $($report.summary.status)" -ForegroundColor Yellow
Write-Host "Report: $reportPath"
Write-Host "Upload bundle: $(Join-Path $outputRoot 'andromeda-forensic-upload.zip')"

if ($Mode -eq 'Preflight' -or $Mode -eq 'Start') {
    if (-not $report.selectedCandidate) {
        throw 'No real package candidate was selected. Upload the forensic ZIP for diagnosis.'
    }
    $packagePath = [string]$report.selectedCandidate.manifestPath
    $helper = Join-Path $repo 'tools\Invoke-AndromedaLatestProduction.ps1'
    if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
        throw "Latest-production helper not found: $helper"
    }

    if ($Mode -eq 'Preflight') {
        Write-Stage 'Running the existing safe preflight against the exact selected package'
        $preflightLog = Join-Path $outputRoot 'existing-production-preflight.log'
        $arguments = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', $helper,
            '-Mode', 'Preflight',
            '-ReleaseBundlePath', $packagePath
        )
        if ($OutputMatrix -eq 'HorizontalAndVertical') { $arguments += '-EnableVertical' }
        & powershell.exe @arguments *>&1 | Tee-Object -FilePath $preflightLog
        $preflightExitCode = $LASTEXITCODE
        Write-Host "Existing preflight exit code: $preflightExitCode"

        Write-Stage 'Refreshing forensic report with the exact preflight output'
        Invoke-ForensicAudit -Repo $repo -Output $outputRoot -PreflightLog $preflightLog
        $report = Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
    }
}

if ($Mode -eq 'Start') {
    if (-not $report.summary.readyForStart) {
        Write-Host "`nSTART BLOCKED — the forensic report did not establish a coherent, unheld, human-closed release with matching private sources." -ForegroundColor Red
        Write-Host 'Upload andromeda-forensic-upload.zip from the report folder. No guard was bypassed.'
        exit 2
    }

    $audio = $SourceAudioPath
    if (-not $audio) {
        $audioMatches = @($report.sourceDiscovery.'source-audio'.matches)
        if ($audioMatches.Count -eq 1) { $audio = [string]$audioMatches[0].path }
    }
    $cue = $SourceCuePath
    if (-not $cue) {
        $cueMatches = @($report.sourceDiscovery.'source-cue'.matches)
        if ($cueMatches.Count -eq 1) { $cue = [string]$cueMatches[0].path }
    }
    if (-not $audio -or -not $cue) {
        throw 'Start requires exact source audio and cue paths. Supply -SourceAudioPath and -SourceCuePath, or ensure the audit finds exactly one hash match for each.'
    }

    $packagePath = [string]$report.selectedCandidate.manifestPath
    $packageSha = [string]$report.selectedCandidate.manifestSha256
    $matrixLabel = if ($OutputMatrix -eq 'HorizontalAndVertical') { 'HORIZONTAL+VERTICAL' } else { 'HORIZONTAL-ONLY' }
    $phrase = "START ANDROMEDA V2 | $matrixLabel | PACKAGE $($packageSha.Substring(0, 12).ToUpperInvariant())"

    Write-Host "`nThis will cross the production-start boundary and may occupy the machine for many hours." -ForegroundColor Yellow
    Write-Host "Type the exact phrase:`n$phrase" -ForegroundColor Magenta
    $typed = Read-Host
    if ($typed -cne $phrase) {
        Write-Host 'Exact phrase did not match. Nothing was started.' -ForegroundColor Yellow
        exit 3
    }

    $launcher = Join-Path $repo 'WZHK-Media-Launcher.cmd'
    if (Test-Path -LiteralPath $launcher -PathType Leaf) {
        Write-Stage 'Opening Mission Control'
        Start-Process -FilePath $launcher | Out-Null
        Start-Sleep -Seconds 2
    }

    Write-Stage 'Starting the exact release through the existing guarded production helper'
    $helper = Join-Path $repo 'tools\Invoke-AndromedaLatestProduction.ps1'
    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $helper,
        '-Mode', 'StartAndEncode',
        '-ReleaseBundlePath', $packagePath,
        '-SourceAudioPath', $audio,
        '-SourceCuePath', $cue
    )
    if ($OutputMatrix -eq 'HorizontalAndVertical') { $arguments += '-EnableVertical' }
    & powershell.exe @arguments
    exit $LASTEXITCODE
}

if ($OpenReportFolder) {
    Start-Process explorer.exe $outputRoot | Out-Null
}

Write-Host "`nAudit complete. Upload this file here:" -ForegroundColor Green
Write-Host (Join-Path $outputRoot 'andromeda-forensic-upload.zip') -ForegroundColor White
