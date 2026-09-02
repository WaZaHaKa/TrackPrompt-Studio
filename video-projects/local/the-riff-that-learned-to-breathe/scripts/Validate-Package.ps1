[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

$Required = @(
    'README.md',
    'CODEX_IMPLEMENTATION_PROMPT.md',
    'project-config.json',
    'creative-bible.json',
    'continuity-profile.json',
    'chapter-map.json',
    'shot-bank.json',
    'edit-blueprint.json',
    'render-plan.json',
    'model-profile.json',
    'hardware-policy.json',
    'rights-and-credits.json',
    'persistent-analysis-policy.json',
    'prompts\scene-prompts.json',
    'prompts\transition-prompts.json',
    'timing\provisional-timeline.json',
    'timing\sync-policy.json',
    'workflows\comfyui-wan22-i2v-a14b-workflow-contract.json'
)

foreach ($Relative in $Required) {
    $Path = Join-Path $Root $Relative
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Fail "Missing required file: $Relative"
    }
}

$JsonFiles = Get-ChildItem -LiteralPath $Root -Recurse -File -Filter '*.json'
foreach ($File in $JsonFiles) {
    try {
        $null = Get-Content -LiteralPath $File.FullName -Raw -Encoding UTF8 | ConvertFrom-Json -Depth 100
    } catch {
        Fail "Invalid JSON: $($File.FullName): $($_.Exception.Message)"
    }
}

$ShotBank = Get-Content -LiteralPath (Join-Path $Root 'shot-bank.json') -Raw -Encoding UTF8 | ConvertFrom-Json -Depth 100
if ($ShotBank.shotCount -ne 16 -or $ShotBank.shots.Count -ne 16) {
    Fail "shot-bank.json must contain exactly 16 shots."
}

$ExpectedIds = 1..16 | ForEach-Object { 'shot-' + $_.ToString('000') }
$ActualIds = @($ShotBank.shots | ForEach-Object { $_.shotId })
if ((Compare-Object -ReferenceObject $ExpectedIds -DifferenceObject $ActualIds).Count -ne 0) {
    Fail "Shot IDs must be exactly shot-001 through shot-016."
}

$ExpectedStart = 0.0
foreach ($Shot in ($ShotBank.shots | Sort-Object order)) {
    $Start = [double]$Shot.provisionalStartSeconds
    $End = [double]$Shot.provisionalEndSeconds
    if ([Math]::Abs($Start - $ExpectedStart) -gt 0.000001) {
        Fail "Timing gap/overlap before $($Shot.shotId): expected $ExpectedStart, found $Start."
    }
    if ($End -le $Start) {
        Fail "Invalid duration for $($Shot.shotId)."
    }
    if ([string]::IsNullOrWhiteSpace([string]$Shot.keyframePrompt) -or
        [string]::IsNullOrWhiteSpace([string]$Shot.prompt) -or
        [string]::IsNullOrWhiteSpace([string]$Shot.negativePrompt)) {
        Fail "Missing prompt field for $($Shot.shotId)."
    }
    $WordCount = ([regex]::Matches([string]$Shot.prompt, "\b[\w'-]+\b")).Count
    if ($WordCount -gt 100) {
        Fail "Wan motion prompt exceeds 100 words for $($Shot.shotId): $WordCount."
    }
    $ExpectedStart = $End
}

if ([Math]::Abs($ExpectedStart - 149.0) -gt 0.000001) {
    Fail "Provisional timeline must end at exactly 149.0 seconds; found $ExpectedStart."
}

$AudioFiles = @(Get-ChildItem -LiteralPath (Join-Path $Root 'audio') -File | Where-Object { $_.Extension -match '^\.(wav|flac|mp3|m4a|aac|ogg)$' })
if ($AudioFiles.Count -gt 1) {
    Fail "Place exactly one audio track in audio\. Found $($AudioFiles.Count)."
}

Write-Host "PACKAGE_VALIDATION_PASS"
Write-Host "Root: $Root"
Write-Host "JSON files: $($JsonFiles.Count)"
Write-Host "Shots: 16"
Write-Host "Provisional duration: 149.000 seconds"
if ($AudioFiles.Count -eq 1) {
    Write-Host "Audio: $($AudioFiles[0].Name)"
} else {
    Write-Warning "No track is present yet. Add audio\track.wav, audio\track.flac, or audio\track.mp3 before rendering."
}
