[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageDirectory,
    [string]$PythonExecutable = ""
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $candidate = Join-Path $repositoryRoot "backend\.venv\Scripts\python.exe"
    $PythonExecutable = if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidate } else { (Get-Command python.exe -ErrorAction Stop).Source }
}
& $PythonExecutable (Join-Path $PSScriptRoot "remote_render_tooling.py") validate-package --package ([IO.Path]::GetFullPath($PackageDirectory))
exit $LASTEXITCODE
