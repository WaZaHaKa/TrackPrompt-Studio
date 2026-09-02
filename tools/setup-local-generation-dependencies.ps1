[CmdletBinding()]
param(
    [string]$ComfyUIRoot = 'D:\TrackPrompt-ComfyUI',
    [string]$ToolsRoot = 'D:\TrackPrompt-Tools'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Pinned = [ordered]@{
    pytorch = [ordered]@{
        index = 'https://download.pytorch.org/whl/cu128'
        torch = '2.11.0+cu128'
        torchvision = '0.26.0+cu128'
        torchaudio = '2.11.0+cu128'
    }
    comfyUiGguf = [ordered]@{
        repository = 'https://github.com/city96/ComfyUI-GGUF.git'
        revision = '6ea2651e7df66d7585f6ffee804b20e92fb38b8a'
        license = 'Apache-2.0'
    }
    rife = [ordered]@{
        repository = 'https://github.com/nihui/rife-ncnn-vulkan'
        release = '20221029'
        revision = 'a7532fc3f9f8f008cd6eecd6f2ffe2a9698e0cf7'
        license = 'MIT'
        asset = 'rife-ncnn-vulkan-20221029-windows.zip'
        url = 'https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-windows.zip'
    }
    realesrgan = [ordered]@{
        repository = 'https://github.com/xinntao/Real-ESRGAN'
        release = 'v0.2.2.4'
        revision = 'f83472d0113b8af82b5c5dcaa6e5a9dc88e466a7'
        license = 'MIT'
        asset = 'realesrgan-ncnn-vulkan-20210901-windows.zip'
        url = 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/realesrgan-ncnn-vulkan-20210901-windows.zip'
    }
}

function Resolve-ContainedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Child
    )
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $resolvedChild = [System.IO.Path]::GetFullPath((Join-Path $resolvedRoot $Child))
    if (-not $resolvedChild.StartsWith("$resolvedRoot\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Managed path escaped its configured root: $Child"
    }
    return $resolvedChild
}

function Get-PinnedRepository {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Revision,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (Test-Path -LiteralPath $Destination) {
        if (-not (Test-Path -LiteralPath (Join-Path $Destination '.git'))) {
            throw "Existing custom-node target is not the reviewed Git repository: $Destination"
        }
    } else {
        & git.exe clone '--no-checkout' $Repository $Destination
        if ($LASTEXITCODE -ne 0) {
            throw "Clone failed for $Repository"
        }
    }
    & git.exe '-c' "safe.directory=$Destination" '-C' $Destination 'checkout' '--detach' $Revision
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned checkout failed for $Repository at $Revision"
    }
    $actual = (& git.exe '-c' "safe.directory=$Destination" '-C' $Destination 'rev-parse' 'HEAD').Trim()
    if ($actual -ne $Revision) {
        throw "Pinned checkout mismatch for $Repository. Expected $Revision, got $actual"
    }
}

function Get-PinnedArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$ExecutableName
    )
    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
        $partial = "$Archive.partial"
        & curl.exe '--fail' '--location' '--retry' '3' '--continue-at' '-' '--output' $partial $Url
        if ($LASTEXITCODE -ne 0) {
            throw "Download failed; resumable partial retained at $partial"
        }
        Move-Item -LiteralPath $partial -Destination $Archive
    }
    if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
        Expand-Archive -LiteralPath $Archive -DestinationPath $Destination
    }
    $matches = @(Get-ChildItem -LiteralPath $Destination -Recurse -File -Filter $ExecutableName)
    if ($matches.Count -ne 1) {
        throw "Expected exactly one $ExecutableName under $Destination; found $($matches.Count)"
    }
    return $matches[0].FullName
}

