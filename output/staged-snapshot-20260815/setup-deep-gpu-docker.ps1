#requires -Version 5.1
<#
.SYNOPSIS
    Builds and launches TrackPrompt Studio Deep mode with NVIDIA GPU support.

.DESCRIPTION
    This script performs the complete Windows + Docker setup used by
    TrackPrompt Studio's Deep mode:

      1. Validates Docker, Docker Compose, and the host NVIDIA GPU.
      2. Regenerates backend/Dockerfile.deep from backend/Dockerfile.
      3. Installs Demucs without forcing a CPU-only PyTorch wheel.
      4. Creates a GPU-enabled compose.deep.yaml.
      5. Downloads or reuses the reviewed htdemucs repository files.
      6. Creates the required SHA-256 allowlist manifest.
      7. Builds the backend image.
      8. Verifies Demucs, PyTorch, CUDA, and the GPU inside Docker.
      9. Copies the model repository into /data/models.
     10. Corrects private permissions and ownership for the trackprompt user.
     11. Starts the application and checks Deep-mode readiness.

    Place this script in the TrackPrompt Studio repository root.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass `
      -File .\setup-deep-gpu-docker.ps1 `
      -AcceptModelTerms

.EXAMPLE
    .\setup-deep-gpu-docker.ps1 `
      -AcceptModelTerms `
      -Device cuda `
      -ForceDownload

.EXAMPLE
    .\setup-deep-gpu-docker.ps1 `
      -AcceptModelTerms `
      -Device cpu

.NOTES
    Default device: cuda

    Use -Device auto to let the application choose CUDA when available and
    otherwise fall back according to its own device resolver.

    The script verifies that the container can see CUDA. TrackPrompt Studio must
    also read DEMUCS_DEVICE and pass the resolved device into Demucs. The script
    warns if the capability API does not expose an effective device.
#>

[CmdletBinding()]
param(
    [switch]$AcceptModelTerms,
    [switch]$ForceDownload,
    [switch]$NoStart,
    [switch]$NoBrowser,

    [ValidateSet("cuda", "auto", "cpu")]
    [string]$Device = "cuda",

    [string]$DemucsVersion = "4.1.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ModelName = "htdemucs"
$ModelSignature = "955717e8"
$CheckpointFileName = "955717e8-8726e21a.th"
$YamlFileName = "htdemucs.yaml"

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

function Invoke-CapturedChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Write-Host "> $Executable $($ArgumentList -join ' ')" -ForegroundColor DarkGray

    $captured = @(& $Executable @ArgumentList 2>&1)
    $exitCode = $LASTEXITCODE

    foreach ($line in $captured) {
        Write-Host ([string]$line)
    }

    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode."
    }

    return @($captured | ForEach-Object { [string]$_ })
}

function Download-File {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination,
        [long]$MinimumBytes = 1
    )

    $partialPath = "$Destination.partial"
    Remove-Item -LiteralPath $partialPath -Force -ErrorAction SilentlyContinue

    $lastError = $null

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Write-Host "Downloading $(Split-Path -Leaf $Destination) (attempt $attempt/3)..."

            Invoke-WebRequest `
                -UseBasicParsing `
                -Uri $Uri `
                -OutFile $partialPath `
                -Headers @{ "User-Agent" = "TrackPrompt-Studio-Deep-GPU-Setup/2.0" }

            $length = (Get-Item -LiteralPath $partialPath).Length

            if ($length -lt $MinimumBytes) {
                throw "Downloaded file is unexpectedly small: $length bytes."
            }

            Move-Item `
                -LiteralPath $partialPath `
                -Destination $Destination `
                -Force

            Write-Success "Downloaded $(Split-Path -Leaf $Destination) ($length bytes)"
            return
        }
        catch {
            $lastError = $_
            Remove-Item -LiteralPath $partialPath -Force -ErrorAction SilentlyContinue

            if ($attempt -lt 3) {
                Start-Sleep -Seconds 2
            }
        }
    }

    throw "Could not download $Uri. Last error: $($lastError.Exception.Message)"
}

