[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ModulePath,
    [Parameter(Mandatory = $true)][string]$StatePath,
    [Parameter(Mandatory = $true)][string]$ControlPath,
    [switch]$UseHighPerformancePowerPlan,
    [ValidateRange(0, 2147483647)][int]$BlenderProcessId = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Import-Module -Name $ModulePath -Force -DisableNameChecking

$arguments = @{
    StatePath = [IO.Path]::GetFullPath($StatePath)
    OperatorConfirmed = $true
    BlenderProcessId = $BlenderProcessId
}
if ($UseHighPerformancePowerPlan) { $arguments.UseHighPerformancePowerPlan = $true }

$started = $false
try {
    $null = Start-WzhkExclusivePerformanceMode @arguments
    $started = $true
    [pscustomobject][ordered]@{
        ready = $true
        processId = $PID
        statePath = [IO.Path]::GetFullPath($StatePath)
        blenderProcessId = $BlenderProcessId
    } | ConvertTo-Json -Compress
    [Console]::Out.Flush()

    while ($true) {
        if (Test-Path -LiteralPath $ControlPath -PathType Leaf) {
            $control = Get-Content -LiteralPath $ControlPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ([string]$control.kind -ne "trackprompt-performance-control" -or [string]$control.action -ne "restore") {
                throw "Performance helper control file is invalid."
            }
            break
        }
        if ($BlenderProcessId -gt 0) {
            $boundProcess = Get-Process -Id $BlenderProcessId -ErrorAction SilentlyContinue
            if ($null -eq $boundProcess -or [string]$boundProcess.ProcessName -notmatch '^blender$') {
                break
            }
        }
        Start-Sleep -Milliseconds 500
    }
}
finally {
    if ($started -or (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
        $null = Stop-WzhkExclusivePerformanceMode -StatePath ([IO.Path]::GetFullPath($StatePath))
    }
    if (Test-Path -LiteralPath $ControlPath -PathType Leaf) {
        Remove-Item -LiteralPath $ControlPath -Force -ErrorAction SilentlyContinue
    }
}
