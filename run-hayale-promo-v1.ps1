[CmdletBinding()]
param(
    [switch]$Render,
    [ValidatePattern('^$|^v1(?:-[a-zA-Z0-9-]{1,80})?$')][string]$VerifyRun = '',
    [string]$FfmpegPath = '',
    [string]$FfprobePath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Render -and $VerifyRun) { throw 'Choose rendering or verification, not both.' }
$Repo = [IO.Path]::GetFullPath($PSScriptRoot)
$Python = Join-Path $Repo 'backend/.venv/Scripts/python.exe'
$Script = Join-Path $Repo 'video-projects/hayale-flowers-promo/build_v1.py'
foreach ($Path in @($Python, $Script, (Join-Path $Repo 'AGENTS.md'))) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'Required repository file or backend environment is missing.' }
}

function Resolve-MediaExecutable([string]$ExplicitPath, [string]$Name) {
    if ($ExplicitPath) {
        if (-not (Test-Path -LiteralPath $ExplicitPath -PathType Leaf)) { throw "$Name executable is missing." }
        return (Resolve-Path -LiteralPath $ExplicitPath).Path
    }
    $Command = Get-Command "$Name.exe" -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $Item = Get-Item -LiteralPath $Command.Source -Force
    if ($Item.LinkType -and $Item.Target) { return [string]@($Item.Target)[0] }
    return $Command.Source
}

$Arguments = @($Script)
if ($Render -or $VerifyRun) {
    $Arguments += @('--ffmpeg', (Resolve-MediaExecutable $FfmpegPath 'ffmpeg'), '--ffprobe', (Resolve-MediaExecutable $FfprobePath 'ffprobe'))
}
if ($Render) {
    $RunId = 'v1-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + ([guid]::NewGuid().ToString('N').Substring(0, 8))
    $Arguments += @('--render', '--run-id', $RunId)
    Write-Host 'Creating the master and hero locally. This foreground window owns the run.'
    Write-Host "Output: .trackprompt-data/video-generation/hayale-flowers-promo/$RunId"
} elseif ($VerifyRun) {
    $Arguments += @('--verify-only', '--run-id', $VerifyRun)
} else {
    Write-Host 'PLAN ONLY. Use -Render or RUN-HAYALE-PROMO-V1.cmd when ready to create the videos.'
}
Push-Location $Repo
try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Hayale command failed (exit $LASTEXITCODE). Prior runs and originals are preserved." }
} finally {
    Pop-Location
}