function New-DeepDockerfile {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDockerfile,
        [Parameter(Mandatory = $true)][string]$DeepDockerfile,
        [Parameter(Mandatory = $true)][string]$Version
    )

    [string[]]$sourceLines = [System.IO.File]::ReadAllLines($SourceDockerfile)

    if ($sourceLines.Count -eq 0) {
        throw "backend/Dockerfile is empty."
    }

    $lastFromIndex = -1

    for ($i = 0; $i -lt $sourceLines.Count; $i++) {
        if ($sourceLines[$i] -match '^\s*FROM\b') {
            $lastFromIndex = $i
        }
    }

    if ($lastFromIndex -lt 0) {
        throw "backend/Dockerfile does not contain a FROM instruction."
    }

    $insertIndex = $sourceLines.Count

    for ($i = $lastFromIndex + 1; $i -lt $sourceLines.Count; $i++) {
        if ($sourceLines[$i] -match '^\s*(USER|CMD|ENTRYPOINT)\b') {
            $insertIndex = $i
            break
        }
    }

    $demucsInstruction = 'RUN python -m pip install --no-cache-dir "demucs==' + $Version + '"'

    $managedLines = @(
        "",
        "# TrackPrompt Deep dependency (generated by setup-deep-gpu-docker.ps1)",
        "# Do not force a CPU-only Torch wheel; CUDA capability is verified after build.",
        $demucsInstruction,
        ""
    )

    $output = New-Object System.Collections.Generic.List[string]

    for ($i = 0; $i -lt $sourceLines.Count; $i++) {
        if ($i -eq $insertIndex) {
            foreach ($managedLine in $managedLines) {
                [void]$output.Add($managedLine)
            }
        }

        [void]$output.Add($sourceLines[$i])
    }

    if ($insertIndex -eq $sourceLines.Count) {
        foreach ($managedLine in $managedLines) {
            [void]$output.Add($managedLine)
        }
    }

    $newContent = ($output -join [Environment]::NewLine) + [Environment]::NewLine

    if (Test-Path -LiteralPath $DeepDockerfile -PathType Leaf) {
        $oldContent = [System.IO.File]::ReadAllText($DeepDockerfile)

        if ($oldContent -eq $newContent) {
            Write-Success "backend/Dockerfile.deep is already current"
            return
        }

        Backup-File -Path $DeepDockerfile
    }

    Write-Utf8File -Path $DeepDockerfile -Content $newContent
    Write-Success "Regenerated backend/Dockerfile.deep with Demucs $Version"
}

function New-ComposeOverride {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$RequestedDevice
    )

    $lines = New-Object System.Collections.Generic.List[string]

    [void]$lines.Add("# Generated by setup-deep-gpu-docker.ps1")
    [void]$lines.Add("services:")
    [void]$lines.Add("  backend:")
    [void]$lines.Add("    build:")
    [void]$lines.Add("      context: ./backend")
    [void]$lines.Add("      dockerfile: Dockerfile.deep")

    if ($RequestedDevice -ne "cpu") {
        [void]$lines.Add("    gpus: all")
    }

    [void]$lines.Add("    environment:")
    [void]$lines.Add('      ENABLE_DEMUCS: "true"')
    [void]$lines.Add("      DEMUCS_MODEL_NAME: `"$ModelName`"")
    [void]$lines.Add("      DEMUCS_DEVICE: `"$RequestedDevice`"")
    [void]$lines.Add('      MODEL_CACHE_DIR: "/data/models"')

    $newContent = ($lines -join [Environment]::NewLine) + [Environment]::NewLine

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $oldContent = [System.IO.File]::ReadAllText($Path)

        if ($oldContent -eq $newContent) {
            Write-Success "compose.deep.yaml is already current"
            return
        }

        Backup-File -Path $Path
    }

    Write-Utf8File -Path $Path -Content $newContent
    Write-Success "Created GPU-aware compose.deep.yaml with DEMUCS_DEVICE=$RequestedDevice"
}

