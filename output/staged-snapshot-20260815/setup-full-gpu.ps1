#requires -Version 5.1
<#
.SYNOPSIS
    Installs, diagnoses, and starts the reviewed TrackPrompt Studio full-GPU profile.

.DESCRIPTION
    This is the canonical Windows full-GPU setup and recovery entry point. It
    preserves Docker volumes and model caches, never edits application source,
    and invokes repository diagnostics with ``python -m`` instead of inline
    Python or shell programs.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-full-gpu.ps1 `
      -AcceptAllReviewedModelTerms

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-full-gpu.ps1 `
      -AcceptAllReviewedModelTerms -SkipBuild -SkipModelInstall
#>
[CmdletBinding()]
param(
    [switch]$AcceptAllReviewedModelTerms,
    [switch]$SkipBuild,
    [switch]$SkipModelInstall,
    [switch]$ForceDownload,
    [switch]$SkipGenre,
    [switch]$SkipLyrics,
    [switch]$SkipPromptWriter,
    [switch]$NoStart,
    [switch]$NoBrowser,
    [ValidateSet("cuda", "cpu")]
    [string]$Device = "cuda"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
Set-Location -LiteralPath $RepoRoot
$script:ComposeArguments = @("compose", "-f", "compose.yaml", "-f", "compose.full-gpu.yaml")

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host $Message -ForegroundColor Green
}

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

function Invoke-NativeCaptured {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $lines = & $Executable @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = (($lines | Out-String).Trim())
    }
}

function Invoke-ComposeChecked {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )
    Invoke-NativeChecked -Executable "docker" -Arguments ($script:ComposeArguments + $Arguments) -Description $Description
}

function Invoke-ComposeAllowFailure {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker @($script:ComposeArguments + $Arguments)
    return $LASTEXITCODE
}

function Get-ServiceRuntimeState {
    param([Parameter(Mandatory = $true)][string]$Service)
    $container = Invoke-NativeCaptured -Executable "docker" -Arguments ($script:ComposeArguments + @("ps", "--all", "-q", $Service))
    if ($container.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($container.Output)) {
        return "missing"
    }
    $containerId = ($container.Output -split "\s+" | Select-Object -Last 1)
    $format = "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}"
    $inspection = Invoke-NativeCaptured -Executable "docker" -Arguments @("inspect", "--format", $format, $containerId)
    if ($inspection.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($inspection.Output)) {
        return "missing"
    }
    return $inspection.Output.Trim().ToLowerInvariant()
}

function Wait-ServiceReady {
    param(
        [Parameter(Mandatory = $true)][string]$Service,
        [int]$Attempts = 90
    )
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $state = Get-ServiceRuntimeState -Service $Service
        if ($state -in @("healthy", "running")) {
            return $true
        }
        if ($state -in @("dead", "exited", "unhealthy")) {
            return $false
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Run-BackendDiagnostic {
    param(
        [Parameter(Mandatory = $true)][string]$Module,
        [Parameter(Mandatory = $true)][string]$Description,
        [string[]]$ExtraArguments = @()
    )
    $arguments = @("run", "--rm", "--no-deps", "backend", "python", "-m", $Module) + $ExtraArguments
    Invoke-ComposeChecked -Arguments $arguments -Description $Description
}

function Wait-HttpJson {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$Attempts = 60
    )
    $lastFailure = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            return Invoke-RestMethod -Method Get -Uri $Uri -TimeoutSec 5
        }
        catch {
            $lastFailure = $_
            Start-Sleep -Seconds 2
        }
    }
    throw "HTTP verification failed for $Uri. $($lastFailure.Exception.Message)"
}

function Show-BackendFailureDiagnostics {
    Write-Host ""
    Write-Host "Compose service state:" -ForegroundColor Yellow
    Invoke-ComposeAllowFailure -Arguments @("ps", "--all") | Out-Null

    Write-Host ""
    Write-Host "Backend logs:" -ForegroundColor Yellow
    Invoke-ComposeAllowFailure -Arguments @("logs", "--no-color", "--tail", "400", "backend") | Out-Null

    $container = Invoke-NativeCaptured -Executable "docker" -Arguments ($script:ComposeArguments + @("ps", "--all", "-q", "backend"))
    if ($container.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($container.Output)) {
        $containerId = ($container.Output -split "\s+" | Select-Object -Last 1)
        Write-Host ""
        Write-Host "Backend container state:" -ForegroundColor Yellow
        & docker inspect --format "{{json .State}}" $containerId
    }

    Write-Host ""
    Write-Host "Fresh-process import diagnostic:" -ForegroundColor Yellow
    Invoke-ComposeAllowFailure -Arguments @(
        "run", "--rm", "--no-deps", "backend",
        "python", "-m", "app.diagnostics.imports"
    ) | Out-Null
}

