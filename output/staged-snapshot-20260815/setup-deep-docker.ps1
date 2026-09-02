#requires -Version 5.1
<#
.SYNOPSIS
    Enables TrackPrompt Studio Deep mode in Docker on Windows.

.DESCRIPTION
    This script:
      1. Validates the repository and Docker installation.
      2. Creates/patches backend/Dockerfile.deep.
      3. Creates compose.deep.yaml.
      4. Explicitly downloads the reviewed htdemucs repository files.
      5. Generates the required SHA-256 allowlist manifest.
      6. Builds the custom backend image.
      7. Copies the model repository into the Compose /data volume.
      8. Starts TrackPrompt Studio.
      9. Checks /api/capabilities for Deep/Demucs readiness.

    Run this script from the TrackPrompt Studio repository root, or place the
    script in that root and launch it from any directory.

    The model download is explicit. You must pass -AcceptModelTerms after you
    have reviewed the checkpoint source, license, training-data terms, and
    suitability for your use.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-deep-docker.ps1 -AcceptModelTerms

.EXAMPLE
    .\setup-deep-docker.ps1 -AcceptModelTerms -ForceDownload -NoBrowser

.EXAMPLE
    .\setup-deep-docker.ps1 -AcceptModelTerms -NoStart

.NOTES
    This convenience profile does not configure NVIDIA Container Toolkit or
    promise GPU access. The installed PyTorch wheel may include CUDA build
    support, so the API reports build support, runtime availability, GPU name,
    and selected device separately. CPU remains the verified fallback; CUDA is
    selected only in a separately reviewed GPU-capable image/runtime.
#>

[CmdletBinding()]
param(
    [switch]$AcceptModelTerms,
    [switch]$ForceDownload,
    [switch]$NoStart,
    [switch]$NoBrowser,
    [string]$DemucsVersion = "4.0.1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ModelName = "htdemucs"
$ModelSignature = "955717e8"
$CheckpointFileName = "955717e8-8726e21a.th"
$YamlFileName = "htdemucs.yaml"

# These are explicit upstream locations used by the Demucs htdemucs repository.
$YamlUrl = "https://raw.githubusercontent.com/facebookresearch/demucs/main/demucs/remote/htdemucs.yaml"
$CheckpointUrl = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/$CheckpointFileName"

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "OK: $Message" -ForegroundColor Green
}

function Write-WarningMessage {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "WARNING: $Message" -ForegroundColor Yellow
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Write-Host "> $Executable $($ArgumentList -join ' ')" -ForegroundColor DarkGray
    & $Executable @ArgumentList
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode."
    }
}

function Write-Utf8File {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    [System.IO.File]::WriteAllText($Path, $Content, $Utf8NoBom)
}

function Backup-File {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backupPath = "$Path.bak-$stamp"
    Copy-Item -LiteralPath $Path -Destination $backupPath -Force
    Write-Host "Backup: $backupPath" -ForegroundColor DarkGray
}

function Download-File {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination,
        [long]$MinimumBytes = 1
    )

    $partial = "$Destination.partial"
    Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue

    $lastError = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Write-Host "Downloading $(Split-Path -Leaf $Destination) (attempt $attempt/3)..."
            Invoke-WebRequest `
                -UseBasicParsing `
                -Uri $Uri `
                -OutFile $partial `
                -Headers @{ "User-Agent" = "TrackPrompt-Studio-Deep-Setup/1.0" }

            $length = (Get-Item -LiteralPath $partial).Length
            if ($length -lt $MinimumBytes) {
                throw "Downloaded file is unexpectedly small: $length bytes."
            }

            Move-Item -LiteralPath $partial -Destination $Destination -Force
            Write-Success "Downloaded $(Split-Path -Leaf $Destination) ($length bytes)"
            return
        }
        catch {
            $lastError = $_
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
            if ($attempt -lt 3) {
                Start-Sleep -Seconds 2
            }
        }
    }

    throw "Could not download $Uri. Last error: $($lastError.Exception.Message)"
}

