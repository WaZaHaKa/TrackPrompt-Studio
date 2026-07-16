#requires -Version 5.1
[CmdletBinding()]
param([switch]$SkipEndToEnd)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
Set-Location -LiteralPath $RepoRoot
$compose = @("compose", "-f", "compose.yaml", "-f", "compose.full-gpu.yaml")

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments, [string]$Description)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Description failed with exit code $LASTEXITCODE." }
}

Invoke-Checked "docker" ($compose + @("ps")) "Compose service status"
$health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 10
$capabilities = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/capabilities" -TimeoutSec 30
$capabilities | ConvertTo-Json -Depth 20 | Write-Host
if ($health.status -notin @("ok", "degraded")) { throw "Backend health is not usable." }

Invoke-Checked "docker" ($compose + @("exec", "-T", "backend", "python", "-m", "app.diagnostics.gpu")) "Torch/CTranslate2 GPU smoke"
Invoke-Checked "docker" ($compose + @("exec", "-T", "backend", "python", "-m", "app.diagnostics.demucs", "--smoke")) "Demucs GPU inference smoke"
Invoke-Checked "docker" ($compose + @("exec", "-T", "backend", "python", "-m", "app.diagnostics.genre", "--smoke")) "CLAP GPU inference smoke"
Invoke-Checked "docker" ($compose + @("exec", "-T", "backend", "python", "-m", "app.diagnostics.lyrics", "--smoke")) "faster-whisper GPU inference smoke"
Invoke-Checked "docker" ($compose + @("exec", "-T", "backend", "python", "-m", "app.diagnostics.prompt_writer", "--smoke")) "Ollama prompt-writer inference smoke"

if (-not $SkipEndToEnd) {
    $fixtureDirectory = Join-Path $RepoRoot "test-fixtures"
    New-Item -ItemType Directory -Force -Path $fixtureDirectory | Out-Null
    $fixture = Join-Path $fixtureDirectory "permitted-local-speech-smoke.wav"
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try { $synth.SetOutputToWaveFile($fixture); $synth.Speak("Synthetic local verification phrase for Track Prompt Studio") }
    finally { $synth.Dispose() }
    $responseText = curl.exe -sS -X POST "http://127.0.0.1:8000/api/analyses" -F "file=@$fixture;type=audio/wav" -F "mode=deep" -F "permissionConfirmed=true" -F "enableGenreAnalysis=true" -F "enableLyricalAnalysis=true" -F "lyricsConsentConfirmed=true" -F "deriveLyricalThemes=false" -F "allowFeatureFallback=false"
    if ($LASTEXITCODE -ne 0) { throw "Synthetic full-flow upload failed." }
    $job = $responseText | ConvertFrom-Json
    for ($attempt = 0; $attempt -lt 180; $attempt++) {
        Start-Sleep -Seconds 2
        $job = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyses/$($job.jobId)" -TimeoutSec 10
        if ($job.status -in @("completed", "failed", "cancelled")) { break }
    }
    if ($job.status -ne "completed" -or $job.analysis.effectiveMode -ne "deep") { throw "Synthetic Deep full flow did not complete in Deep mode." }
    if ($null -eq $job.analysis.genreAnalysis) { throw "Synthetic full flow did not produce genre evidence." }
    $topGenre = $job.analysis.genreAnalysis.broadCandidates | Select-Object -First 1
    $genrePatch = @{ updates = @(@{ candidateId = $topGenre.id; accepted = $true }) } | ConvertTo-Json -Depth 5
    Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:8000/api/analyses/$($job.jobId)/genre" -ContentType "application/json" -Body $genrePatch | Out-Null
    $promptRequest = @{
        promptEngineMode = "creative"; genreInterpretationMode = "strict_top"; lyricsInfluenceMode = "prosody_only"
        candidateCount = 3; creativity = 0.65; variationSeed = 24680; outputLanguage = "English"
        generationIntent = "preserve_core_character"; promptLength = "balanced"; includeBpm = $true; includeKey = $true
        instrumental = $false; preserveEnergyArc = $true; preserveInstrumentation = $true; preserveStructure = $true
        preserveGroove = $true; exclusions = @(); disabledFeaturePaths = @(); userOverrides = @{}; lockSeed = $true
        lockedFeaturePaths = @(); includeDetectedGenre = $true; acceptedGenreIds = @($topGenre.id)
        includeLyricalThemes = $false; desiredTransformations = @()
    } | ConvertTo-Json -Depth 8
    $prompts = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/analyses/$($job.jobId)/prompt" -ContentType "application/json" -Body $promptRequest -TimeoutSec 180
    if ($prompts.candidates.Count -lt 1) { throw "Creative prompt generation returned no validated candidate." }
    if ($job.analysis.lyricsSummary.transcriptAvailable) {
        $lyrics = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyses/$($job.jobId)/lyrics" -TimeoutSec 10
        foreach ($segment in $lyrics.segments) {
            if ($segment.text.Length -ge 8 -and $prompts.primaryPrompt.Contains($segment.text)) { throw "Raw transcript text leaked into the prompt." }
        }
        Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:8000/api/analyses/$($job.jobId)/lyrics" | Out-Null
    }
    Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:8000/api/analyses/$($job.jobId)" | Out-Null
    Remove-Item -LiteralPath $fixture -Force -ErrorAction SilentlyContinue
    Write-Host "Synthetic Deep + genre + lyrics + Creative flow passed." -ForegroundColor Green
}
