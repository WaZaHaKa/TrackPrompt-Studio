Set-StrictMode -Version Latest

function Save-WzhkPerformanceState {
    param([Parameter(Mandatory = $true)][object]$State, [Parameter(Mandatory = $true)][string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    $directory = [IO.Path]::GetDirectoryName($full)
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { $null = New-Item -ItemType Directory -Path $directory }
    $temporary = Join-Path $directory (".performance-state." + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [IO.File]::WriteAllText($temporary, (($State | ConvertTo-Json -Depth 30) + "`n"), (New-Object Text.UTF8Encoding($false)))
        $null = Get-Content -LiteralPath $temporary -Raw | ConvertFrom-Json
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $backup = $temporary + ".bak"
            try { [IO.File]::Replace($temporary, $full, $backup, $true) }
            finally { if (Test-Path -LiteralPath $backup -PathType Leaf) { Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue } }
        }
        else { [IO.File]::Move($temporary, $full) }
    }
    finally { if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue } }
    return $full
}

function Get-WzhkPowerSource {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $status = [Windows.Forms.SystemInformation]::PowerStatus
        $line = [string]$status.PowerLineStatus
        return [pscustomobject]@{ Available = $true; OnAcPower = ($line -eq "Online"); PowerLineStatus = $line; BatteryChargeStatus = [string]$status.BatteryChargeStatus }
    }
    catch { return [pscustomobject]@{ Available = $false; OnAcPower = $false; PowerLineStatus = "unknown"; Error = $_.Exception.Message } }
}

function Set-WzhkSleepInhibition {
    param([Parameter(Mandatory = $true)][bool]$Enabled)
    if ($null -eq ("WzhkNativeExecutionState" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class WzhkNativeExecutionState {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint flags);
}
"@
    }
    [uint32]$flags = if ($Enabled) {
        [uint32]::Parse("80000041", [Globalization.NumberStyles]::HexNumber)
    }
    else {
        [uint32]::Parse("80000000", [Globalization.NumberStyles]::HexNumber)
    }
    $previous = [WzhkNativeExecutionState]::SetThreadExecutionState($flags)
    if ($previous -eq 0) { throw "Windows rejected the sleep-inhibition request." }
    return [pscustomobject]@{ Enabled = $Enabled; Flags = $flags }
}

function Get-WzhkCompetingGpuProcesses {
    $command = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) { return @() }
    $items = New-Object System.Collections.Generic.List[object]
    try {
        foreach ($line in @(& $command.Source --query-compute-apps=pid,process_name,used_gpu_memory --format=csv,noheader,nounits 2>$null)) {
            $parts = @([string]$line -split ',' | ForEach-Object { $_.Trim() })
            if ($parts.Count -lt 3) { continue }
            $name = [IO.Path]::GetFileNameWithoutExtension($parts[1])
            $known = $name -match '^(ollama|com\.docker\.backend|vmmem|python|blender)$'
            $items.Add([pscustomobject]@{ ProcessId = [int]$parts[0]; ProcessName = $name; UsedGpuMemoryMiB = [double]$parts[2]; KnownTrackPromptRelated = $known })
        }
    }
    catch { }
    return $items.ToArray()
}

function Get-WzhkNvidiaTelemetry {
    $command = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) { return [pscustomobject]@{ Available = $false } }
    try {
        $line = [string](@(& $command.Source --query-gpu=name,driver_version,utilization.gpu,memory.total,memory.used,temperature.gpu,power.draw --format=csv,noheader,nounits 2>$null | Select-Object -First 1))
        $parts = @($line -split ',' | ForEach-Object { $_.Trim() })
        if ($parts.Count -lt 7) { throw "Unexpected nvidia-smi output." }
        return [pscustomobject][ordered]@{
            Available = $true
            GpuModel = $parts[0]
            DriverVersion = $parts[1]
            UtilizationPercent = [double]$parts[2]
            VramTotalMiB = [double]$parts[3]
            VramUsedMiB = [double]$parts[4]
            TemperatureC = [double]$parts[5]
            PowerDrawW = [double]$parts[6]
        }
    }
    catch { return [pscustomobject]@{ Available = $false; Error = $_.Exception.Message } }
}

function Get-WzhkActivePowerPlan {
    $line = [string](@(& powercfg.exe /GETACTIVESCHEME 2>$null | Select-Object -First 1))
    $match = [regex]::Match($line, '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
    if (-not $match.Success) { throw "The active Windows power plan could not be determined." }
    return [pscustomobject]@{ Guid = $match.Value.ToLowerInvariant(); Description = $line.Trim() }
}

function Set-WzhkBlenderProcessPriority {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [ValidateSet("AboveNormal", "High")][string]$Priority = "High"
    )
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    if ($process.ProcessName -ne "blender") { throw "Priority changes are limited to an explicitly selected Blender process." }
    if ($Priority -eq "Realtime") { throw "Realtime process priority is forbidden." }
    $process.PriorityClass = [Diagnostics.ProcessPriorityClass]::$Priority
    return [pscustomobject]@{ ProcessId = $ProcessId; Priority = [string]$process.PriorityClass }
}