$resolvedComfy = [System.IO.Path]::GetFullPath($ComfyUIRoot).TrimEnd('\')
$resolvedTools = [System.IO.Path]::GetFullPath($ToolsRoot).TrimEnd('\')
$comfyPython = Resolve-ContainedPath -Root $resolvedComfy -Child '.venv\Scripts\python.exe'
$comfyMain = Resolve-ContainedPath -Root $resolvedComfy -Child 'main.py'
if (-not (Test-Path -LiteralPath $comfyMain -PathType Leaf) -or -not (Test-Path -LiteralPath $comfyPython -PathType Leaf)) {
    throw 'The managed ComfyUI installation or its Python environment is unavailable.'
}

New-Item -ItemType Directory -Force -Path $resolvedTools | Out-Null
$downloads = Resolve-ContainedPath -Root $resolvedTools -Child 'downloads'
New-Item -ItemType Directory -Force -Path $downloads | Out-Null

$ggufRoot = Resolve-ContainedPath -Root $resolvedComfy -Child 'custom_nodes\ComfyUI-GGUF'
Get-PinnedRepository -Repository $Pinned.comfyUiGguf.repository -Revision $Pinned.comfyUiGguf.revision -Destination $ggufRoot
& $comfyPython '-m' 'pip' 'install' '--disable-pip-version-check' '-r' (Join-Path $ggufRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    throw 'ComfyUI-GGUF dependency installation failed.'
}
& $comfyPython '-m' 'pip' 'install' '--disable-pip-version-check' '--index-url' $Pinned.pytorch.index "torch==$($Pinned.pytorch.torch)" "torchvision==$($Pinned.pytorch.torchvision)" "torchaudio==$($Pinned.pytorch.torchaudio)"
if ($LASTEXITCODE -ne 0) {
    throw 'The pinned CUDA PyTorch installation failed.'
}
$cudaCheck = @(& $comfyPython '-c' 'import torch; print(torch.__version__); print(torch.version.cuda); print(int(torch.cuda.is_available())); print(torch.cuda.get_device_name(0))')
if ($cudaCheck.Count -ne 4 -or $cudaCheck[2].Trim() -ne '1') {
    throw 'The managed PyTorch environment still cannot access CUDA.'
}
$cudaRecord = [ordered]@{
    torch = $cudaCheck[0].Trim()
    cuda = $cudaCheck[1].Trim()
    device = $cudaCheck[3].Trim()
}

$rifeArchive = Resolve-ContainedPath -Root $downloads -Child $Pinned.rife.asset
$rifeRoot = Resolve-ContainedPath -Root $resolvedTools -Child "rife-$($Pinned.rife.release)"
$rifeExe = Get-PinnedArchive -Url $Pinned.rife.url -Archive $rifeArchive -Destination $rifeRoot -ExecutableName 'rife-ncnn-vulkan.exe'

$realesrganArchive = Resolve-ContainedPath -Root $downloads -Child $Pinned.realesrgan.asset
$realesrganRoot = Resolve-ContainedPath -Root $resolvedTools -Child "realesrgan-$($Pinned.realesrgan.release.TrimStart('v'))"
$realesrganExe = Get-PinnedArchive -Url $Pinned.realesrgan.url -Archive $realesrganArchive -Destination $realesrganRoot -ExecutableName 'realesrgan-ncnn-vulkan.exe'

[Environment]::SetEnvironmentVariable('TRACKPROMPT_COMFYUI_ROOT', $resolvedComfy, 'User')
[Environment]::SetEnvironmentVariable('TRACKPROMPT_COMFYUI_URL', 'http://127.0.0.1:8188', 'User')
[Environment]::SetEnvironmentVariable('TRACKPROMPT_RIFE_PATH', $rifeExe, 'User')
[Environment]::SetEnvironmentVariable('TRACKPROMPT_REALESRGAN_PATH', $realesrganExe, 'User')

$record = [ordered]@{
    schemaVersion = '1.0.0'
    recordedAt = [DateTimeOffset]::UtcNow.ToString('o')
    pytorch = [ordered]@{
        index = $Pinned.pytorch.index
        torch = $cudaRecord.torch
        cuda = $cudaRecord.cuda
        device = $cudaRecord.device
    }
    comfyUiGguf = [ordered]@{
        repository = $Pinned.comfyUiGguf.repository
        revision = $Pinned.comfyUiGguf.revision
        license = $Pinned.comfyUiGguf.license
        installDirectory = $ggufRoot
    }
    rife = [ordered]@{
        repository = $Pinned.rife.repository
        release = $Pinned.rife.release
        revision = $Pinned.rife.revision
        license = $Pinned.rife.license
        asset = $Pinned.rife.asset
        archiveSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $rifeArchive).Hash.ToLowerInvariant()
        executable = $rifeExe
        executableSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $rifeExe).Hash.ToLowerInvariant()
    }
    realesrgan = [ordered]@{
        repository = $Pinned.realesrgan.repository
        release = $Pinned.realesrgan.release
        revision = $Pinned.realesrgan.revision
        license = $Pinned.realesrgan.license
        asset = $Pinned.realesrgan.asset
        archiveSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $realesrganArchive).Hash.ToLowerInvariant()
        executable = $realesrganExe
        executableSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $realesrganExe).Hash.ToLowerInvariant()
    }
}
$lockPath = Resolve-ContainedPath -Root $resolvedComfy -Child 'trackprompt-local-generation-tools-lock.json'
$temporaryLock = "$lockPath.partial"
$record | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $temporaryLock -Encoding UTF8
Move-Item -LiteralPath $temporaryLock -Destination $lockPath -Force

Write-Host "ComfyUI-GGUF pinned at $($Pinned.comfyUiGguf.revision)"
Write-Host "TRACKPROMPT_RIFE_PATH=$rifeExe"
Write-Host "TRACKPROMPT_REALESRGAN_PATH=$realesrganExe"
Write-Host "Install record: $lockPath"