function New-ModelManifest {
    param(
        [Parameter(Mandatory = $true)][string]$ModelsDirectory,
        [Parameter(Mandatory = $true)][string]$ManifestPath
    )

    $files = [ordered]@{}

    Get-ChildItem -LiteralPath $ModelsDirectory -File -Recurse |
        Where-Object {
            $_.FullName -ne $ManifestPath -and
            $_.Extension -ne ".partial"
        } |
        Sort-Object FullName |
        ForEach-Object {
            $relative = $_.FullName.Substring($ModelsDirectory.Length + 1).Replace("\", "/")
            $hash = (
                Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
            ).Hash.ToLowerInvariant()

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

function Collect-DeepSignals {
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
        $Node.GetType().IsArray) {

        $index = 0

        foreach ($item in $Node) {
            Collect-DeepSignals -Node $item -Path "$Path[$index]"
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

        $deepRelated = $childPath -match '(?i)(deep|demucs)'
        $interestingName = $property.Name -match '(?i)^(available|ready|enabled|status|requestedDevice|effectiveDevice|cudaAvailable|gpuName)$'

        if ($deepRelated -and $interestingName) {
            $script:DeepSignals += [pscustomobject]@{
                Path = $childPath
                Value = $property.Value
            }
        }

        if ($null -ne $property.Value -and
            -not ($property.Value -is [string]) -and
            -not ($property.Value -is [ValueType])) {

            Collect-DeepSignals -Node $property.Value -Path $childPath
        }
    }
}

try {
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

    Write-Step "Validating repository, Docker, and host GPU"

    if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
        throw "compose.yaml was not found in $RepoRoot. Place this script in the TrackPrompt Studio repository root."
    }

    if (-not (Test-Path -LiteralPath $BackendDockerfile -PathType Leaf)) {
        throw "backend/Dockerfile was not found in $RepoRoot."
    }

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker was not found on PATH. Start Docker Desktop and open a new PowerShell window."
    }

    Invoke-Checked `
        -Executable "docker" `
        -ArgumentList @("--version") `
        -Description "Docker version check"

    Invoke-Checked `
        -Executable "docker" `
        -ArgumentList @("compose", "version") `
        -Description "Docker Compose version check"

    Invoke-Checked `
        -Executable "docker" `
        -ArgumentList @("info", "--format", "{{.ServerVersion}}") `
        -Description "Docker Engine check"

    if ($Device -ne "cpu") {
        if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
            throw "nvidia-smi was not found. Install or repair the NVIDIA driver, or rerun with -Device cpu."
        }

        Invoke-Checked `
            -Executable "nvidia-smi" `
            -ArgumentList @(
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader"
            ) `
            -Description "Host NVIDIA GPU check"
    }

    if (-not $AcceptModelTerms) {
        throw @"
Model use was not acknowledged.

Review:
  - the exact checkpoint source;
  - checkpoint and weight license;
  - training-data terms;
  - commercial-use suitability;
  - your right to use the model and analyze the audio.

Then rerun:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-deep-gpu-docker.ps1 -AcceptModelTerms
"@
    }

    Write-Step "Generating Deep Docker and Compose configuration"

    New-DeepDockerfile `
        -SourceDockerfile $BackendDockerfile `
        -DeepDockerfile $DeepDockerfile `
        -Version $DemucsVersion

    New-ComposeOverride `
        -Path $ComposeOverride `
        -RequestedDevice $Device

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

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $ModelsDirectory |
        Out-Null

    Get-ChildItem `
        -LiteralPath $ModelsDirectory `
        -Filter "*.partial" `
        -File `
        -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue

    if ($ForceDownload -or -not (Test-Path -LiteralPath $YamlPath -PathType Leaf)) {
        Download-File `
            -Uri $YamlUrl `
            -Destination $YamlPath `
            -MinimumBytes 5
    }
    else {
        Write-Success "$YamlFileName already exists; use -ForceDownload to replace it"
    }

    if ($ForceDownload -or -not (Test-Path -LiteralPath $CheckpointPath -PathType Leaf)) {
        Download-File `
            -Uri $CheckpointUrl `
            -Destination $CheckpointPath `
            -MinimumBytes 1000000
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

    New-ModelManifest `
        -ModelsDirectory $ModelsDirectory `
        -ManifestPath $ManifestPath

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

    Write-Step "Verifying Demucs, PyTorch, CUDA, and GPU access inside Docker"

    # Keep this as one line and avoid quoted Python labels. This prevents
    # Windows PowerShell 5.1 from stripping nested quotes in native arguments.
    $verifyPython = 'import demucs,torch;print(demucs.__file__);print(demucs.__version__);print(torch.__version__);print(torch.version.cuda);print(torch.cuda.is_available());print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)'

    $verificationOutput = Invoke-CapturedChecked `
        -Executable "docker" `
        -ArgumentList ($composePrefix + @(
            "run", "--rm", "--no-deps",
            "backend",
            "python", "-c", $verifyPython
        )) `
        -Description "Demucs and CUDA verification"

    $cudaAvailable = $false

    foreach ($line in $verificationOutput) {
        if ($line.Trim() -eq "True") {
            $cudaAvailable = $true
            break
        }
    }

    if ($Device -eq "cuda" -and -not $cudaAvailable) {
        throw @"
DEMUCS_DEVICE=cuda was requested, but PyTorch cannot access CUDA inside the backend container.

The host GPU may work while the backend service still lacks GPU allocation or
a CUDA-capable Torch build. Confirm:
  docker compose -f compose.yaml -f compose.deep.yaml config
  docker compose -f compose.yaml -f compose.deep.yaml run --rm --no-deps backend python -c "import torch;print(torch.cuda.is_available())"
"@
    }

    if ($Device -eq "auto" -and -not $cudaAvailable) {
        Write-WarningMessage "CUDA is unavailable inside the backend image. Auto mode may fall back to CPU."
    }

    if ($cudaAvailable) {
        Write-Success "PyTorch can access the NVIDIA GPU inside Docker"
    }

    Write-Step "Stopping the backend before replacing /data/models"

    & docker @($composePrefix + @("stop", "backend")) | Out-Host
    $global:LASTEXITCODE = 0

    Write-Step "Copying the checksum-verified model repository into the Docker volume"

    $mountArgument = "${ModelsDirectory}:/seed:ro"

    $seedCommand = @'
set -eu
rm -rf /data/models
mkdir -p /data/models
cp -a /seed/. /data/models/
chown -R trackprompt:trackprompt /data/models
find /data/models -type d -exec chmod 700 {} \;
find /data/models -type f -exec chmod 600 {} \;
test -r /data/models/demucs-models.json
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

    $verifyModelAccessCommand = @'
set -eu
test -r /data/models/demucs-models.json
test -r /data/models/htdemucs.yaml
test -r /data/models/955717e8-8726e21a.th
find /data/models -maxdepth 2 -type f -print
'@

    Invoke-Checked `
        -Executable "docker" `
        -ArgumentList ($composePrefix + @(
            "run", "--rm", "--no-deps",
            "backend",
            "sh", "-lc", $verifyModelAccessCommand
        )) `
        -Description "Non-root model access verification"

    if ($NoStart) {
        Write-Success "Deep GPU image and model volume are prepared"

        Write-Host ""
        Write-Host "Start later with:" -ForegroundColor Cyan
        Write-Host "docker compose -f compose.yaml -f compose.deep.yaml up --build -d"
        exit 0
    }

    Write-Step "Starting TrackPrompt Studio"

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

    $script:DeepSignals = @()
    Collect-DeepSignals -Node $capabilities

    if ($script:DeepSignals.Count -gt 0) {
        Write-Host ""
        Write-Host "Detected Deep/GPU capability signals:" -ForegroundColor Cyan
        $script:DeepSignals | Format-Table -AutoSize | Out-Host
    }

    $explicitDeepFailure = $script:DeepSignals |
        Where-Object {
            ($_.Path -match '(?i)(available|ready|enabled)$' -and $_.Value -eq $false) -or
            ([string]$_.Value -match '(?i)^(unavailable|disabled|failed|error)$')
        } |
        Select-Object -First 1

    if ($null -ne $explicitDeepFailure) {
        throw "The capability API reports Deep mode unavailable: $($explicitDeepFailure.Path) = $($explicitDeepFailure.Value)"
    }

    $positiveDeepSignal = $script:DeepSignals |
        Where-Object {
            $_.Value -eq $true -or
            ([string]$_.Value -match '(?i)^(ready|available|enabled|cuda)$')
        } |
        Select-Object -First 1

    if ($null -ne $positiveDeepSignal) {
        Write-Success "Deep readiness signal confirmed: $($positiveDeepSignal.Path) = $($positiveDeepSignal.Value)"
    }
    else {
        Write-WarningMessage "The API did not expose a recognizable positive Deep readiness field. Review the capability JSON above."
    }

    if ($Device -eq "cuda" -and $capabilityJson -notmatch '(?i)"effectiveDevice"\s*:\s*"cuda"') {
        Write-WarningMessage "Docker CUDA verification passed, but /api/capabilities did not explicitly report effectiveDevice=cuda. Confirm that backend/app reads DEMUCS_DEVICE and passes it to Demucs."
    }

    Write-Step "Final CUDA check inside the running backend"

    $runningVerification = Invoke-CapturedChecked `
        -Executable "docker" `
        -ArgumentList ($composePrefix + @(
            "exec", "-T",
            "backend",
            "python", "-c", $verifyPython
        )) `
        -Description "Running-backend CUDA verification"

    if ($Device -eq "cuda") {
        $runningCudaAvailable = $false

        foreach ($line in $runningVerification) {
            if ($line.Trim() -eq "True") {
                $runningCudaAvailable = $true
                break
            }
        }

        if (-not $runningCudaAvailable) {
            throw "The running backend lost CUDA access even though the build-time service test passed."
        }
    }

    Write-Success "TrackPrompt Studio is available at http://localhost:5173"

    if (-not $NoBrowser) {
        Start-Process "http://localhost:5173"
    }

    Write-Host ""
    Write-Host "Useful commands:" -ForegroundColor Cyan
    Write-Host "  docker compose -f compose.yaml -f compose.deep.yaml logs -f backend"
    Write-Host "  docker compose -f compose.yaml -f compose.deep.yaml ps"
    Write-Host "  docker compose -f compose.yaml -f compose.deep.yaml down"
    Write-Host "  docker compose -f compose.yaml -f compose.deep.yaml down --volumes"
    Write-Host "  nvidia-smi -l 1"
}
catch {
    Write-Host ""
    Write-Host "Deep GPU setup failed:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "For backend diagnostics, run:" -ForegroundColor Yellow
    Write-Host "docker compose -f compose.yaml -f compose.deep.yaml logs --tail 200 backend"
    exit 1
}
