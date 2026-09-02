#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$RepositoryRoot = 'C:\Users\theon\GitHub\TrackPrompt-Studio',
    [string]$OutputDirectory = "$env:USERPROFILE\Desktop",
    [switch]$OpenOutputFolder
)

# TrackPrompt bootstrap: explicitly load Microsoft.PowerShell.Utility.
$script:TrackPromptUtilityManifest = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1'

if (-not (Test-Path -LiteralPath $script:TrackPromptUtilityManifest -PathType Leaf)) {
    throw "Required PowerShell utility module was not found: $script:TrackPromptUtilityManifest"
}

Import-Module `
    -Name $script:TrackPromptUtilityManifest `
    -Force `
    -ErrorAction Stop


Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Stage([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

$repo = (Resolve-Path -LiteralPath $RepositoryRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $repo '.git') -PathType Container)) {
    throw "Not a Git repository: $repo"
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stage = Join-Path $env:TEMP "andromeda-release-source-kit-$stamp"
$zip = Join-Path $OutputDirectory "andromeda-release-source-kit-$stamp.zip"

Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $stage -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

# Deliberately source-only. No .env, credentials, audio, .blend files, frame
# sequences, previews, final media, model weights, databases, or runtime logs.
$patterns = @(
    'AGENTS.md',
    '.gitignore',
    'backend/app/cinematic/*.py',
    'backend/app/mission_control/*.py',
    'backend/tests/test_andromeda*.py',
    'backend/tests/test_*release*.py',
    'backend/tests/test_*operator*authorization*.py',
    'backend/tests/test_mission_control*.py',
    'blender/render_final_chunk.py',
    'blender/render_calibration_chunk.py',
    'blender/timeline_health_scan.py',
    'blender/trackprompt_visualizer/andromeda_story_v2.py',
    'tools/andromeda*.py',
    'tools/andromeda*.ps1',
    'tools/Invoke-Andromeda*.ps1',
    'tools/final_render_tooling.py',
    'tools/test-andromeda-production-paths.ps1',
    'tools/test-wzhk-mission-control.ps1',
    'production/andromeda-v2/*.ps1',
    'production/andromeda-v2/*.md',
    'production/andromeda-v2/*.json',
    'production/andromeda-v2/evidence/*.json',
    'render-profiles/trip-to-andromeda/andromeda-v2-*.json',
    'render-trackprompt-final.ps1',
    'encode-trackprompt-final.ps1',
    'verify-trackprompt-final.ps1',
    'WZHK-Media-Launcher.cmd',
    'frontend/src/mission-control/api.ts',
    'frontend/src/mission-control/types.ts',
    'frontend/src/mission-control/events.ts',
    'frontend/src/mission-control/screens/EncodeScreen.tsx',
    'frontend/src/mission-control/screens/LiveProgress.tsx',
    'frontend/src/mission-control/screens/RenderWorkspace.tsx',
    'docs/andromeda-v2-release-finalization.md',
    'docs/andromeda-v2-production-runbook.md',
    'docs/final-render-production-tooling.md',
    'docs/render-operation-architecture.md',
    'docs/mission-control-user-guide.md'
)

$files = [System.Collections.Generic.Dictionary[string,string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($pattern in $patterns) {
    $fullPattern = Join-Path $repo ($pattern -replace '/', [IO.Path]::DirectorySeparatorChar)
    foreach ($item in @(Get-ChildItem -Path $fullPattern -File -ErrorAction SilentlyContinue)) {
        $relative = [IO.Path]::GetRelativePath($repo, $item.FullName)
        if ($relative -match '(^|[\\/])(?:\.env|node_modules|\.git|\.venv|__pycache__|test-output|final-output|deep-models)([\\/]|$)') {
            continue
        }
        if ($item.Extension -in @('.wav','.mp3','.flac','.aiff','.aif','.ogg','.m4a','.mp4','.mov','.mkv','.blend','.blend1','.png','.jpg','.jpeg','.webp','.sqlite3','.db','.th','.pt','.pth')) {
            continue
        }
        $files[$relative] = $item.FullName
    }
}

if ($files.Count -eq 0) {
    throw 'No source files were found. The repository layout may have changed.'
}

Write-Stage "Copying $($files.Count) source and contract files"
$inventory = [System.Collections.Generic.List[object]]::new()
foreach ($relative in @($files.Keys | Sort-Object)) {
    $source = $files[$relative]
    $destination = Join-Path $stage $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
    $item = Get-Item -LiteralPath $source
    $inventory.Add([ordered]@{
        path = ($relative -replace '\\','/')
        sha256 = Get-Sha256 $source
        sizeBytes = [int64]$item.Length
        modifiedUtc = $item.LastWriteTimeUtc.ToString('o')
    })
}

Write-Stage 'Capturing safe Git metadata'
Push-Location $repo
try {
    (& git status --short --branch 2>&1) | Set-Content -LiteralPath (Join-Path $stage 'git-status.txt') -Encoding utf8
    (& git log --oneline --decorate -n 40 2>&1) | Set-Content -LiteralPath (Join-Path $stage 'git-log.txt') -Encoding utf8
    (& git diff --name-status 2>&1) | Set-Content -LiteralPath (Join-Path $stage 'git-diff-names.txt') -Encoding utf8
    (& git diff --cached --name-status 2>&1) | Set-Content -LiteralPath (Join-Path $stage 'git-cached-diff-names.txt') -Encoding utf8
    $head = (& git rev-parse HEAD).Trim()
    $branch = (& git branch --show-current).Trim()
}
finally {
    Pop-Location
}

# Include only the latest forensic metadata files, never the selected media.
$forensicRoots = @(Get-ChildItem -LiteralPath (Join-Path $repo 'test-output') -Directory -Filter 'andromeda-forensic-*' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending)
if ($forensicRoots.Count -gt 0) {
    $forensic = $forensicRoots[0]
    $forensicDest = Join-Path $stage 'forensic'
    New-Item -ItemType Directory -Path $forensicDest -Force | Out-Null
    foreach ($name in @('forensic-report.json','forensic-report.md','human-review-worksheet.md','pydantic-model-schemas.json')) {
        $candidate = Join-Path $forensic.FullName $name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            Copy-Item -LiteralPath $candidate -Destination (Join-Path $forensicDest $name) -Force
        }
    }
}

$manifest = [ordered]@{
    schemaVersion = '1.0.0'
    kind = 'trackprompt-andromeda-release-source-kit'
    generatedAt = [DateTimeOffset]::Now.ToString('o')
    repositoryRoot = $repo
    branch = $branch
    head = $head
    fileCount = $inventory.Count
    privacy = [ordered]@{
        containsAudio = $false
        containsBlendScenes = $false
        containsRenderedFrames = $false
        containsFinalMedia = $false
        containsCredentials = $false
        containsEnvironmentFiles = $false
    }
    files = $inventory
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $stage 'source-kit-inventory.json') -Encoding utf8

@"
# Andromeda release source kit

Generated: $([DateTimeOffset]::Now.ToString('o'))
Branch: $branch
HEAD: $head

This archive contains source code, tests, contracts, production JSON evidence,
render profiles, safe Git metadata, and the latest forensic metadata. It
intentionally excludes audio, Blender scenes, rendered frames, previews, final
media, databases, model weights, environment files, and credentials.
"@ | Set-Content -LiteralPath (Join-Path $stage 'README.md') -Encoding utf8

Write-Stage 'Creating ZIP'
Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
$zipSha = Get-Sha256 $zip

Write-Host "`nSOURCE KIT READY" -ForegroundColor Green
Write-Host "Path:   $zip"
Write-Host "SHA256: $zipSha"
Write-Host "Files:  $($inventory.Count)"
Write-Host 'Upload this ZIP. It contains no private media or credentials.'

if ($OpenOutputFolder) {
    Start-Process explorer.exe -ArgumentList @('/select,', $zip) | Out-Null
}
