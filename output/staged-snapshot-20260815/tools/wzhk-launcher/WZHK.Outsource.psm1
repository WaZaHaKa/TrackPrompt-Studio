Set-StrictMode -Version Latest

function New-WzhkRemoteChunkDistribution {
    param(
        [int]$FrameStart = 1,
        [int]$FrameEnd = 13029,
        [ValidateRange(1, 10000)][int]$FramesPerChunk = 150,
        [ValidateRange(1, 256)][int]$RemoteWorkers = 1,
        [switch]$IncludeLocalWorker,
        [Parameter(Mandatory = $true)][string]$SceneSha256,
        [Parameter(Mandatory = $true)][string]$ProfileSha256,
        [string]$OutputFormat = "PNG"
    )
    if ($FrameEnd -lt $FrameStart) { throw "FrameEnd must be at least FrameStart." }
    foreach ($hash in @($SceneSha256, $ProfileSha256)) { if ($hash -notmatch '^[A-Fa-f0-9]{64}$') { throw "Scene and profile identities must be complete SHA-256 values." } }
    $workerCount = $RemoteWorkers + $(if ($IncludeLocalWorker) { 1 } else { 0 })
    $assignments = New-Object System.Collections.Generic.List[object]
    $index = 0
    for ($start = $FrameStart; $start -le $FrameEnd; $start += $FramesPerChunk) {
        $end = [Math]::Min($FrameEnd, $start + $FramesPerChunk - 1)
        $workerIndex = $index % $workerCount
        $workerId = if ($IncludeLocalWorker -and $workerIndex -eq 0) { "local" } else { "remote-" + ($(if ($IncludeLocalWorker) { $workerIndex } else { $workerIndex + 1 })).ToString("D2") }
        $chunkId = "chunk-" + $start.ToString("D6") + "-" + $end.ToString("D6")
        $assignments.Add([pscustomobject][ordered]@{
            chunkId = $chunkId
            workerId = $workerId
            startFrame = $start
            endFrame = $end
            expectedFrameCount = $end - $start + 1
            sceneSha256 = $SceneSha256.ToUpperInvariant()
            profileSha256 = $ProfileSha256.ToUpperInvariant()
            outputFormat = $OutputFormat.ToUpperInvariant()
            returnArchiveName = $chunkId + "-" + $workerId + ".zip"
            leaseStatus = "unassigned"
        })
        $index += 1
    }
    return $assignments.ToArray()
}

function Get-WzhkOutsourceEstimate {
    param(
        [Parameter(Mandatory = $true)][ValidateRange(0.001, 10000.0)][double]$SecondsPerFrame,
        [ValidateRange(1, 1000000)][int]$FrameCount = 13029,
        [ValidateRange(1, 256)][int]$Workers = 1,
        [ValidateRange(0.0, 100000.0)][double]$HourlyRate = 0.0,
        [ValidateRange(0.0, 10000.0)][double]$PerFramePrice = 0.0,
        [ValidateRange(0.0, 100000.0)][double]$StorageCost = 0.0,
        [ValidateRange(0.0, 100000.0)][double]$EgressCost = 0.0,
        [ValidateRange(0.0, 100000.0)][double]$TransferHours = 0.0,
        [double]$TransferBytes = 0.0
    )
    $gpuHours = ($SecondsPerFrame * $FrameCount) / 3600.0
    $wallHours = ($gpuHours / $Workers) + $TransferHours
    $computeCost = ($gpuHours * $HourlyRate) + ($FrameCount * $PerFramePrice)
    $expectedCost = $computeCost + $StorageCost + $EgressCost
    return [pscustomobject][ordered]@{
        TotalGpuHours = [Math]::Round($gpuHours, 3)
        ExpectedWallHours = [Math]::Round($wallHours, 3)
        ConservativeWallHours = [Math]::Round(($gpuHours * 1.25 / $Workers) + $TransferHours, 3)
        ExpectedCost = [Math]::Round($expectedCost, 2)
        ConservativeCost = [Math]::Round(($computeCost * 1.25) + $StorageCost + $EgressCost, 2)
        TransferBytes = [int64]$TransferBytes
        Confidence = "LOW"
        Note = "Provider performance and cost remain estimates until a provider-specific 10-still plus 30-consecutive-frame test passes."
    }
}

function Invoke-WzhkRemoteTool {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExecutable,
        [Parameter(Mandatory = $true)][string]$ToolPath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $output = @(& $PythonExecutable $ToolPath @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $text = ($output | ForEach-Object { [string]$_ }) -join "`n"
    if ($exitCode -ne 0) { throw "Remote-render tool failed with exit code $exitCode. $text" }
    return ($text | ConvertFrom-Json)
}

Export-ModuleMember -Function `
    New-WzhkRemoteChunkDistribution, `
    Get-WzhkOutsourceEstimate, `
    Invoke-WzhkRemoteTool
