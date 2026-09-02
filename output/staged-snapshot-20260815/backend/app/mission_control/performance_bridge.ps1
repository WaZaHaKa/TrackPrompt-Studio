[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Status", "Enable", "Restore")]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$ModulePath,

    [Parameter(Mandatory = $true)]
    [string]$StatePath,

    [switch]$OperatorConfirmed,
    [switch]$UseHighPerformancePowerPlan,
    [ValidateRange(0, 2147483647)]
    [int]$BlenderProcessId = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ModulePath -PathType Leaf)) {
    throw "Performance module does not exist."
}

Import-Module -Name $ModulePath -Force -DisableNameChecking

function Get-OptionalProperty {
    param([object]$Value, [string]$Name, [object]$Default = $null)
    if ($null -eq $Value -or $null -eq $Value.PSObject.Properties[$Name]) { return $Default }
    return $Value.$Name
}

function Get-PerformanceStatus {
    $powerSource = Get-WzhkPowerSource
    $activePlan = $null
    try { $activePlan = Get-WzhkActivePowerPlan } catch { $activePlan = $null }
    $telemetry = Get-WzhkNvidiaTelemetry
    $saved = $null
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        try { $saved = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { throw "Performance-mode state file is invalid JSON." }
        if ([string](Get-OptionalProperty $saved "kind" "") -ne "trackprompt-exclusive-performance-state") {
            throw "Performance-mode state file has the wrong kind."
        }
    }
    $restoreRequired = [bool](Get-OptionalProperty $saved "restoreRequired" $false)
    return [pscustomobject][ordered]@{
        available = $true
        active = $restoreRequired
        restoreRequired = $restoreRequired
        onAcPower = $(if ([bool](Get-OptionalProperty $powerSource "Available" $false)) { [bool](Get-OptionalProperty $powerSource "OnAcPower" $false) } else { $null })
        powerLineStatus = [string](Get-OptionalProperty $powerSource "PowerLineStatus" "unknown")
        previousPowerPlanGuid = [string](Get-OptionalProperty $saved "previousPowerPlanGuid" "")
        currentPowerPlanGuid = [string](Get-OptionalProperty $activePlan "Guid" "")
        selectedPowerPlanGuid = [string](Get-OptionalProperty $saved "selectedPowerPlanGuid" "")
        sleepInhibited = [bool](Get-OptionalProperty $saved "sleepInhibited" $false)
        blenderProcessId = [int](Get-OptionalProperty $saved "blenderProcessId" 0)
        blenderPriority = [string](Get-OptionalProperty $saved "blenderPriority" "")
        gpuTemperatureC = $(if ([bool](Get-OptionalProperty $telemetry "Available" $false)) { [double](Get-OptionalProperty $telemetry "TemperatureC" 0) } else { $null })
        gpuUtilizationPercent = $(if ([bool](Get-OptionalProperty $telemetry "Available" $false)) { [double](Get-OptionalProperty $telemetry "UtilizationPercent" 0) } else { $null })
        vramUsedMiB = $(if ([bool](Get-OptionalProperty $telemetry "Available" $false)) { [double](Get-OptionalProperty $telemetry "VramUsedMiB" 0) } else { $null })
        restoredAt = [string](Get-OptionalProperty $saved "restoredAt" "")
        detail = $(if ($restoreRequired) { "Exclusive Performance Mode is active and must be restored after rendering." } else { "Exclusive Performance Mode is not active." })
    }
}

if ($Action -eq "Enable") {
    if (-not $OperatorConfirmed) { throw "Exclusive Performance Mode requires explicit operator confirmation." }
    $arguments = @{
        StatePath = [IO.Path]::GetFullPath($StatePath)
        OperatorConfirmed = $true
        BlenderProcessId = $BlenderProcessId
    }
    if ($UseHighPerformancePowerPlan) { $arguments.UseHighPerformancePowerPlan = $true }
    $null = Start-WzhkExclusivePerformanceMode @arguments
}
elseif ($Action -eq "Restore") {
    if (-not $OperatorConfirmed) { throw "Restoring Exclusive Performance Mode requires explicit operator confirmation." }
    $null = Stop-WzhkExclusivePerformanceMode -StatePath ([IO.Path]::GetFullPath($StatePath))
}

Get-PerformanceStatus | ConvertTo-Json -Depth 30 -Compress
