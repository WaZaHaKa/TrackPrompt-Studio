[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Download')]
    [string]$Mode = 'Plan',

    [ValidateSet('Q5_K_M', 'Q4_K_M', 'TI2V-5B')]
    [string]$Tier = 'Q5_K_M',

    [Parameter(Mandatory = $true)]
    [string]$ComfyUIRoot,

    [switch]$AcceptModelLicenses,

    [switch]$InstallManagedComfyUI
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ResolvedRoot = [System.IO.Path]::GetFullPath($ComfyUIRoot)
$DriveRoot = [System.IO.Path]::GetPathRoot($ResolvedRoot)
if ($ResolvedRoot -eq $DriveRoot -or $ResolvedRoot.Length -lt ($DriveRoot.Length + 4)) {
    throw 'ComfyUIRoot must be a specific ComfyUI installation directory, not a drive root.'
}

$PinnedWanRevision = 'e4c692ecd295e54ffead84a0f2f3c89b55107020'
$PinnedGgufRevision = 'c95ab6c210a60ff915aa3f7cb0fa07300b0b2f36'
$PinnedFluxRevision = '44ea96fbcead75dfa908449883350ada44601791'
$PinnedComfyUIRelease = 'v0.30.0'

function New-ModelEntry {
    param(
        [string]$Name,
        [string]$Source,
        [string]$Revision,
        [string]$License,
        [double]$ExpectedGiB,
        [string]$RelativeDestination,
        [string]$Purpose
    )
    [pscustomobject]@{
        Name = $Name
        Source = $Source
        Revision = $Revision
        License = $License
        ExpectedGiB = $ExpectedGiB
        Destination = Join-Path $ResolvedRoot $RelativeDestination
        Purpose = $Purpose
    }
}

$WanVaeEntry = if ($Tier -eq 'TI2V-5B') {
    New-ModelEntry -Name 'wan2.2_vae.safetensors' `
        -Source "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/$PinnedWanRevision/split_files/vae/wan2.2_vae.safetensors" `
        -Revision $PinnedWanRevision -License 'Apache-2.0 upstream model family; see host model card' `
        -ExpectedGiB 1.4 -RelativeDestination 'models\vae\wan2.2_vae.safetensors' `
        -Purpose 'Wan2.2 TI2V-5B VAE'
} else {
    New-ModelEntry -Name 'wan_2.1_vae.safetensors' `
        -Source "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/$PinnedWanRevision/split_files/vae/wan_2.1_vae.safetensors" `
        -Revision $PinnedWanRevision -License 'Apache-2.0 upstream model family; see host model card' `
        -ExpectedGiB 0.25 -RelativeDestination 'models\vae\wan_2.1_vae.safetensors' `
        -Purpose 'Wan2.2 A14B compatible VAE'
}

$Shared = @(
    New-ModelEntry -Name 'umt5_xxl_fp8_e4m3fn_scaled.safetensors' `
        -Source "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/$PinnedWanRevision/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" `
        -Revision $PinnedWanRevision -License 'Apache-2.0 upstream model family; see host model card' `
        -ExpectedGiB 6.7 -RelativeDestination 'models\text_encoders\umt5_xxl_fp8_e4m3fn_scaled.safetensors' `
        -Purpose 'Wan text encoder'
    $WanVaeEntry
    New-ModelEntry -Name 'flux1-schnell-fp8.safetensors' `
        -Source "https://huggingface.co/Comfy-Org/flux1-schnell/resolve/$PinnedFluxRevision/flux1-schnell-fp8.safetensors" `
        -Revision $PinnedFluxRevision -License 'Apache-2.0' `
        -ExpectedGiB 12.0 -RelativeDestination 'models\checkpoints\flux1-schnell-fp8.safetensors' `
        -Purpose 'Reference sheets and keyframes'
)

$TierEntries = switch ($Tier) {
    'Q5_K_M' {
        @(
            New-ModelEntry -Name 'wan2.2_i2v_high_noise_14B_Q5_K_M.gguf' `
                -Source "https://huggingface.co/bullerwins/Wan2.2-I2V-A14B-GGUF/resolve/$PinnedGgufRevision/wan2.2_i2v_high_noise_14B_Q5_K_M.gguf" `
                -Revision $PinnedGgufRevision -License 'Apache-2.0; community quantization of Wan2.2' `
                -ExpectedGiB 10.8 -RelativeDestination 'models\unet\wan2.2_i2v_high_noise_14B_Q5_K_M.gguf' `
                -Purpose 'Wan2.2 I2V high-noise expert'
            New-ModelEntry -Name 'wan2.2_i2v_low_noise_14B_Q5_K_M.gguf' `
                -Source "https://huggingface.co/bullerwins/Wan2.2-I2V-A14B-GGUF/resolve/$PinnedGgufRevision/wan2.2_i2v_low_noise_14B_Q5_K_M.gguf" `
                -Revision $PinnedGgufRevision -License 'Apache-2.0; community quantization of Wan2.2' `
                -ExpectedGiB 10.8 -RelativeDestination 'models\unet\wan2.2_i2v_low_noise_14B_Q5_K_M.gguf' `
                -Purpose 'Wan2.2 I2V low-noise expert'
        )
    }
    'Q4_K_M' {
        @(
            New-ModelEntry -Name 'wan2.2_i2v_high_noise_14B_Q4_K_M.gguf' `
                -Source "https://huggingface.co/bullerwins/Wan2.2-I2V-A14B-GGUF/resolve/$PinnedGgufRevision/wan2.2_i2v_high_noise_14B_Q4_K_M.gguf" `
                -Revision $PinnedGgufRevision -License 'Apache-2.0; community quantization of Wan2.2' `
                -ExpectedGiB 9.65 -RelativeDestination 'models\unet\wan2.2_i2v_high_noise_14B_Q4_K_M.gguf' `
                -Purpose 'Wan2.2 I2V high-noise expert'
            New-ModelEntry -Name 'wan2.2_i2v_low_noise_14B_Q4_K_M.gguf' `
                -Source "https://huggingface.co/bullerwins/Wan2.2-I2V-A14B-GGUF/resolve/$PinnedGgufRevision/wan2.2_i2v_low_noise_14B_Q4_K_M.gguf" `
                -Revision $PinnedGgufRevision -License 'Apache-2.0; community quantization of Wan2.2' `
                -ExpectedGiB 9.65 -RelativeDestination 'models\unet\wan2.2_i2v_low_noise_14B_Q4_K_M.gguf' `
                -Purpose 'Wan2.2 I2V low-noise expert'
        )
    }
    'TI2V-5B' {
        @(
            New-ModelEntry -Name 'wan2.2_ti2v_5B_fp16.safetensors' `
                -Source "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/$PinnedWanRevision/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" `
                -Revision $PinnedWanRevision -License 'Apache-2.0' `
                -ExpectedGiB 10.0 -RelativeDestination 'models\diffusion_models\wan2.2_ti2v_5B_fp16.safetensors' `
                -Purpose 'Official native reliable fallback'
        )
    }
}

$Entries = @($Shared + $TierEntries)
$ExpectedDownloadGiB = [Math]::Round(($Entries | Measure-Object -Property ExpectedGiB -Sum).Sum, 2)
$Drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($ResolvedRoot).Substring(0, 1))
$FreeGiB = [Math]::Round($Drive.Free / 1GB, 2)
$RequiredFreeGiB = [Math]::Ceiling($ExpectedDownloadGiB + 60)