try {
    Write-Step "Validating prerequisites and reviewed terms"
    if ($ForceDownload -and $SkipModelInstall) {
        throw "-ForceDownload cannot be combined with -SkipModelInstall."
    }
    if (-not $SkipModelInstall -and -not $AcceptAllReviewedModelTerms) {
        throw "Review docs/model-licenses.md, then pass -AcceptAllReviewedModelTerms or reuse installed caches with -SkipModelInstall."
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker was not found on PATH."
    }
    if ($Device -eq "cuda" -and -not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        throw "nvidia-smi was not found on PATH."
    }
    Invoke-NativeChecked -Executable "docker" -Arguments @("version", "--format", "{{.Server.Version}}") -Description "Docker Engine validation"
    Invoke-NativeChecked -Executable "docker" -Arguments @("compose", "version") -Description "Docker Compose validation"
    if ($Device -eq "cuda") {
        Invoke-NativeChecked -Executable "nvidia-smi" -Arguments @("--query-gpu=name,driver_version,memory.total", "--format=csv,noheader") -Description "NVIDIA validation"
    }

    $env:DEMUCS_DEVICE = $Device
    $env:GENRE_DEVICE = $Device
    $env:LYRICS_DEVICE = $Device
    $env:PROMPT_WRITER_DEVICE = $Device
    $env:ENABLE_GENRE_TAGGER = if ($SkipGenre) { "false" } else { "true" }
    $env:ENABLE_LYRICS_ADAPTER = if ($SkipLyrics) { "false" } else { "true" }
    $env:ENABLE_LOCAL_PROMPT_WRITER = if ($SkipPromptWriter) { "false" } else { "true" }

    Write-Step "Validating the rendered full-GPU Compose profile"
    Invoke-ComposeChecked -Arguments @("config", "--quiet") -Description "Full-GPU Compose validation"

    if (-not $SkipBuild) {
        Write-Step "Building backend and frontend with Docker cache"
        Invoke-ComposeChecked -Arguments @("build", "backend", "frontend") -Description "Full-GPU image build"
    }
    else {
        Write-Step "Reusing existing images because -SkipBuild was supplied"
    }

    Write-Step "Running GPU, import, and dependency diagnostics"
    Run-BackendDiagnostic -Module "app.diagnostics.gpu" -Description "CUDA and CTranslate2 diagnostic"
    Run-BackendDiagnostic -Module "app.diagnostics.imports" -Description "Fresh-process import diagnostic"
    Invoke-ComposeChecked -Arguments @("run", "--rm", "--no-deps", "backend", "python", "-m", "pip", "check") -Description "Full-GPU dependency integrity check"
    Run-BackendDiagnostic -Module "app.diagnostics.dependencies" -Description "Reviewed direct dependency version diagnostic"

    if (-not $SkipModelInstall) {
        Write-Step "Provisioning reviewed model caches without deleting Docker volumes"
        $demucsSeed = Join-Path $RepoRoot "deep-models"
        if (-not (Test-Path -LiteralPath (Join-Path $demucsSeed "demucs-models.json") -PathType Leaf)) {
            throw "The ignored local deep-models seed is missing its checksum manifest."
        }
        $resolvedSeed = (Resolve-Path -LiteralPath $demucsSeed).Path
        $provisionArguments = @(
            "run", "--rm", "--no-deps", "--user", "root",
            "-v", "${resolvedSeed}:/seed:ro",
            "backend", "python", "-m", "app.diagnostics.provision_demucs",
            "--source", "/seed"
        )
        if ($ForceDownload) {
            $provisionArguments += "--force"
        }
        Invoke-ComposeChecked -Arguments $provisionArguments -Description "Verified Demucs model provisioning"

        if (-not $SkipGenre) {
            $genreArguments = @(
                "run", "--rm", "--no-deps",
                "-e", "HF_HUB_OFFLINE=0", "-e", "TRANSFORMERS_OFFLINE=0",
                "backend", "python", "-m", "app.diagnostics.install_model", "genre"
            )
            if ($ForceDownload) {
                $genreArguments += "--force"
            }
            Invoke-ComposeChecked -Arguments $genreArguments -Description "Explicit genre-model installation"
        }
        if (-not $SkipLyrics) {
            $lyricsArguments = @(
                "run", "--rm", "--no-deps",
                "-e", "HF_HUB_OFFLINE=0", "-e", "TRANSFORMERS_OFFLINE=0",
                "backend", "python", "-m", "app.diagnostics.install_model", "lyrics"
            )
            if ($ForceDownload) {
                $lyricsArguments += "--force"
            }
            Invoke-ComposeChecked -Arguments $lyricsArguments -Description "Explicit lyrics-model installation"
        }
    }
    else {
        Write-Step "Reusing all existing model volumes because -SkipModelInstall was supplied"
    }

    Write-Step "Running Demucs, genre, and lyrics diagnostics"
    Run-BackendDiagnostic -Module "app.diagnostics.demucs" -Description "Demucs tiny inference diagnostic" -ExtraArguments @("--smoke")
    if (-not $SkipGenre) {
        Run-BackendDiagnostic -Module "app.diagnostics.genre" -Description "Genre tiny inference diagnostic" -ExtraArguments @("--smoke")
    }
    if (-not $SkipLyrics) {
        Run-BackendDiagnostic -Module "app.diagnostics.lyrics" -Description "Lyrics tiny inference diagnostic" -ExtraArguments @("--smoke")
    }

    Write-Step "Starting and verifying the private prompt writer"
    $promptWriterStateBefore = Get-ServiceRuntimeState -Service "prompt-writer"
    Invoke-ComposeChecked -Arguments @("up", "-d", "prompt-writer") -Description "Prompt-writer startup"
    if (-not (Wait-ServiceReady -Service "prompt-writer" -Attempts 60)) {
        Invoke-ComposeAllowFailure -Arguments @("logs", "--no-color", "--tail", "300", "prompt-writer") | Out-Null
        throw "The prompt-writer service did not become healthy."
    }

    if (-not $SkipPromptWriter -and -not $SkipModelInstall) {
        $promptWriterModel = if ($env:LOCAL_LLM_MODEL) { $env:LOCAL_LLM_MODEL } else { "qwen2.5:7b-instruct-q4_K_M" }
        $installedModels = Invoke-NativeCaptured -Executable "docker" -Arguments ($script:ComposeArguments + @("exec", "-T", "prompt-writer", "ollama", "list"))
        if ($installedModels.ExitCode -ne 0) {
            throw "The prompt-writer model inventory could not be read."
        }
        $modelPresent = $installedModels.Output.Contains($promptWriterModel)
        if ($ForceDownload -or -not $modelPresent) {
            Invoke-ComposeChecked -Arguments @("exec", "-T", "prompt-writer", "ollama", "pull", $promptWriterModel) -Description "Explicit prompt-writer model installation"
        }
        else {
            Write-Success "The reviewed prompt-writer model is already installed; its volume was reused."
        }
    }

    if (-not $SkipPromptWriter) {
        Run-BackendDiagnostic -Module "app.diagnostics.prompt_writer" -Description "Prompt-writer tiny inference diagnostic" -ExtraArguments @("--smoke")
    }
    Run-BackendDiagnostic -Module "app.diagnostics.capabilities" -Description "Combined capability diagnostic"

    if ($NoStart) {
        if ($promptWriterStateBefore -notin @("healthy", "running")) {
            Invoke-ComposeChecked -Arguments @("stop", "prompt-writer") -Description "Temporary prompt-writer stop"
        }
        Write-Success "Full-GPU diagnostics passed; application startup was skipped."
        Write-Host "Launch later with: docker compose -f compose.yaml -f compose.full-gpu.yaml up -d"
        exit 0
    }

    Write-Step "Starting the backend"
    Invoke-ComposeChecked -Arguments @("up", "-d", "--no-deps", "backend") -Description "Backend startup"
    if (-not (Wait-ServiceReady -Service "backend" -Attempts 90)) {
        Show-BackendFailureDiagnostics
        throw "The backend failed to become healthy."
    }

    Write-Step "Starting the frontend"
    Invoke-ComposeChecked -Arguments @("up", "-d", "--no-deps", "frontend") -Description "Frontend startup"
    if (-not (Wait-ServiceReady -Service "frontend" -Attempts 60)) {
        Invoke-ComposeAllowFailure -Arguments @("logs", "--no-color", "--tail", "300", "frontend") | Out-Null
        throw "The frontend failed to become ready."
    }

    Write-Step "Checking API health and independent capabilities"
    $health = Wait-HttpJson -Uri "http://127.0.0.1:8000/api/health"
    $capabilities = Wait-HttpJson -Uri "http://127.0.0.1:8000/api/capabilities"
    if ($health.status -ne "ok") {
        throw "The backend health endpoint returned '$($health.status)'."
    }
    if (-not $capabilities.fastMode.available -or -not $capabilities.deepMode.available) {
        throw "Fast or Deep mode is unavailable in the full-GPU profile."
    }
    if (-not $SkipGenre -and -not $capabilities.genreTagger.available) {
        throw "The genre tagger is unavailable after its diagnostic passed."
    }
    if (-not $SkipLyrics -and -not $capabilities.lyricsAdapter.available) {
        throw "The lyrics adapter is unavailable after its diagnostic passed."
    }
    if (-not $SkipPromptWriter -and (
        -not $capabilities.promptWriter.reliableAvailable -or
        -not $capabilities.promptWriter.creativeAvailable -or
        -not $capabilities.promptWriter.experimentalAvailable
    )) {
        throw "Reliable, Creative, and Experimental prompt modes are not all available."
    }

    Write-Host ($health | ConvertTo-Json -Depth 30)
    Write-Host ($capabilities | ConvertTo-Json -Depth 50)

    Write-Step "Final service status"
    Invoke-ComposeChecked -Arguments @("ps") -Description "Final Compose status"
    Write-Success "TrackPrompt Studio is available at http://localhost:5173"
    if (-not $NoBrowser) {
        Start-Process "http://localhost:5173"
    }
}
catch {
    Write-Host ""
    Write-Host "Full-GPU setup failed: $($_.Exception.Message)" -ForegroundColor Red
    try {
        Show-BackendFailureDiagnostics
    }
    catch {
        Write-Host "Additional failure diagnostics could not be collected." -ForegroundColor Yellow
    }
    exit 1
}
