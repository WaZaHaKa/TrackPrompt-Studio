[CmdletBinding()]
param(
    [switch]$Create,
    [switch]$AssembleOnly,
    [switch]$Status,
    [switch]$ResumePreSubmit,
    [switch]$Mute,
    [string]$AudioPath = '',
    [string]$GcpProjectId = '',
    [string]$GcsBucket = '',
    [string]$FfmpegPath = '',
    [string]$FfprobePath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Create -and $AssembleOnly) { throw 'Choose creation or local assembly.' }
if ($Status -and ($Create -or $AssembleOnly -or $ResumePreSubmit)) { throw 'Status is read-only; use it by itself.' }
if ($ResumePreSubmit -and -not $Create) { throw '-ResumePreSubmit requires -Create.' }
$Repo = [IO.Path]::GetFullPath($PSScriptRoot)
$Python = Join-Path $Repo 'backend/.venv/Scripts/python.exe'
$Script = Join-Path $Repo 'video-projects/hayale-flowers-promo/v2/run.py'
foreach ($Path in @($Python,$Script)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'The project or backend Python environment is missing.' }
}
function Find-HayaleV2Tool([string]$Explicit, [string]$Name) {
    if ($Explicit) {
        if (-not (Test-Path -LiteralPath $Explicit -PathType Leaf)) { throw "$Name executable is missing." }
        return (Resolve-Path -LiteralPath $Explicit).Path
    }
    $Command = Get-Command "$Name.exe" -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $Item = Get-Item -LiteralPath $Command.Source -Force
    if ($Item.LinkType -and $Item.Target) { return [string]@($Item.Target)[0] }
    return $Command.Source
}
$Arguments = @($Script)
if ($AudioPath) { $Arguments += @('--audio',$AudioPath) }
if ($Mute) { $Arguments += '--mute' }
if ($Status) {
    $Arguments += '--status'
} elseif ($Create -or $AssembleOnly) {
    if ($env:TRACKPROMPT_HAYALE_PREPARE_ONLY -eq '1') { throw 'This session is restricted to preparation.' }
    $Arguments += @('--ffmpeg',(Find-HayaleV2Tool $FfmpegPath 'ffmpeg'),'--ffprobe',(Find-HayaleV2Tool $FfprobePath 'ffprobe'))
    $Target = Join-Path $Repo '.trackprompt-data/video-generation/hayale-flowers-promo/v2-preparation/operator-target.json'
    if (-not (Test-Path -LiteralPath $Target)) {
        if (-not $GcpProjectId) { $GcpProjectId = Read-Host 'GCP project ID for this manual creation run' }
        if (-not $GcsBucket) { $GcsBucket = Read-Host 'Existing private GCS bucket name' }
    }
    if ($GcpProjectId) { $Arguments += @('--gcp-project',$GcpProjectId) }
    if ($GcsBucket) { $Arguments += @('--bucket',$GcsBucket) }
    if ($Create) { $Arguments += '--create' } else { $Arguments += '--assemble-only' }
    if ($ResumePreSubmit) { $Arguments += '--resume-pre-submit' }
} else {
    $Arguments += '--prepare'
    Write-Host 'PREPARATION ONLY: no GCP access, authorization, generation or video rendering.'
}
Push-Location $Repo
try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        $FailureCode = $LASTEXITCODE
        [Console]::Error.WriteLine("Hayale V2 stopped. Read the cause above; saved attempts are preserved.")
        exit $FailureCode
    }
} finally { Pop-Location }
