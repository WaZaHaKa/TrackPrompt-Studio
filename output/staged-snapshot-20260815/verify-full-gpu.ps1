#requires -Version 5.1
<#
.SYNOPSIS
    Verifies the running TrackPrompt Studio full-GPU profile.

.DESCRIPTION
    Runs dependency, import, GPU, adapter, API, and optional synthetic end-to-end
    checks against the canonical full-GPU Compose stack. No model or data volume
    is deleted.
#>
[CmdletBinding()]
param([switch]$SkipEndToEnd)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
Set-Location -LiteralPath $RepoRoot
$script:ComposeArguments = @("compose", "-f", "compose.yaml", "-f", "compose.full-gpu.yaml")

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )
    & $Executable @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode."
    }
}

function Invoke-ComposeChecked {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )
    Invoke-NativeChecked -Executable "docker" -Arguments ($script:ComposeArguments + $Arguments) -Description $Description
}

function Assert-ServiceReady {
    param([Parameter(Mandatory = $true)][string]$Service)
    $containerId = & docker @($script:ComposeArguments + @("ps", "--all", "-q", $Service)) 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($containerId | Out-String))) {
        throw "$Service has no Compose container."
    }
    $resolvedId = (($containerId | Out-String).Trim() -split "\s+" | Select-Object -Last 1)
    $format = "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}"
    $state = & docker inspect --format $format $resolvedId 2>$null
    if ($LASTEXITCODE -ne 0 -or (($state | Out-String).Trim()) -notin @("healthy", "running")) {
        throw "$Service is not healthy or running."
    }
}

function ConvertTo-NormalizedPrivacyText {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) {
        return ""
    }
    $text = if ($Value -is [string]) {
        [string]$Value
    }
    else {
        $Value | ConvertTo-Json -Depth 100 -Compress
    }
    return (($text.ToLowerInvariant() -replace "[^\p{L}\p{Nd}']+", " ") -replace "\s+", " ").Trim()
}

function Assert-NoPrivateTranscriptLeak {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][object]$Transcript,
        [Parameter(Mandatory = $true)][string]$Context
    )
    $candidateText = ConvertTo-NormalizedPrivacyText -Value $Value
    $orderedSegments = @($Transcript.segments | Sort-Object startSeconds, endSeconds, id)
    $normalizedSegments = @(
        $orderedSegments |
            ForEach-Object { ConvertTo-NormalizedPrivacyText -Value ([string]$_.text) } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    foreach ($segmentText in $normalizedSegments) {
        if ($segmentText.Length -ge 8 -and $candidateText.Contains($segmentText)) {
            throw "Raw transcript text leaked into $Context."
        }
    }
    $transcriptWords = @(($normalizedSegments -join " ") -split "\s+" | Where-Object { $_ })
    if ($transcriptWords.Count -ge 4) {
        for ($index = 0; $index -le $transcriptWords.Count - 4; $index++) {
            $fragment = ($transcriptWords[$index..($index + 3)] -join " ")
            if ($candidateText.Contains($fragment)) {
                throw "A normalized cross-segment transcript fragment leaked into $Context."
            }
        }
    }
}

