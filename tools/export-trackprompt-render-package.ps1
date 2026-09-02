[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ApprovedScenePath,
    [Parameter(Mandatory = $true)][string]$RenderProfilePath,
    [switch]$PrivacyConfirmed,
    [switch]$AcknowledgeSceneDisclosure,
    [ValidateRange(1, 64)][int]$RemoteWorkers = 1,
    [ValidateRange(1, 1200)][int]$FramesPerChunk = 150,
    [string]$BlenderExecutable = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    [string]$PythonExecutable = "",
    [switch]$SkipHeadlessSmoke
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
$ErrorActionPreference = "Stop"
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Import-Module (Join-Path $PSScriptRoot "wzhk-launcher\WZHK.Calibration.psm1") -Force -DisableNameChecking
if (-not $PrivacyConfirmed -or -not $AcknowledgeSceneDisclosure) {
    throw "Remote packaging requires explicit privacy confirmation and acknowledgement that the provider can inspect scene design, materials, geometry, and included assets. No upload was performed."
}
$safety = Get-WzhkRenderSafetyAudit -RepositoryRoot $repositoryRoot
if (-not $safety.SafeForGpuCalibration) { throw "Remote scene sanitization is blocked while a Blender render or production mutex is active." }
foreach ($path in @($ApprovedScenePath, $RenderProfilePath, $BlenderExecutable)) { if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required package input is missing: $path" } }
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $candidate = Join-Path $repositoryRoot "backend\.venv\Scripts\python.exe"
    $PythonExecutable = if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidate } else { (Get-Command python.exe -ErrorAction Stop).Source }
}
$scene = [IO.Path]::GetFullPath($ApprovedScenePath)
$profile = [IO.Path]::GetFullPath($RenderProfilePath)
$sceneHash = (Get-FileHash -LiteralPath $scene -Algorithm SHA256).Hash.ToUpperInvariant()
$profileHash = (Get-FileHash -LiteralPath $profile -Algorithm SHA256).Hash.ToUpperInvariant()
$package = Join-Path $repositoryRoot ("render-packages\trip-to-andromeda\{0}\{1}\package" -f $sceneHash.Substring(0, 12).ToLowerInvariant(), $profileHash.Substring(0, 12).ToLowerInvariant())
if (Test-Path -LiteralPath $package) { throw "Remote package destination already exists; package creation never overwrites: $package" }
$staging = Join-Path $repositoryRoot ("test-output\render-package-staging\" + [Guid]::NewGuid().ToString("N"))
$null = New-Item -ItemType Directory -Path $staging
$sanitized = Join-Path $staging "trackprompt-remote.blend"
$sanitizationReport = Join-Path $staging "sanitization-report.json"
$sanitizer = Join-Path $repositoryRoot "blender\sanitize_remote_scene.py"
& $BlenderExecutable --background $scene --python-exit-code 1 --python $sanitizer -- --output $sanitized --report $sanitizationReport --expected-source-sha256 $sceneHash
if ($LASTEXITCODE -ne 0) { throw "Blender remote-scene sanitization failed with exit code $LASTEXITCODE." }
$tool = Join-Path $repositoryRoot "tools\remote_render_tooling.py"
& $PythonExecutable $tool create-package --sanitized-scene $sanitized --source-profile $profile --sanitization-report $sanitizationReport --destination $package --workers $RemoteWorkers --frames-per-chunk $FramesPerChunk --blender-version "5.2.0 LTS"
if ($LASTEXITCODE -ne 0) { throw "Remote package assembly failed with exit code $LASTEXITCODE." }
& $PythonExecutable $tool validate-package --package $package
if ($LASTEXITCODE -ne 0) { throw "Remote package validation failed with exit code $LASTEXITCODE." }
if (-not $SkipHeadlessSmoke) {
    $smoke = Join-Path $staging "clean-environment-smoke"
    & $PythonExecutable (Join-Path $package "render_trackprompt_worker.py") --package-directory $package --blender $BlenderExecutable --start 1 --end 1 --worker-id clean-environment-smoke --output-directory $smoke
    if ($LASTEXITCODE -ne 0) { throw "Clean-environment one-frame package smoke failed with exit code $LASTEXITCODE." }
}
Write-Output ([pscustomobject]@{
    Ok = $true
    Package = $package
    SceneSha256 = $sceneHash
    SourceProfileSha256 = $profileHash
    PrivateAudioIncluded = $false
    UploadPerformed = $false
    SanitizationReport = $sanitizationReport
    HeadlessSmoke = $(if ($SkipHeadlessSmoke) { "skipped" } else { "passed" })
} | ConvertTo-Json -Depth 20)
