[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $repositoryRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = 'python'
}
$arguments = @('-m', 'app.catalog.backup', 'create', $Destination)
if ($DryRun) { $arguments += '--dry-run' }
Push-Location (Join-Path $repositoryRoot 'backend')
try { & $python @arguments; exit $LASTEXITCODE }
finally { Pop-Location }

