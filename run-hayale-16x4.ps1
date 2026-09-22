#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Check','Select','Music','Prepare','Authorize','Start','Resume','Status','Stop','Export','Verify','RecoverWorker')]
    [string]$Action = 'Check',
    [ValidateSet('still-animatic','motion-preview','full-draft','final')][string]$Profile = 'still-animatic',
    [string]$JobId, [string]$Scene, [string]$Asset, [switch]$PromotionalUse,
    [switch]$CloudUpload, [switch]$RetainedVideo, [double]$SourceIn = 0,
    [string[]]$MusicSegment, [switch]$ApproveArrangement,
    [string]$Confirmation, [string]$LeaseToken,
    [string]$PythonExecutable = $env:TRACKPROMPT_PYTHON_EXECUTABLE
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe'
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw 'Select an existing stable Python 3.11-3.13 runtime with -PythonExecutable. No installation was attempted.'
}
$entry = Join-Path $PSScriptRoot 'tools/hayale-16x4.py'
$arguments = @('-B', $entry, $Action.ToLowerInvariant(), '--profile', $Profile)
if ($JobId) { $arguments += @('--job-id', $JobId) }
if ($Scene) { $arguments += @('--scene', $Scene) }
if ($Asset) { $arguments += @('--asset', $Asset) }
if ($PromotionalUse) { $arguments += '--promotional-use' }
if ($CloudUpload) { $arguments += '--cloud-upload' }
if ($RetainedVideo) { $arguments += '--retained-video' }
$arguments += @('--source-in', $SourceIn.ToString([Globalization.CultureInfo]::InvariantCulture))
foreach ($segment in $MusicSegment) { $arguments += @('--music-segment', $segment) }
if ($ApproveArrangement) { $arguments += '--approve-arrangement' }
if ($Confirmation) { $arguments += @('--confirmation', $Confirmation) }
if ($LeaseToken) { $arguments += @('--lease-token', $LeaseToken) }
& $PythonExecutable @arguments
exit $LASTEXITCODE