Write-Host 'TrackPrompt local ComfyUI setup plan'
Write-Host "Mode: $Mode"
Write-Host "Tier: $Tier"
Write-Host "ComfyUI root: $ResolvedRoot"
Write-Host "ComfyUI mode: $(if ($InstallManagedComfyUI) { "managed install pinned to $PinnedComfyUIRelease" } else { 'existing external install' })"
Write-Host "Expected selected-tier download: $ExpectedDownloadGiB GiB (host values are approximate)"
Write-Host "Free disk now: $FreeGiB GiB"
Write-Host "Recommended free disk before download: $RequiredFreeGiB GiB"
Write-Host ''
$Entries | Select-Object Name, Purpose, License, ExpectedGiB, Revision, Source, Destination | Format-List

Write-Warning 'ComfyUI itself and ComfyUI-GGUF are not silently installed. Use an existing current ComfyUI install and explicitly install City96/ComfyUI-GGUF at a reviewed revision for GGUF tiers.'
Write-Warning 'RIFE and Real-ESRGAN are separate explicit post-production dependencies; configure TRACKPROMPT_RIFE_PATH and TRACKPROMPT_REALESRGAN_PATH after reviewing their release licenses.'

if ($Mode -eq 'Plan') {
    Write-Host 'Plan only. No network request or file write was made.'
    exit 0
}