function Assert-MaterialCandidateDiversity {
    param(
        [Parameter(Mandatory = $true)][object[]]$Candidates,
        [Parameter(Mandatory = $true)][string]$Mode
    )
    for ($leftIndex = 0; $leftIndex -lt $Candidates.Count; $leftIndex++) {
        for ($rightIndex = $leftIndex + 1; $rightIndex -lt $Candidates.Count; $rightIndex++) {
            $leftWords = @((ConvertTo-NormalizedPrivacyText -Value ([string]$Candidates[$leftIndex].prompt)) -split "\s+" | Where-Object { $_ })
            $rightWords = @((ConvertTo-NormalizedPrivacyText -Value ([string]$Candidates[$rightIndex].prompt)) -split "\s+" | Where-Object { $_ })
            $leftOpening = @(($leftWords | Select-Object -First 5)) -join " "
            $rightOpening = @(($rightWords | Select-Object -First 5)) -join " "
            if ($leftOpening -eq $rightOpening) {
                throw "$Mode candidates reused the same opening."
            }
            $leftBigrams = @{}
            for ($index = 0; $index -lt $leftWords.Count - 1; $index++) {
                $leftBigrams[($leftWords[$index..($index + 1)] -join " ")] = $true
            }
            $rightBigrams = @{}
            for ($index = 0; $index -lt $rightWords.Count - 1; $index++) {
                $rightBigrams[($rightWords[$index..($index + 1)] -join " ")] = $true
            }
            $intersection = @($leftBigrams.Keys | Where-Object { $rightBigrams.ContainsKey($_) }).Count
            $union = @($leftBigrams.Keys + $rightBigrams.Keys | Sort-Object -Unique).Count
            if ($union -eq 0 -or ($intersection / $union) -ge 0.72) {
                throw "$Mode candidates were not materially diverse."
            }
        }
    }
}

