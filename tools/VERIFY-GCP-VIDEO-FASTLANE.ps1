[CmdletBinding()]
param(
    [string]$RepositoryRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Split-Path -Parent $PSScriptRoot
}
$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path

$python = Join-Path $RepositoryRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $python = $pythonCommand.Source
}

$ffmpegCandidates = @(
    $env:TRACKPROMPT_MC_FFMPEG_PATH,
    'C:\Users\theon\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe'
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
$ffmpeg = $ffmpegCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $ffmpeg) {
    $ffmpegCommand = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    if ($ffmpegCommand) { $ffmpeg = $ffmpegCommand.Source }
}
if (-not $ffmpeg) { throw 'FFmpeg is required for the bounded media verification.' }
$ffprobe = Join-Path (Split-Path -Parent $ffmpeg) 'ffprobe.exe'
if (-not (Test-Path -LiteralPath $ffprobe -PathType Leaf)) { throw 'ffprobe.exe was not found beside FFmpeg.' }

$oldPythonPath = $env:PYTHONPATH
$oldPath = $env:Path
$oldFfmpegPath = $env:TRACKPROMPT_MC_FFMPEG_PATH
$oldFfprobePath = $env:TRACKPROMPT_MC_FFPROBE_PATH
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("trackprompt-fastlane-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $temporary | Out-Null
$env:PYTHONPATH = Join-Path $RepositoryRoot 'backend'
$env:Path = "$(Split-Path -Parent $ffmpeg);$oldPath"
$env:TRACKPROMPT_MC_FFMPEG_PATH = $ffmpeg
$env:TRACKPROMPT_MC_FFPROBE_PATH = $ffprobe

try {
    Write-Host 'Compiling integrated Python source...'
    $sourceFiles = Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot 'backend\app\video_generation') -Filter '*.py' -File
    & $python -m py_compile @($sourceFiles.FullName)
    if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed.' }

    Write-Host 'Running focused backend contracts, Mission Control persistence, and real FFmpeg tests...'
    & $python -m pytest -q `
        (Join-Path $RepositoryRoot 'backend\tests\video_generation\test_video_generation_fastlane.py') `
        (Join-Path $RepositoryRoot 'backend\tests\video_generation\test_video_generation_api.py') `
        (Join-Path $RepositoryRoot 'backend\tests\test_mission_control_v2_store.py') `
        --basetemp (Join-Path $temporary 'pytest')
    if ($LASTEXITCODE -ne 0) { throw 'Focused backend tests failed.' }

    Write-Host 'Parsing every content-package and schema JSON file...'
    & $python -c "import json,pathlib; root=pathlib.Path(r'$RepositoryRoot'); files=list(root.glob('video-projects/the-glitch-is-me/*.json'))+list(root.glob('schemas/*.json'))+list(root.glob('config/*.json')); [json.loads(p.read_text(encoding='utf-8')) for p in files]; print(f'Parsed {len(files)} JSON files')"
    if ($LASTEXITCODE -ne 0) { throw 'JSON parsing failed.' }

    foreach ($config in @('project-config.json', 'project-config.quality-1080p.json')) {
        $output = Join-Path $temporary ($config + '.plan.json')
        & $python -m app.video_generation.cli compile `
            --project-config (Join-Path $RepositoryRoot "video-projects\the-glitch-is-me\$config") `
            --creative-bible (Join-Path $RepositoryRoot 'video-projects\the-glitch-is-me\creative-bible.json') `
            --shot-bank (Join-Path $RepositoryRoot 'video-projects\the-glitch-is-me\shot-bank.json') `
            --gcs-bucket 'gs://example-trackprompt-video' `
            --output $output
        if ($LASTEXITCODE -ne 0) { throw "Plan compilation failed for $config" }
    }

    Write-Host 'Confirming the dormant GA 4K profile fails closed before authorization...'
    & $python -m app.video_generation.cli compile `
        --project-config (Join-Path $RepositoryRoot 'video-projects\the-glitch-is-me\project-config.4k-optional.json') `
        --creative-bible (Join-Path $RepositoryRoot 'video-projects\the-glitch-is-me\creative-bible.json') `
        --shot-bank (Join-Path $RepositoryRoot 'video-projects\the-glitch-is-me\shot-bank.json') `
        --gcs-bucket 'gs://example-trackprompt-video' `
        --output (Join-Path $temporary 'unexpected-4k.plan.json')
    if ($LASTEXITCODE -eq 0) { throw 'The unsupported GA 4K plan unexpectedly compiled.' }

    Write-Host 'Running the focused React workflow test and strict TypeScript compiler...'
    Push-Location (Join-Path $RepositoryRoot 'frontend')
    try {
        & npm.cmd test -- --run src/mission-control/screens/VideoGenerationScreen.test.tsx
        if ($LASTEXITCODE -ne 0) { throw 'Focused React test failed.' }
        & npm.cmd run typecheck
        if ($LASTEXITCODE -ne 0) { throw 'TypeScript compilation failed.' }
    }
    finally { Pop-Location }

    Write-Host 'GCP VIDEO FAST LANE VALIDATION PASSED.' -ForegroundColor Green
    Write-Host 'No cloud generation request was made.'
}
finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:Path = $oldPath
    $env:TRACKPROMPT_MC_FFMPEG_PATH = $oldFfmpegPath
    $env:TRACKPROMPT_MC_FFPROBE_PATH = $oldFfprobePath
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