if (-not $AcceptModelLicenses) {
    throw 'Download mode requires -AcceptModelLicenses after reviewing every source and license printed above.'
}
if ($FreeGiB -lt $RequiredFreeGiB) {
    throw "Insufficient free disk for the selected tier and 60 GiB working reserve. Required: $RequiredFreeGiB GiB; available: $FreeGiB GiB."
}
$ComfyMain = Join-Path $ResolvedRoot 'main.py'
if (-not (Test-Path -LiteralPath $ComfyMain -PathType Leaf)) {
    if (-not $InstallManagedComfyUI) {
        throw 'No ComfyUI install was found at ComfyUIRoot. Rerun with -InstallManagedComfyUI or select an existing reviewed install.'
    }
    if (Test-Path -LiteralPath $ResolvedRoot) {
        $ExistingItems = @(Get-ChildItem -LiteralPath $ResolvedRoot -Force)
        if ($ExistingItems.Count -gt 0) {
            throw 'Managed installation refuses to use a non-empty directory that is not already ComfyUI.'
        }
    }
    $Git = (Get-Command git.exe -ErrorAction Stop).Source
    Write-Host "Installing managed ComfyUI from official release $PinnedComfyUIRelease"
    & $Git clone '--branch' $PinnedComfyUIRelease '--depth' '1' 'https://github.com/Comfy-Org/ComfyUI.git' $ResolvedRoot
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $ComfyMain -PathType Leaf)) {
        throw 'Managed ComfyUI clone failed.'
    }
    $ManagedPython = Join-Path $ResolvedRoot '.venv\Scripts\python.exe'
    & py.exe '-3.12' '-m' 'venv' (Join-Path $ResolvedRoot '.venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $ManagedPython -PathType Leaf)) {
        throw 'Managed ComfyUI Python environment creation failed.'
    }
    & $ManagedPython '-m' 'pip' 'install' '--disable-pip-version-check' '-r' (Join-Path $ResolvedRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        throw 'Managed ComfyUI dependency installation failed.'
    }
}
$GitRevision = $null
if (Test-Path -LiteralPath (Join-Path $ResolvedRoot '.git')) {
    $GitRevision = (& git.exe '-C' $ResolvedRoot 'rev-parse' 'HEAD').Trim()
}
$Curl = (Get-Command curl.exe -ErrorAction Stop).Source
$Records = @()
foreach ($Entry in $Entries) {
    $Destination = [System.IO.Path]::GetFullPath([string]$Entry.Destination)
    if (-not $Destination.StartsWith($ResolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'A model destination escaped the configured ComfyUI root.'
    }
    $Directory = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
        Write-Host "Keeping existing model: $($Entry.Name) sha256=$Hash"
    } else {
        $Partial = "$Destination.partial"
        Write-Host "Downloading resumably: $($Entry.Name)"
        & $Curl '--fail' '--location' '--retry' '3' '--continue-at' '-' '--output' $Partial ([string]$Entry.Source)
        if ($LASTEXITCODE -ne 0) {
            throw "Download failed for $($Entry.Name). The partial file was preserved for resume."
        }
        $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Partial).Hash.ToLowerInvariant()
        Move-Item -LiteralPath $Partial -Destination $Destination
    }
    $Records += [pscustomobject]@{
        name = $Entry.Name
        source = $Entry.Source
        revision = $Entry.Revision
        license = $Entry.License
        destinationRelativeToComfyUI = $Destination.Substring($ResolvedRoot.TrimEnd('\\').Length).TrimStart('\\')
        sha256 = $Hash
        installedAt = [DateTimeOffset]::UtcNow.ToString('o')
    }
}

$Lock = [pscustomobject]@{
    schemaVersion = '1.0.0'
    providerId = 'local-comfyui'
    selectedTier = $Tier
    comfyUiRoot = $ResolvedRoot
    comfyUiRelease = $PinnedComfyUIRelease
    comfyUiRevision = $GitRevision
    managedInstall = [bool]$InstallManagedComfyUI
    recordedAt = [DateTimeOffset]::UtcNow.ToString('o')
    files = $Records
}
$LockPath = Join-Path $ResolvedRoot 'trackprompt-local-comfyui-install-lock.json'
$TemporaryLock = "$LockPath.partial"
$Lock | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $TemporaryLock -Encoding UTF8
Move-Item -LiteralPath $TemporaryLock -Destination $LockPath -Force
Write-Host "Setup record written: $LockPath"
Write-Host 'No existing model was deleted or overwritten.'
