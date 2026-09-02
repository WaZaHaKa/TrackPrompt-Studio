Set-StrictMode -Version Latest

$script:CloudCliKind = "trackprompt-cloud-cli-result"
$script:CloudCliSchemaVersion = "1.0.0"

function Resolve-WzhkCloudPython {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)

    $root = [IO.Path]::GetFullPath($RepositoryRoot)
    foreach ($candidate in @(
        (Join-Path $root "backend\.venv\Scripts\python.exe"),
        (Join-Path $root ".venv\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    foreach ($name in @("python.exe", "python")) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($null -ne $command) { return [string]$command.Source }
    }
    return ""
}

function Get-WzhkCloudCliReadiness {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)

    $root = [IO.Path]::GetFullPath($RepositoryRoot)
    $python = Resolve-WzhkCloudPython -RepositoryRoot $root
    $packagePath = Join-Path $root "cloud_render\__init__.py"
    $cliPath = Join-Path $root "cloud_render\cli.py"
    $issues = New-Object System.Collections.Generic.List[string]
    if ([string]::IsNullOrWhiteSpace($python)) { $issues.Add("Python runtime was not found.") }
    if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) { $issues.Add("cloud_render package is missing.") }
    if (-not (Test-Path -LiteralPath $cliPath -PathType Leaf)) { $issues.Add("cloud_render.cli is missing.") }

    return [pscustomobject][ordered]@{
        Ready = ($issues.Count -eq 0)
        Offline = $true
        ProvisioningEnabled = $false
        RepositoryRoot = $root
        PythonExecutable = $python
        PackagePath = $packagePath
        CliPath = $cliPath
        Issues = $issues.ToArray()
        Detail = $(if ($issues.Count -eq 0) {
            "Provider-neutral cloud CLI is installed; provisioning remains disabled until an explicit provider capability check and operator authorization."
        }
        else { $issues -join " " })
    }
}

function Invoke-WzhkCloudCli {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)]
        [ValidateSet(
            "readiness", "authorization-token", "validate-manifest", "prepare-manifest", "seal-manifest",
            "scheduler-status", "scheduler-init", "scheduler-claim", "scheduler-cancel",
            "tournament-rank", "encode-plan", "mux-plan", "import-return", "mock-worker",
            "brev-readiness", "brev-discover", "brev-list", "brev-provision-benchmark", "brev-teardown"
        )]
        [string]$Command,
        [string[]]$Arguments = @(),
        [string]$PythonExecutable = "",
        [switch]$AllowNetwork,
        [switch]$AllowLocalMutation,
        [switch]$AllowDestructive,
        [switch]$AllowBillable,
        [switch]$PlanLocked,
        [switch]$FinalConfirmed,
        [string]$AuthorizationToken = "",
        [string]$ExpectedAuthorizationToken = ""
    )

    $root = [IO.Path]::GetFullPath($RepositoryRoot)
    $readiness = Get-WzhkCloudCliReadiness -RepositoryRoot $root
    if (-not $readiness.Ready) { throw $readiness.Detail }
    if ([string]::IsNullOrWhiteSpace($PythonExecutable)) { $PythonExecutable = $readiness.PythonExecutable }
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) { throw "Cloud CLI Python executable does not exist." }

    $networkCommands = @("brev-discover", "brev-list", "brev-provision-benchmark", "brev-teardown")
    $billableCommands = @("brev-provision-benchmark")
    $localMutationCommands = @("prepare-manifest", "seal-manifest", "scheduler-status", "scheduler-init", "scheduler-claim", "scheduler-cancel", "import-return", "mock-worker")
    $destructiveCommands = @("scheduler-cancel", "import-return", "brev-teardown")
    if ($Command -in $networkCommands -and -not $AllowNetwork) {
        throw "Cloud command '$Command' may contact a provider and requires explicit -AllowNetwork."
    }
    if ($Command -in $localMutationCommands -and -not $AllowLocalMutation) {
        throw "Cloud command '$Command' changes local preparation/runtime state and requires explicit -AllowLocalMutation."
    }
    if ($Command -in $destructiveCommands -and -not $AllowDestructive) {
        throw "Cloud command '$Command' can cancel, move, publish, stop, or delete state and requires explicit -AllowDestructive."
    }
    if ($Command -in $billableCommands) {
        if (-not $AllowBillable) { throw "Billable cloud commands require explicit -AllowBillable." }
        if (-not $PlanLocked) { throw "Billable cloud commands require [Y] LOCK CLOUD PLAN." }
        if (-not $FinalConfirmed) { throw "Billable cloud commands require [Y] PROVISION BILLABLE GPU WORKERS." }
        if ([string]::IsNullOrWhiteSpace($ExpectedAuthorizationToken) -or $AuthorizationToken -cne $ExpectedAuthorizationToken) {
            throw "Billable cloud authorization token does not match the exact locked plan."
        }
    }

    $nativeArguments = @("-m", "cloud_render.cli", $Command) + @($Arguments)
    $captured = @()
    $exitCode = 3
    $previousPreference = $ErrorActionPreference
    Push-Location $root
    try {
        $ErrorActionPreference = "Continue"
        $captured = @(& $PythonExecutable @nativeArguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
        Pop-Location
    }

    $raw = (($captured | ForEach-Object { [string]$_ }) -join "`n").Trim()
    $payload = $null
    try { $payload = $raw | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "cloud_render.cli returned unreadable JSON (exit $exitCode)." }
    if ([string]$payload.schemaVersion -ne $script:CloudCliSchemaVersion -or [string]$payload.kind -ne $script:CloudCliKind) {
        throw "cloud_render.cli returned an unsupported response envelope."
    }
    if ([string]$payload.command -ne $Command) { throw "cloud_render.cli response command did not match the request." }

    return [pscustomobject][ordered]@{
        Ok = ([bool]$payload.ok -and $exitCode -eq 0)
        ExitCode = $exitCode
        Command = $Command
        Payload = $payload
        Data = $(if ($null -ne $payload.PSObject.Properties["data"]) { $payload.data } else { $null })
        Error = $(if ($null -ne $payload.PSObject.Properties["error"]) { $payload.error } else { $null })
        Raw = $raw
    }
}