$fixture = $null
$jobId = $null
$uploadAcceptedWithoutJobId = $false
try {
    Invoke-ComposeChecked -Arguments @("config", "--quiet") -Description "Full-GPU Compose validation"
    Assert-ServiceReady -Service "prompt-writer"
    Assert-ServiceReady -Service "backend"
    Assert-ServiceReady -Service "frontend"
    Invoke-ComposeChecked -Arguments @("ps") -Description "Compose service status"

    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 10
    $capabilities = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/capabilities" -TimeoutSec 30
    if ($health.status -ne "ok") {
        throw "Backend health is '$($health.status)' rather than ok."
    }
    if (-not $capabilities.fastMode.available -or -not $capabilities.deepMode.available) {
        throw "Fast and Deep modes are not both available."
    }
    if (-not $capabilities.genreTagger.available) {
        throw "The genre tagger is unavailable."
    }
    if (-not $capabilities.lyricsAdapter.available) {
        throw "The lyrics adapter is unavailable."
    }
    if (
        -not $capabilities.promptWriter.serviceReachable -or
        -not $capabilities.promptWriter.reliableAvailable -or
        -not $capabilities.promptWriter.creativeAvailable -or
        -not $capabilities.promptWriter.experimentalAvailable
    ) {
        throw "The prompt writer or one of its advertised modes is unavailable."
    }
    $deepAdapter = @($capabilities.deepMode.adapters | Where-Object { $_.enabled }) | Select-Object -First 1
    if (
        $null -eq $deepAdapter -or [string]$deepAdapter.selectedDevice -ne "cuda" -or
        [string]$capabilities.genreTagger.effectiveDevice -ne "cuda" -or
        [string]$capabilities.lyricsAdapter.effectiveDevice -ne "cuda" -or
        [string]$capabilities.promptWriter.effectiveDevice -ne "cuda"
    ) {
        throw "One or more enabled full-GPU adapters did not declare CUDA as the effective device."
    }
    Write-Host ($health | ConvertTo-Json -Depth 30)
    Write-Host ($capabilities | ConvertTo-Json -Depth 50)

    Invoke-ComposeChecked -Arguments @("exec", "-T", "backend", "python", "-m", "pip", "check") -Description "Python dependency integrity"
    Invoke-ComposeChecked -Arguments @(
        "exec", "-T", "backend", "python", "-m", "pip", "show",
        "numpy", "scipy", "librosa", "scikit-learn", "torch", "torchaudio", "demucs", "transformers", "tokenizers",
        "huggingface-hub", "safetensors", "sentencepiece", "faster-whisper",
        "ctranslate2"
    ) -Description "Important full-GPU package versions"
    Invoke-ComposeChecked -Arguments @("exec", "-T", "backend", "python", "-m", "app.diagnostics.dependencies") -Description "Reviewed direct dependency versions"
    Invoke-ComposeChecked -Arguments @("exec", "-T", "backend", "python", "-m", "app.diagnostics.imports") -Description "Fresh-process import diagnostic"
    Invoke-ComposeChecked -Arguments @("exec", "-T", "backend", "python", "-m", "app.diagnostics.gpu") -Description "Torch and CTranslate2 GPU diagnostic"
    Invoke-ComposeChecked -Arguments @("exec", "-T", "backend", "python", "-m", "app.diagnostics.demucs", "--smoke") -Description "Demucs GPU inference smoke"
    Invoke-ComposeChecked -Arguments @("exec", "-T", "backend", "python", "-m", "app.diagnostics.genre", "--smoke") -Description "Genre GPU inference smoke"
    Invoke-ComposeChecked -Arguments @("exec", "-T", "backend", "python", "-m", "app.diagnostics.lyrics", "--smoke") -Description "Lyrics GPU inference smoke"
    Invoke-ComposeChecked -Arguments @("exec", "-T", "backend", "python", "-m", "app.diagnostics.prompt_writer", "--smoke") -Description "Prompt-writer inference smoke"
    Invoke-ComposeChecked -Arguments @("exec", "-T", "backend", "python", "-m", "app.diagnostics.capabilities") -Description "Combined capability diagnostic"

    if (-not $SkipEndToEnd) {
        $fixtureDirectory = Join-Path $RepoRoot "test-fixtures"
        New-Item -ItemType Directory -Force -Path $fixtureDirectory | Out-Null
        $fixture = Join-Path $fixtureDirectory ("permitted-local-speech-smoke-{0}.wav" -f [guid]::NewGuid().ToString("N"))
        Add-Type -AssemblyName System.Speech
        $synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
        try {
            $synthesizer.SetOutputToWaveFile($fixture)
            $synthesizer.Speak("Synthetic local verification phrase for Track Prompt Studio")
        }
        finally {
            $synthesizer.Dispose()
        }

        $responseText = curl.exe --fail-with-body -sS -X POST "http://127.0.0.1:8000/api/analyses" `
            -F "file=@$fixture;type=audio/wav" `
            -F "mode=deep" `
            -F "permissionConfirmed=true" `
            -F "enableGenreAnalysis=true" `
            -F "enableLyricalAnalysis=true" `
            -F "lyricsConsentConfirmed=true" `
            -F "deriveLyricalThemes=false" `
            -F "allowFeatureFallback=false"
        if ($LASTEXITCODE -ne 0) {
            throw "Synthetic full-flow upload failed."
        }
        $uploadAcceptedWithoutJobId = $true
        $job = $responseText | ConvertFrom-Json
        $jobId = $job.jobId
        $parsedJobId = [guid]::Empty
        if ([string]::IsNullOrWhiteSpace([string]$jobId) -or -not [guid]::TryParse([string]$jobId, [ref]$parsedJobId)) {
            throw "Synthetic full-flow upload did not return a valid job ID."
        }
        $uploadAcceptedWithoutJobId = $false
        for ($attempt = 0; $attempt -lt 180; $attempt++) {
            Start-Sleep -Seconds 2
            $job = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyses/$jobId" -TimeoutSec 10
            if ($job.status -in @("completed", "failed", "cancelled")) {
                break
            }
        }
        if ($job.status -ne "completed" -or $job.analysis.effectiveMode -ne "deep") {
            throw "Synthetic Deep full flow did not complete in Deep mode."
        }
        if ($null -eq $job.analysis.genreAnalysis) {
            throw "Synthetic full flow did not produce genre evidence."
        }
        if (
            [string]$job.analysis.deepDiagnostics.selectedDevice -ne "cuda" -or
            [string]$job.analysis.genreAnalysis.selectedDevice -ne "cuda" -or
            [string]$job.analysis.lyricsSummary.selectedDevice -ne "cuda"
        ) {
            throw "Synthetic Deep analysis did not prove CUDA execution for Demucs, genre, and lyrics."
        }
        $genreAnalysis = $job.analysis.genreAnalysis
        if ($genreAnalysis.taxonomyVersion -ne "2.0.0") {
            throw "Synthetic genre flow did not use taxonomy 2.0.0."
        }
        if (@($genreAnalysis.windowEvidence).Count -eq 0) {
            throw "Synthetic genre flow returned no representative-window evidence."
        }
        if (@($genreAnalysis.windowEvidence | Where-Object { @($_.sectionIds).Count -gt 0 }).Count -eq 0) {
            throw "Synthetic genre windows were not mapped to structural sections."
        }
        foreach ($window in @($genreAnalysis.windowEvidence)) {
            if (
                [double]$window.weight -lt 0.0 -or [double]$window.weight -gt 1.0 -or
                [double]$window.representativeness -lt 0.0 -or [double]$window.representativeness -gt 1.0
            ) {
                throw "Synthetic genre window weights were outside their declared bounds."
            }
        }
        $windowKinds = @($genreAnalysis.windowEvidence | ForEach-Object { [string]$_.kind })
        if (@($windowKinds | Where-Object { $_ -in @("middle", "repeated-groove", "whole-track") }).Count -eq 0) {
            throw "Synthetic genre analysis did not retain a central representative window."
        }
        if (
            @($genreAnalysis.windowEvidence).Count -gt 1 -and
            @($windowKinds | Where-Object { $_ -in @("high-energy", "median-energy", "percussion", "intro", "outro") }).Count -eq 0
        ) {
            throw "Synthetic multi-window genre analysis did not retain a distinct contextual or energy view."
        }
        $topGenre = $job.analysis.genreAnalysis.broadCandidates | Select-Object -First 1
        $topSubgenre = $job.analysis.genreAnalysis.subgenreCandidates | Select-Object -First 1
        if ($null -eq $topGenre) {
            throw "Synthetic genre flow returned no broad-family candidate."
        }
        $returnedBroadIds = @($job.analysis.genreAnalysis.broadCandidates | ForEach-Object { [string]$_.id })
        if ($null -ne $topSubgenre -and $returnedBroadIds -notcontains [string]$topSubgenre.parent) {
            throw "Synthetic subgenre ranking references a broad family outside the returned hierarchy."
        }
        if (@($job.analysis.styleAndMood.broadStyle.value) -notcontains $topGenre.label) {
            throw "Legacy broad-style projection is not synchronized with authoritative genre state."
        }
        if (
            (@($job.analysis.styleAndMood.genreBlend.value) -join "|") -ne
            (@($genreAnalysis.blendCandidates) -join "|")
        ) {
            throw "Legacy genre-blend projection is not synchronized with authoritative genre state."
        }
        if ([string]$genreAnalysis.confidence -notin @("low", "medium", "high", "unknown")) {
            throw "Synthetic genre confidence is outside the declared confidence vocabulary."
        }
        $genreUpdates = @()
        $reviewBroad = $topGenre
        if ($null -ne $topSubgenre) {
            $reviewBroad = @(
                $job.analysis.genreAnalysis.broadCandidates |
                    Where-Object { $_.id -eq $topSubgenre.parent }
            ) | Select-Object -First 1
        }
        if ($null -ne $reviewBroad) {
            $genreUpdates += @{ candidateId = $reviewBroad.id; accepted = $true }
        }
        if ($null -ne $topSubgenre) {
            $genreUpdates += @{ candidateId = $topSubgenre.id; accepted = $true }
        }
        if ($genreUpdates.Count -eq 0) {
            throw "Synthetic genre analysis returned no reviewable candidate."
        }
        $acceptedGenreIds = @($genreUpdates | ForEach-Object { $_.candidateId })
        $genrePatch = @{ updates = $genreUpdates } | ConvertTo-Json -Depth 5
        Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:8000/api/analyses/$jobId/genre" -ContentType "application/json" -Body $genrePatch | Out-Null

        $lyrics = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyses/$jobId/lyrics" -TimeoutSec 10
        $qualityCounts = @{
            accepted = 0
            uncertain = 0
            rejected_as_likely_hallucination = 0
            non_lexical = 0
        }
        foreach ($segment in @($lyrics.segments)) {
            $decision = [string]$segment.qualityDecision
            if (-not $qualityCounts.ContainsKey($decision)) {
                throw "Synthetic lyrics flow returned an unknown segment quality decision."
            }
            $qualityCounts[$decision] += 1
        }
        $usableSegments = @($lyrics.segments | Where-Object { $_.qualityDecision -in @("accepted", "uncertain") })
        if ($usableSegments.Count -eq 0) {
            throw "Synthetic speech produced no accepted or uncertain transcript segment."
        }
        foreach ($segment in $usableSegments) {
            if (@($segment.activeSectionIds).Count -eq 0) {
                throw "A usable synthetic transcript segment was not mapped to a structural section."
            }
        }
        $mappedSectionIds = @(
            $usableSegments |
                ForEach-Object { @($_.activeSectionIds) } |
                Sort-Object -Unique
        )
        $summarySectionIds = @($job.analysis.lyricsSummary.activeSectionIds | Sort-Object -Unique)
        if (
            [int]$job.analysis.lyricsSummary.segmentCount -ne $usableSegments.Count -or
            ($mappedSectionIds -join "|") -ne ($summarySectionIds -join "|")
        ) {
            throw "Synthetic lyrics summary is inconsistent with usable segment-to-section mapping."
        }
        Write-Host ("Lyrics quality decisions: {0}" -f ($qualityCounts | ConvertTo-Json -Compress))

        $approvedTheme = "verification and creative forward motion"
        $themePatch = @{ abstractThemes = @($approvedTheme) } | ConvertTo-Json -Depth 4
        Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:8000/api/analyses/$jobId/lyrics" -ContentType "application/json" -Body $themePatch | Out-Null
        $themeReload = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyses/$jobId" -TimeoutSec 30
        if (
            -not $themeReload.analysis.lyricsSummary.themesUserApproved -or
            @($themeReload.analysis.lyricsSummary.abstractThemes).Count -ne 1 -or
            $themeReload.analysis.lyricsSummary.abstractThemes[0] -ne $approvedTheme
        ) {
            throw "Synthetic abstract theme approval did not persist exactly in the authoritative analysis summary."
        }

        $modeCases = @(
            @{ mode = "reliable"; genre = "strict_top"; lyrics = "none"; count = 1; seed = 24680; creativity = 0.35; themes = $false; transformations = @() },
            @{ mode = "reliable"; genre = "blend"; lyrics = "prosody_only"; count = 1; seed = 24681; creativity = 0.35; themes = $false; transformations = @() },
            @{ mode = "creative"; genre = "user_selected_only"; lyrics = "abstract_themes"; count = 3; seed = 24682; creativity = 0.65; themes = $true; transformations = @("vary arrangement emphasis") },
            @{ mode = "experimental"; genre = "disabled"; lyrics = "user_written_direction"; count = 3; seed = 24683; creativity = 0.9; themes = $false; transformations = @("increase timbral surprise", "reimagine section contrast"); lyricalDirection = "Write wholly original words about patient forward motion." }
        )
        $creativeTemperature = $null
        $experimentalTemperature = $null
        foreach ($modeCase in $modeCases) {
            $promptRequest = @{
                promptEngineMode = $modeCase["mode"]
                genreInterpretationMode = $modeCase["genre"]
                lyricsInfluenceMode = $modeCase["lyrics"]
                candidateCount = $modeCase["count"]
                creativity = $modeCase["creativity"]
                variationSeed = $modeCase["seed"]
                outputLanguage = "English"
                generationIntent = "preserve_core_character"
                promptLength = "balanced"
                includeBpm = $true
                includeKey = $true
                instrumental = $false
                preserveEnergyArc = $true
                preserveInstrumentation = $true
                preserveStructure = $true
                preserveGroove = $true
                exclusions = @()
                disabledFeaturePaths = @()
                userOverrides = @{}
                lockSeed = $true
                lockedFeaturePaths = @()
                includeDetectedGenre = $true
                acceptedGenreIds = $acceptedGenreIds
                includeLyricalThemes = $modeCase["themes"]
                desiredTransformations = $modeCase["transformations"]
            }
            if ($modeCase.ContainsKey("lyricalDirection")) {
                $promptRequest["userWrittenLyricalDirection"] = $modeCase["lyricalDirection"]
            }
            $requestJson = $promptRequest | ConvertTo-Json -Depth 8
            $prompts = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/analyses/$jobId/prompt" -ContentType "application/json" -Body $requestJson -TimeoutSec 240
            $candidateCount = @($prompts.candidates).Count
            if ($prompts.engineMode -ne $modeCase["mode"] -or $candidateCount -ne $modeCase["count"]) {
                $safeValidationReasons = @($prompts.validationWarnings) -join "; "
                throw "$($modeCase["mode"]) prompt generation did not return the requested validated candidate set. Safe validation reasons: $safeValidationReasons"
            }
            if ($modeCase["mode"] -ne "reliable" -and $prompts.deterministicFallbackUsed) {
                throw "$($modeCase["mode"]) unexpectedly used the Reliable fallback during end-to-end verification."
            }
            if (
                $modeCase["lyrics"] -eq "abstract_themes" -and
                @($prompts.factsUsed) -notcontains "lyricsSummary.abstractThemes"
            ) {
                throw "Creative abstract-theme mode did not record approved theme evidence."
            }
            if ([string]::IsNullOrWhiteSpace([string]$prompts.selectedCandidateId)) {
                throw "$($modeCase["mode"]) did not persist an initial selected candidate."
            }
            Assert-NoPrivateTranscriptLeak -Value $prompts -Transcript $lyrics -Context "$($modeCase["mode"]) prompt package"
            if ($candidateCount -gt 1) {
                Assert-MaterialCandidateDiversity -Candidates @($prompts.candidates) -Mode ([string]$modeCase["mode"])
            }
            if ($modeCase["mode"] -ne "reliable") {
                foreach ($candidate in @($prompts.candidates)) {
                    if ([string]$candidate.modelId -ne [string]$capabilities.promptWriter.modelId) {
                        throw "$($modeCase["mode"]) candidate provenance did not identify the reviewed local model."
                    }
                }
            }
            else {
                $repeatPrompts = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/analyses/$jobId/prompt" -ContentType "application/json" -Body $requestJson -TimeoutSec 240
                if (
                    ($repeatPrompts | ConvertTo-Json -Depth 100 -Compress) -ne
                    ($prompts | ConvertTo-Json -Depth 100 -Compress)
                ) {
                    throw "Reliable mode was not fully deterministic for the same request and seed."
                }
            }

            $selectedCandidate = @($prompts.candidates)[-1]
            $selectionJson = @{ candidateId = $selectedCandidate.id } | ConvertTo-Json -Depth 3
            $selectedPackage = Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:8000/api/analyses/$jobId/prompt" -ContentType "application/json" -Body $selectionJson -TimeoutSec 30
            if ($selectedPackage.selectedCandidateId -ne $selectedCandidate.id -or $selectedPackage.primaryPrompt -ne $selectedCandidate.prompt) {
                throw "$($modeCase["mode"]) candidate selection did not persist consistently."
            }
            $reloaded = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyses/$jobId" -TimeoutSec 30
            $exported = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyses/$jobId/export.json" -TimeoutSec 30
            $markdown = (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/api/analyses/$jobId/export.md" -TimeoutSec 30).Content
            $selectedJson = $selectedPackage | ConvertTo-Json -Depth 100 -Compress
            $reloadedJson = $reloaded.promptPackage | ConvertTo-Json -Depth 100 -Compress
            $exportedJson = $exported.promptPackage | ConvertTo-Json -Depth 100 -Compress
            $selectedMarkerIndex = $markdown.IndexOf(" (selected)", [System.StringComparison]::Ordinal)
            $candidateMarker = "Candidate ID: ``$([string]$selectedCandidate.id)``"
            $candidateMarkerIndex = if ($selectedMarkerIndex -ge 0) {
                $markdown.IndexOf($candidateMarker, $selectedMarkerIndex, [System.StringComparison]::Ordinal)
            }
            else {
                -1
            }
            if (
                $selectedJson -ne $reloadedJson -or
                $selectedJson -ne $exportedJson -or
                $selectedMarkerIndex -lt 0 -or
                $candidateMarkerIndex -lt 0 -or
                $candidateMarkerIndex -gt ($selectedMarkerIndex + 600)
            ) {
                throw "$($modeCase["mode"]) prompt package did not survive reload and both exports."
            }
            Assert-NoPrivateTranscriptLeak -Value $reloaded.promptPackage -Transcript $lyrics -Context "$($modeCase["mode"]) reloaded package"
            Assert-NoPrivateTranscriptLeak -Value $exported.promptPackage -Transcript $lyrics -Context "$($modeCase["mode"]) JSON export"
            Assert-NoPrivateTranscriptLeak -Value $markdown -Transcript $lyrics -Context "$($modeCase["mode"]) Markdown export"
            if ($modeCase["mode"] -eq "creative") {
                $creativeTemperature = [double]$prompts.generationParameters.temperature
            }
            if ($modeCase["mode"] -eq "experimental") {
                $experimentalTemperature = [double]$prompts.generationParameters.temperature
            }
            Write-Host "$($modeCase["mode"]) / $($modeCase["genre"]) / $($modeCase["lyrics"]): $candidateCount validated candidate(s), selected candidate persisted." -ForegroundColor Green
        }
        if ($null -eq $creativeTemperature -or $null -eq $experimentalTemperature -or $experimentalTemperature -le $creativeTemperature) {
            throw "Experimental sampling was not stronger than Creative sampling."
        }
        Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:8000/api/analyses/$jobId" | Out-Null
        $deletedStatus = 200
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyses/$jobId" -TimeoutSec 10 | Out-Null
        }
        catch {
            if ($null -eq $_.Exception.Response) {
                throw
            }
            $deletedStatus = [int]$_.Exception.Response.StatusCode
        }
        if ($deletedStatus -ne 404) {
            throw "Synthetic job remained readable after deletion."
        }
        $jobId = $null
        Write-Host "Synthetic Deep + genre + lyrics + all prompt modes passed." -ForegroundColor Green
    }

    Write-Host "Full-GPU verification passed." -ForegroundColor Green
}
catch {
    Write-Host "Full-GPU verification failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    if ($null -ne $jobId) {
        try {
            Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:8000/api/analyses/$jobId" -TimeoutSec 10 | Out-Null
            $cleanupStatus = 200
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/analyses/$jobId" -TimeoutSec 10 | Out-Null
            }
            catch {
                if ($null -ne $_.Exception.Response) {
                    $cleanupStatus = [int]$_.Exception.Response.StatusCode
                }
                else {
                    throw
                }
            }
            if ($cleanupStatus -ne 404) {
                Write-Host "The failed synthetic job was deleted but remained readable; normal TTL cleanup may still be required." -ForegroundColor Yellow
            }
        }
        catch {
            Write-Host "The failed synthetic job still requires normal TTL cleanup." -ForegroundColor Yellow
        }
    }
    elseif ($uploadAcceptedWithoutJobId) {
        Write-Host "The upload was accepted without a usable job ID; its job can only be reclaimed by the configured TTL cleanup." -ForegroundColor Yellow
    }
    if ($null -ne $fixture) {
        Remove-Item -LiteralPath $fixture -Force -ErrorAction SilentlyContinue
    }
}