function Ensure-DeepDockerfile {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDockerfile,
        [Parameter(Mandatory = $true)][string]$DeepDockerfile,
        [Parameter(Mandatory = $true)][string]$Version
    )

    if (-not (Test-Path -LiteralPath $DeepDockerfile -PathType Leaf)) {
        Copy-Item -LiteralPath $SourceDockerfile -Destination $DeepDockerfile
        Write-Success "Created backend/Dockerfile.deep"
    }

    $existingText = [System.IO.File]::ReadAllText($DeepDockerfile)
    if ($existingText -match '(?im)^\s*.*pip\s+install.*demucs') {
        Write-Success "Dockerfile.deep already contains a Demucs installation instruction"
        return
    }

    Backup-File -Path $DeepDockerfile

    [string[]]$lines = [System.IO.File]::ReadAllLines($DeepDockerfile)
    $lastFromIndex = -1

    for ($i = 0; $i -lt $lines.Length; $i++) {
        if ($lines[$i] -match '^\s*FROM\b') {
            $lastFromIndex = $i
        }
    }

    if ($lastFromIndex -lt 0) {
        throw "backend/Dockerfile.deep does not contain a FROM instruction."
    }

    $insertIndex = $lines.Length
    for ($i = $lastFromIndex + 1; $i -lt $lines.Length; $i++) {
        if ($lines[$i] -match '^\s*(USER|CMD|ENTRYPOINT)\b') {
            $insertIndex = $i
            break
        }
    }

    $managedLines = @(
        "",
        "# TrackPrompt Deep dependencies (managed by setup-deep-docker.ps1)",
        "# CPU-only PyTorch is intentional for this convenience setup; GPU images are separate.",
        "RUN python -m pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \",
        "    && python -m pip install --no-cache-dir `"demucs==$Version`"",
        ""
    )

    $output = New-Object System.Collections.Generic.List[string]

    for ($i = 0; $i -lt $lines.Length; $i++) {
        if ($i -eq $insertIndex) {
            foreach ($managedLine in $managedLines) {
                [void]$output.Add($managedLine)
            }
        }
        [void]$output.Add($lines[$i])
    }

    if ($insertIndex -eq $lines.Length) {
        foreach ($managedLine in $managedLines) {
            [void]$output.Add($managedLine)
        }
    }

    Write-Utf8File -Path $DeepDockerfile -Content (($output -join [Environment]::NewLine) + [Environment]::NewLine)
    Write-Success "Added CPU-only PyTorch, torchaudio, and Demucs $Version to Dockerfile.deep"
}

function Ensure-ComposeOverride {
    param([Parameter(Mandatory = $true)][string]$Path)

    $generatedMarker = "# Generated by setup-deep-docker.ps1"
    if ((Test-Path -LiteralPath $Path -PathType Leaf)) {
        $existing = [System.IO.File]::ReadAllText($Path)
        if ($existing -notmatch [regex]::Escape($generatedMarker)) {
            Backup-File -Path $Path
        }
    }

    $content = @"
$generatedMarker
services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile.deep
    environment:
      ENABLE_DEMUCS: "true"
      DEMUCS_MODEL_NAME: "$ModelName"
      DEMUCS_DEVICE: "cpu"
      MODEL_CACHE_DIR: "/data/models"
"@

    Write-Utf8File -Path $Path -Content ($content.Trim() + [Environment]::NewLine)
    Write-Success "Created compose.deep.yaml"
}

function New-ModelManifest {
    param(
        [Parameter(Mandatory = $true)][string]$ModelsDirectory,
        [Parameter(Mandatory = $true)][string]$ManifestPath
    )

    $files = [ordered]@{}

    Get-ChildItem -LiteralPath $ModelsDirectory -File -Recurse |
        Where-Object { $_.FullName -ne $ManifestPath } |
        Sort-Object FullName |
        ForEach-Object {
            $relative = $_.FullName.Substring($ModelsDirectory.Length + 1).Replace("\", "/")
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            $files[$relative] = $hash
        }

    if ($files.Count -eq 0) {
        throw "No model repository files were found in $ModelsDirectory."
    }

    $manifest = [ordered]@{
        models = [ordered]@{
            $ModelName = [ordered]@{
                files = $files
            }
        }
    }

    $json = $manifest | ConvertTo-Json -Depth 20
    Write-Utf8File -Path $ManifestPath -Content ($json + [Environment]::NewLine)
    Write-Success "Generated SHA-256 manifest for $($files.Count) model file(s)"
}

function Collect-DeepReadinessSignals {
    param(
        [Parameter(Mandatory = $false)]$Node,
        [string]$Path = ""
    )

    if ($null -eq $Node) {
        return
    }

    if ($Node -is [string] -or $Node -is [ValueType]) {
        return
    }

    if ($Node -is [System.Collections.IEnumerable] -and
        -not ($Node -is [System.Collections.IDictionary]) -and
        -not ($Node.PSObject.Properties.Name -contains "Count")) {
        $index = 0
        foreach ($item in $Node) {
            Collect-DeepReadinessSignals -Node $item -Path "$Path[$index]"
            $index++
        }
        return
    }

    foreach ($property in $Node.PSObject.Properties) {
        $childPath = if ([string]::IsNullOrWhiteSpace($Path)) {
            $property.Name
        }
        else {
            "$Path.$($property.Name)"
        }

        $isDeepPath = $childPath -match '(?i)(deep|demucs)'
        $isSignalName = $property.Name -match '(?i)^(available|ready|enabled|status)$'

        if ($isDeepPath -and $isSignalName) {
            $script:DeepReadinessSignals += [pscustomobject]@{
                Path = $childPath
                Value = $property.Value
            }
        }

        if ($null -ne $property.Value -and
            -not ($property.Value -is [string]) -and
            -not ($property.Value -is [ValueType])) {
            Collect-DeepReadinessSignals -Node $property.Value -Path $childPath
        }
    }
}

try {
    # Resolve the repository root from the script location.
    $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
        $RepoRoot = (Get-Location).Path
    }
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
    Set-Location -LiteralPath $RepoRoot

    $ComposeFile = Join-Path $RepoRoot "compose.yaml"
    $ComposeOverride = Join-Path $RepoRoot "compose.deep.yaml"
    $BackendDockerfile = Join-Path $RepoRoot "backend\Dockerfile"
    $DeepDockerfile = Join-Path $RepoRoot "backend\Dockerfile.deep"
    $ModelsDirectory = Join-Path $RepoRoot "deep-models"
    $ManifestPath = Join-Path $ModelsDirectory "demucs-models.json"
    $YamlPath = Join-Path $ModelsDirectory $YamlFileName
    $CheckpointPath = Join-Path $ModelsDirectory $CheckpointFileName

    Write-Step "Validating repository and Docker"

    if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
        throw "compose.yaml was not found in $RepoRoot. Place this script in the TrackPrompt Studio repository root."
    }

    if (-not (Test-Path -LiteralPath $BackendDockerfile -PathType Leaf)) {
        throw "backend/Dockerfile was not found in $RepoRoot."
    }

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker was not found on PATH. Start Docker Desktop and open a new PowerShell window."
    }

    Invoke-Checked -Executable "docker" -ArgumentList @("--version") -Description "Docker version check"
    Invoke-Checked -Executable "docker" -ArgumentList @("compose", "version") -Description "Docker Compose version check"
    Invoke-Checked -Executable "docker" -ArgumentList @("info", "--format", "{{.ServerVersion}}") -Description "Docker Engine check"

    if (-not $AcceptModelTerms) {
        throw @"
Model use was not acknowledged.

Review:
  - the exact checkpoint source;
  - checkpoint/weight license;
  - training-data terms;
  - commercial-use suitability;
  - your right to use the model and analyze the audio.

Then rerun:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-deep-docker.ps1 -AcceptModelTerms
"@
    }

    Write-Step "Creating the Deep Docker configuration"
    Ensure-DeepDockerfile `
        -SourceDockerfile $BackendDockerfile `
        -DeepDockerfile $DeepDockerfile `
        -Version $DemucsVersion

    Ensure-ComposeOverride -Path $ComposeOverride

    $composePrefix = @(
        "compose",
        "-f", $ComposeFile,
        "-f", $ComposeOverride
    )

    Invoke-Checked `
        -Executable "docker" `
        -ArgumentList ($composePrefix + @("config", "--quiet")) `
        -Description "Merged Compose validation"

    Write-Step "Preparing the local htdemucs model repository"
    New-Item -ItemType Directory -Force -Path $ModelsDirectory | Out-Null
    Get-ChildItem -LiteralPath $ModelsDirectory -Filter "*.partial" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue

    if ($ForceDownload -or -not (Test-Path -LiteralPath $YamlPath -PathType Leaf)) {
        Download-File -Uri $YamlUrl -Destination $YamlPath -MinimumBytes 5
    }
    else {
        Write-Success "$YamlFileName already exists; use -ForceDownload to replace it"
    }

    if ($ForceDownload -or -not (Test-Path -LiteralPath $CheckpointPath -PathType Leaf)) {
        Download-File -Uri $CheckpointUrl -Destination $CheckpointPath -MinimumBytes 1000000
    }
    else {
        Write-Success "$CheckpointFileName already exists; use -ForceDownload to replace it"
    }

    $yamlText = [System.IO.File]::ReadAllText($YamlPath)
    if ($yamlText -notmatch [regex]::Escape($ModelSignature)) {
        throw "$YamlFileName does not reference expected model signature $ModelSignature."
    }

    $checkpointLength = (Get-Item -LiteralPath $CheckpointPath).Length
    if ($checkpointLength -lt 1000000) {
        throw "$CheckpointFileName is too small to be a valid checkpoint ($checkpointLength bytes)."
    }

    New-ModelManifest -ModelsDirectory $ModelsDirectory -ManifestPath $ManifestPath

    Write-Host ""
    Get-ChildItem -LiteralPath $ModelsDirectory -File |
        Sort-Object Name |
        Select-Object Name, Length, LastWriteTime |
        Format-Table -AutoSize

    Write-Step "Building the custom Deep backend image"
    Invoke-Checked `
        -Executable "docker" `
        -ArgumentList ($composePrefix + @("build", "backend")) `
        -Description "Deep backend image build"

    Write-Step "Verifying Demucs inside the custom image"
    $verifyPython = 'import demucs,torch;print(demucs.__file__);print(demucs.__version__);print(torch.__version__);print(torch.cuda.is_available())'
    Invoke-Checked `
        -Executable "docker" `
        -ArgumentList ($composePrefix + @(
            "run", "--rm", "--no-deps",
            "backend",
            "python", "-c", $verifyPython
        )) `
        -Description "Demucs import verification"

    Write-Step "Stopping the backend before replacing /data/models"
    & docker @($composePrefix + @("stop", "backend")) | Out-Host
    # A missing/stopped service is harmless here.
    $global:LASTEXITCODE = 0

    Write-Step "Copying the checksum-verified model repository into the Docker volume"
    $mountArgument = "${ModelsDirectory}:/seed:ro"
    $seedCommand = @'
set -eu
rm -rf /data/models
mkdir -p /data/models
cp -a /seed/. /data/models/
chmod 700 /data/models
find /data/models -type d -exec chmod 700 {} \;
find /data/models -type f -exec chmod 600 {} \;
echo "Seeded model files:"
find /data/models -maxdepth 5 -type f -print
'@

    Invoke-Checked `
        -Executable "docker" `
        -ArgumentList ($composePrefix + @(
            "run", "--rm", "--no-deps",
            "--user", "root",
            "-v", $mountArgument,
            "backend",
            "sh", "-lc", $seedCommand
        )) `
        -Description "Model-volume seeding"

    if ($NoStart) {
        Write-Success "Deep image and model volume are prepared. Services were not started because -NoStart was supplied."
        Write-Host ""
        Write-Host "Start later with:" -ForegroundColor Cyan
        Write-Host "docker compose -f compose.yaml -f compose.deep.yaml up --build -d"
        exit 0
    }

    Write-Step "Starting TrackPrompt Studio with Deep mode enabled"
    Invoke-Checked `
        -Executable "docker" `
        -ArgumentList ($composePrefix + @("up", "--build", "-d")) `
        -Description "Compose startup"

    Invoke-Checked `
        -Executable "docker" `
        -ArgumentList ($composePrefix + @("ps")) `
        -Description "Compose status"

    Write-Step "Waiting for the local capability endpoint"
    $capabilities = $null
    $lastApiError = $null

    for ($attempt = 1; $attempt -le 60; $attempt++) {
        try {
            $capabilities = Invoke-RestMethod `
                -Method Get `
                -Uri "http://127.0.0.1:8000/api/capabilities" `
                -TimeoutSec 5
            break
        }
        catch {
            $lastApiError = $_
            Start-Sleep -Seconds 2
        }
    }

    if ($null -eq $capabilities) {
        throw "The backend did not expose /api/capabilities. Last error: $($lastApiError.Exception.Message)"
    }

    $capabilityJson = $capabilities | ConvertTo-Json -Depth 30
    Write-Host $capabilityJson

    $script:DeepReadinessSignals = @()
    Collect-DeepReadinessSignals -Node $capabilities

    $readySignal = $script:DeepReadinessSignals |
        Where-Object {
            $_.Value -eq $true -or
            ([string]$_.Value -match '(?i)^(ready|available|enabled)$')
        } |
        Select-Object -First 1

    if ($null -eq $readySignal) {
        Write-WarningMessage "The services started, but no positive Deep/Demucs readiness signal was found."
        Write-Host ""
        Write-Host "Readiness signals discovered:" -ForegroundColor Yellow
        if ($script:DeepReadinessSignals.Count -gt 0) {
            $script:DeepReadinessSignals | Format-Table -AutoSize | Out-Host
        }
        else {
            Write-Host "(none)"
        }

        Write-Host ""
        Write-Host "Backend logs:" -ForegroundColor Cyan
        Write-Host "docker compose -f compose.yaml -f compose.deep.yaml logs --tail 200 backend"
        exit 2
    }

    Write-Success "Deep/Demucs readiness confirmed at $($readySignal.Path) = $($readySignal.Value)"
    Write-Success "TrackPrompt Studio is available at http://localhost:5173"

    if (-not $NoBrowser) {
        Start-Process "http://localhost:5173"
    }

    Write-Host ""
    Write-Host "Useful commands:" -ForegroundColor Cyan
    Write-Host "  docker compose -f compose.yaml -f compose.deep.yaml logs -f backend"
    Write-Host "  docker compose -f compose.yaml -f compose.deep.yaml down"
    Write-Host "  docker compose -f compose.yaml -f compose.deep.yaml down --volumes"
}
catch {
    Write-Host ""
    Write-Host "Deep-mode setup failed:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "For backend diagnostics, run:" -ForegroundColor Yellow
    Write-Host "docker compose -f compose.yaml -f compose.deep.yaml logs --tail 200 backend"
    exit 1
}
