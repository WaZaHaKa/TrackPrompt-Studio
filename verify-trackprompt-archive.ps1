[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Source
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $repositoryRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { $python = 'python' }
Push-Location (Join-Path $repositoryRoot 'backend')
try { & $python -m app.catalog.verify $Source; exit $LASTEXITCODE }
finally { Pop-Location }

