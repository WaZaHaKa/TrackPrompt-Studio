[CmdletBinding()]
param([switch]$CheckOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$stablePython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
$runner = Join-Path $PSScriptRoot 'tools\hayale-worker.py'
if (-not (Test-Path -LiteralPath $stablePython -PathType Leaf)) { throw 'Existing stable Python 3.12 is required; nothing was installed.' }
$action = if ($CheckOnly) { '--check-only' } else { '--launch' }
& $stablePython -B $runner --run-id 344ab3d2444c $action
exit $LASTEXITCODE