function Start-WzhkExclusivePerformanceMode {
    param(
        [Parameter(Mandatory = $true)][string]$StatePath,
        [switch]$OperatorConfirmed,
        [switch]$UseHighPerformancePowerPlan,
        [int]$BlenderProcessId = 0
    )
    if (-not $OperatorConfirmed) { throw "Exclusive Performance Mode requires explicit operator confirmation." }
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        $existing = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
        if ([bool]$existing.restoreRequired) { throw "A prior performance-mode state still requires restoration; restore it before starting another session." }
    }
    $powerSource = Get-WzhkPowerSource
    if (-not $powerSource.Available -or -not $powerSource.OnAcPower) { throw "Exclusive Performance Mode requires verified AC power." }
    $previous = Get-WzhkActivePowerPlan
    $competing = @(Get-WzhkCompetingGpuProcesses)
    $previousPriority = ""
    if ($BlenderProcessId -gt 0) {
        $selectedProcess = Get-Process -Id $BlenderProcessId -ErrorAction Stop
        if ($selectedProcess.ProcessName -ne "blender") { throw "The selected priority target is not Blender." }
        $previousPriority = [string]$selectedProcess.PriorityClass
    }
    $state = [pscustomobject][ordered]@{
        schemaVersion = "1.0.0"
        kind = "trackprompt-exclusive-performance-state"
        startedAt = (Get-Date).ToUniversalTime().ToString("o")
        previousPowerPlanGuid = $previous.Guid
        selectedPowerPlanGuid = $previous.Guid
        blenderProcessId = $BlenderProcessId
        blenderPreviousPriority = $previousPriority
        blenderPriority = "unchanged"
        powerSource = $powerSource
        sleepInhibited = $false
        competingGpuProcesses = $competing
        servicesPaused = @()
        servicePausePolicy = "detected only; never pause or terminate without a separate explicit operator action"
        restoreRequired = $true
    }
    $null = Save-WzhkPerformanceState -State $state -Path $StatePath
    try {
        $null = Set-WzhkSleepInhibition -Enabled $true
        $state.sleepInhibited = $true
        $null = Save-WzhkPerformanceState -State $state -Path $StatePath
        if ($UseHighPerformancePowerPlan) {
            $highPerformance = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
            & powercfg.exe /SETACTIVE $highPerformance | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Windows rejected the High Performance power plan." }
            $state.selectedPowerPlanGuid = $highPerformance
            $null = Save-WzhkPerformanceState -State $state -Path $StatePath
        }
        if ($BlenderProcessId -gt 0) {
            $priority = Set-WzhkBlenderProcessPriority -ProcessId $BlenderProcessId -Priority High
            $state.blenderPriority = $priority.Priority
            $null = Save-WzhkPerformanceState -State $state -Path $StatePath
        }
        return $state
    }
    catch {
        $startupError = $_.Exception
        try { $null = Stop-WzhkExclusivePerformanceMode -StatePath $StatePath }
        catch {
            throw ("Exclusive Performance Mode startup failed and automatic restoration also failed. Startup: " + $startupError.Message + " Restoration: " + $_.Exception.Message)
        }
        throw $startupError
    }
}

function Stop-WzhkExclusivePerformanceMode {
    param([Parameter(Mandatory = $true)][string]$StatePath)
    $full = [IO.Path]::GetFullPath($StatePath)
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "Performance-mode state file does not exist." }
    $state = Get-Content -LiteralPath $full -Raw | ConvertFrom-Json
    if ([string]$state.kind -ne "trackprompt-exclusive-performance-state") { throw "Performance-mode state file has the wrong kind." }
    if ([string]$state.previousPowerPlanGuid -notmatch '^[0-9a-fA-F-]{36}$') { throw "Previous power-plan identity is invalid." }
    & powercfg.exe /SETACTIVE ([string]$state.previousPowerPlanGuid) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Windows could not restore the previous power plan." }
    if ([int]$state.blenderProcessId -gt 0 -and -not [string]::IsNullOrWhiteSpace([string]$state.blenderPreviousPriority)) {
        try {
            $process = Get-Process -Id ([int]$state.blenderProcessId) -ErrorAction Stop
            if ($process.ProcessName -eq "blender" -and [string]$state.blenderPreviousPriority -ne "Realtime") {
                $process.PriorityClass = [Diagnostics.ProcessPriorityClass][Enum]::Parse([Diagnostics.ProcessPriorityClass], [string]$state.blenderPreviousPriority)
            }
        }
        catch { }
    }
    $null = Set-WzhkSleepInhibition -Enabled $false
    $state.sleepInhibited = $false
    $state.restoreRequired = $false
    $state | Add-Member -NotePropertyName restoredAt -NotePropertyValue ((Get-Date).ToUniversalTime().ToString("o")) -Force
    $null = Save-WzhkPerformanceState -State $state -Path $full
    return $state
}

function Test-WzhkThermalSafety {
    param(
        [Parameter(Mandatory = $true)][double]$TemperatureC,
        [ValidateRange(50, 100)][double]$WarningThresholdC = 82,
        [ValidateRange(50, 110)][double]$CriticalThresholdC = 88
    )
    if ($CriticalThresholdC -le $WarningThresholdC) { throw "Critical thermal threshold must exceed the warning threshold." }
    $state = if ($TemperatureC -ge $CriticalThresholdC) { "CRITICAL" } elseif ($TemperatureC -ge $WarningThresholdC) { "WARNING" } else { "NORMAL" }
    return [pscustomobject]@{ State = $state; TemperatureC = $TemperatureC; RequestStopAfterChunk = ($state -eq "CRITICAL") }
}

Export-ModuleMember -Function `
    Get-WzhkPowerSource, `
    Set-WzhkSleepInhibition, `
    Get-WzhkCompetingGpuProcesses, `
    Get-WzhkNvidiaTelemetry, `
    Get-WzhkActivePowerPlan, `
    Set-WzhkBlenderProcessPriority, `
    Start-WzhkExclusivePerformanceMode, `
    Stop-WzhkExclusivePerformanceMode, `
    Test-WzhkThermalSafety
