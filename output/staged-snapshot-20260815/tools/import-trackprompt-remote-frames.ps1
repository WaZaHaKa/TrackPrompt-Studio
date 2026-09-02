[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ReturnDirectory,
    [Parameter(Mandatory = $true)][string]$PackageDirectory,
    [Parameter(Mandatory = $true)][string]$LocalProfilePath,
    [Parameter(Mandatory = $true)][string]$LocalScenePath,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [switch]$OperatorConfirmed,
    [string]$PythonExecutable = ""
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (-not $OperatorConfirmed) { throw "Remote frame import requires explicit operator confirmation after privacy and visual review. Returned files are quarantined before publication." }
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $candidate = Join-Path $repositoryRoot "backend\.venv\Scripts\python.exe"
    $PythonExecutable = if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidate } else { (Get-Command python.exe -ErrorAction Stop).Source }
}
& $PythonExecutable (Join-Path $PSScriptRoot "remote_render_tooling.py") import-return --return-directory ([IO.Path]::GetFullPath($ReturnDirectory)) --package ([IO.Path]::GetFullPath($PackageDirectory)) --local-profile ([IO.Path]::GetFullPath($LocalProfilePath)) --local-scene ([IO.Path]::GetFullPath($LocalScenePath)) --output ([IO.Path]::GetFullPath($OutputDirectory))
exit $LASTEXITCODE
