#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$AcceptDemucsTerms,
    [switch]$AcceptGenreModelTerms,
    [switch]$AcceptLyricsModelTerms,
    [switch]$AcceptLlmModelTerms,
    [switch]$AcceptAllReviewedModelTerms,
    [switch]$ForceDownload,
    [switch]$NoStart,
    [switch]$NoBrowser,
    [ValidateSet("cuda", "auto", "cpu")][string]$Device = "cuda",
    [switch]$SkipGenre,
    [switch]$SkipLyrics,
    [switch]$SkipLocalLlm
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments, [string]$Description)
    Write-Host "> $Executable $($Arguments -join ' ')" -ForegroundColor DarkGray
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Description failed with exit code $LASTEXITCODE." }
}

function Require-Terms {
    param([bool]$Accepted, [string]$Name, [string]$SwitchName)
    if (-not $Accepted) {
        throw "Review docs/model-licenses.md and the exact $Name terms, then pass -$SwitchName or -AcceptAllReviewedModelTerms."
    }
}

$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
Set-Location -LiteralPath $RepoRoot
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "compose.yaml") -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $RepoRoot "compose.full-gpu.yaml") -PathType Leaf)) {
    throw "Run this script from the TrackPrompt Studio repository root."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker was not found on PATH." }
if ($Device -ne "cpu" -and -not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { throw "nvidia-smi was not found." }

$all = [bool]$AcceptAllReviewedModelTerms
Require-Terms ($all -or $AcceptDemucsTerms) "Demucs htdemucs checkpoint" "AcceptDemucsTerms"
if (-not $SkipGenre) { Require-Terms ($all -or $AcceptGenreModelTerms) "CLAP genre model" "AcceptGenreModelTerms" }
if (-not $SkipLyrics) { Require-Terms ($all -or $AcceptLyricsModelTerms) "faster-whisper-small model" "AcceptLyricsModelTerms" }
if (-not $SkipLocalLlm) { Require-Terms ($all -or $AcceptLlmModelTerms) "Qwen2.5 7B Q4_K_M model" "AcceptLlmModelTerms" }

Invoke-Checked "docker" @("version", "--format", "{{.Server.Version}}") "Docker Engine validation"
Invoke-Checked "docker" @("compose", "version") "Docker Compose validation"
if ($Device -ne "cpu") {
    Invoke-Checked "nvidia-smi" @("--query-gpu=name,driver_version,memory.total", "--format=csv,noheader") "NVIDIA host validation"
}

$env:DEMUCS_DEVICE = $Device
$env:GENRE_DEVICE = $Device
$env:LYRICS_DEVICE = $Device
$env:PROMPT_WRITER_DEVICE = $Device
$env:ENABLE_GENRE_TAGGER = if ($SkipGenre) { "false" } else { "true" }
$env:ENABLE_LYRICS_ADAPTER = if ($SkipLyrics) { "false" } else { "true" }
$env:ENABLE_LOCAL_PROMPT_WRITER = if ($SkipLocalLlm) { "false" } else { "true" }
$compose = @("compose", "-f", "compose.yaml", "-f", "compose.full-gpu.yaml")

Invoke-Checked "docker" ($compose + @("config", "--quiet")) "Full GPU Compose validation"
Invoke-Checked "docker" ($compose + @("build", "backend")) "Full GPU backend build"
Invoke-Checked "docker" ($compose + @("run", "--rm", "--no-deps", "backend", "python", "-m", "app.diagnostics.gpu")) "CUDA and CTranslate2 container validation"

$demucsSeed = (Resolve-Path -LiteralPath (Join-Path $RepoRoot "deep-models")).Path
$mount = "${demucsSeed}:/seed:ro"
$seedCommand = "set -eu; rm -rf /data/models/demucs; mkdir -p /data/models/demucs; cp -a /seed/. /data/models/demucs/; chown -R trackprompt:trackprompt /data/models/demucs; find /data/models/demucs -type d -exec chmod 700 {} ; ; find /data/models/demucs -type f -exec chmod 600 {} ;"
Invoke-Checked "docker" ($compose + @("run", "--rm", "--no-deps", "--user", "root", "-v", $mount, "backend", "sh", "-lc", $seedCommand)) "Demucs model-volume provisioning"

if (-not $SkipGenre) {
    $arguments = $compose + @("run", "--rm", "--no-deps", "-e", "HF_HUB_OFFLINE=0", "-e", "TRANSFORMERS_OFFLINE=0", "backend", "python", "-m", "app.diagnostics.install_model", "genre")
    if ($ForceDownload) { $arguments += "--force" }
    Invoke-Checked "docker" $arguments "Explicit CLAP model installation"
}
if (-not $SkipLyrics) {
    $arguments = $compose + @("run", "--rm", "--no-deps", "-e", "HF_HUB_OFFLINE=0", "-e", "TRANSFORMERS_OFFLINE=0", "backend", "python", "-m", "app.diagnostics.install_model", "lyrics")
    if ($ForceDownload) { $arguments += "--force" }
    Invoke-Checked "docker" $arguments "Explicit faster-whisper model installation"
}
if (-not $SkipLocalLlm) {
    Invoke-Checked "docker" ($compose + @("up", "-d", "prompt-writer")) "Private Ollama startup"
    Invoke-Checked "docker" ($compose + @("exec", "-T", "prompt-writer", "ollama", "pull", "qwen2.5:7b-instruct-q4_K_M")) "Explicit Qwen model pull"
}

if ($NoStart) {
    Invoke-Checked "docker" ($compose + @("stop", "prompt-writer")) "Temporary prompt-writer stop"
    Write-Host "Full GPU images and model volumes are installed."
    Write-Host "Start with: docker compose -f compose.yaml -f compose.full-gpu.yaml up --build -d"
    exit 0
}

Invoke-Checked "docker" ($compose + @("up", "--build", "-d")) "Full GPU stack startup"
Invoke-Checked "powershell" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ".\verify-full-gpu.ps1") "Full GPU inference verification"
Write-Host "TrackPrompt Studio is ready at http://localhost:5173" -ForegroundColor Green
if (-not $NoBrowser) { Start-Process "http://localhost:5173" }