function Get-WzhkCloudDashboardLines {
    [CmdletBinding()]
    param(
        [AllowNull()][object]$Status,
        [AllowNull()][object]$SupplementalStatus = $null
    )

    function Get-CloudValue {
        param([AllowNull()][object]$Object, [string[]]$Names, [string]$Default = "unknown")
        if ($null -eq $Object) { return $Default }
        foreach ($name in $Names) {
            $property = $Object.PSObject.Properties[$name]
            if ($null -ne $property -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)) { return [string]$property.Value }
        }
        return $Default
    }

    function Get-CloudStateCount {
        param([AllowNull()][object]$Object, [string]$State)
        if ($null -eq $Object) { return "not supplied" }
        $countsProperty = $Object.PSObject.Properties["state_counts"]
        if ($null -eq $countsProperty -or $null -eq $countsProperty.Value) { return "not supplied" }
        $stateProperty = $countsProperty.Value.PSObject.Properties[$State]
        if ($null -eq $stateProperty) { return "not supplied" }
        return [string]$stateProperty.Value
    }

    function Get-CloudValueAcross {
        param([string[]]$Names, [string]$Default = "not supplied")
        $supplementalValue = Get-CloudValue $SupplementalStatus $Names ""
        if (-not [string]::IsNullOrWhiteSpace($supplementalValue)) { return $supplementalValue }
        return Get-CloudValue $Status $Names $Default
    }

    $completeChunks = Get-CloudValue $Status @("chunksComplete", "chunks_complete") ""
    if ([string]::IsNullOrWhiteSpace($completeChunks)) { $completeChunks = Get-CloudStateCount $Status "COMPLETE" }
    $completeFrames = Get-CloudValue $Status @("framesComplete", "frames_complete", "published_frames") "not supplied"
    $jobId = Get-CloudValueAcross @("fleetId", "fleet_id", "job_id")
    $schedulerState = @(
        "pending " + (Get-CloudStateCount $Status "PENDING")
        "leased " + (Get-CloudStateCount $Status "LEASED")
        "rendering " + (Get-CloudStateCount $Status "RENDERING")
        "uploading " + (Get-CloudStateCount $Status "UPLOADING")
        "validating " + (Get-CloudStateCount $Status "VALIDATING")
        "complete " + (Get-CloudStateCount $Status "COMPLETE")
        "failed " + (Get-CloudStateCount $Status "FAILED")
        "retryable " + (Get-CloudStateCount $Status "RETRYABLE")
        "quarantined " + (Get-CloudStateCount $Status "QUARANTINED")
    ) -join " / "

    return @(
        "SNAPSHOT SOURCE  : offline scheduler status; provider/cost telemetry appears only when separately supplied"
        "PROVIDER         : " + (Get-CloudValueAcross @("provider"))
        "FLEET / JOB ID   : " + $jobId
        "GPU / WORKERS    : " + (Get-CloudValueAcross @("gpuType", "gpu_type")) + " / " + (Get-CloudValueAcross @("workerCount", "worker_count"))
        "WORKER STATE     : online " + (Get-CloudValueAcross @("workersOnline", "workers_online")) + " / rendering " + (Get-CloudValueAcross @("workersRendering", "workers_rendering")) + " / failed " + (Get-CloudValueAcross @("failedWorkers", "failed_workers"))
        "CHUNK STATES     : " + $schedulerState
        "PROGRESS         : chunks " + $completeChunks + " / published frames " + $completeFrames
        "CONFLICTS        : " + (Get-CloudValue $Status @("unresolved_conflicts") "not supplied") + " unresolved / cancelled " + (Get-CloudValue $Status @("cancelled") "not supplied") + " / complete " + (Get-CloudValue $Status @("complete") "not supplied")
        "THROUGHPUT       : " + (Get-CloudValueAcross @("framesPerHour", "frames_per_hour"))
        "ETA              : " + (Get-CloudValueAcross @("estimatedCompletion", "estimated_completion"))
        "COST / FRAME     : " + (Get-CloudValueAcross @("costPerFrame", "cost_per_frame"))
        "SPEND            : " + (Get-CloudValueAcross @("currentSpend", "current_spend")) + " current / " + (Get-CloudValueAcross @("projectedSpend", "projected_spend")) + " projected / " + (Get-CloudValueAcross @("budget")) + " budget"
        "STORAGE          : " + (Get-CloudValueAcross @("storageUsed", "storage_used"))
        "TRANSFER         : " + (Get-CloudValueAcross @("transferStatus", "transfer_status"))
        "LATEST QA FRAME  : " + (Get-CloudValueAcross @("latestQaFrame", "latest_qa_frame"))
        "WORKER LOG       : " + (Get-CloudValueAcross @("latestWorkerLog", "latest_worker_log"))
    )
}

Export-ModuleMember -Function `
    Resolve-WzhkCloudPython, `
    Get-WzhkCloudCliReadiness, `
    Invoke-WzhkCloudCli, `
    Get-WzhkCloudDashboardLines
