Set-StrictMode -Version Latest

function Find-WzhkBrevExecutable {
    [CmdletBinding()]
    param()

    foreach ($name in @("brev.exe", "brev")) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($null -ne $command) { return [string]$command.Source }
    }
    return ""
}

function Get-WzhkBrevCandidateGpuNames {
    [CmdletBinding()]
    param()

    return @(
        "L40S",
        "L40",
        "RTX 6000 Ada",
        "RTX PRO 6000 Blackwell",
        "RTX PRO Server 6000",
        "RTX 4090",
        "RTX 5090",
        "A40",
        "H100 (optional comparison)",
        "H200 (optional comparison)"
    )
}

function New-WzhkBrevBenchmarkAuthorizationToken {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$PackageSha256,
        [Parameter(Mandatory = $true)][string]$ProfileSha256,
        [Parameter(Mandatory = $true)][ValidateRange(0.01, 1000000.0)][decimal]$MaxBudget
    )

    foreach ($hash in @($PackageSha256, $ProfileSha256)) {
        if ($hash -notmatch '^[A-Fa-f0-9]{64}$') { throw "Package and profile hashes must be complete SHA-256 values." }
    }
    $budgetText = $MaxBudget.ToString("0.00", [Globalization.CultureInfo]::InvariantCulture)
    return "AUTHORIZE BREV BENCHMARK: " + $PackageSha256.Substring(0, 12).ToUpperInvariant() + " | " + $ProfileSha256.Substring(0, 12).ToUpperInvariant() + " | MAX $" + $budgetText
}

function Test-WzhkBrevBenchmarkPlanLock {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedToken,
        [Parameter(Mandatory = $true)][string]$EnteredToken,
        [switch]$PlanLocked,
        [ValidateRange(1, 256)][int]$WorkerCount = 1
    )

    $issues = New-Object System.Collections.Generic.List[string]
    if ($WorkerCount -ne 1) { $issues.Add("A Brev benchmark plan must use exactly one worker.") }
    if (-not $PlanLocked) { $issues.Add("[Y] LOCK CLOUD PLAN was not confirmed.") }
    if ([string]::IsNullOrWhiteSpace($ExpectedToken) -or $EnteredToken -cne $ExpectedToken) { $issues.Add("Exact benchmark authorization token did not match.") }
    return [pscustomobject][ordered]@{
        Valid = ($issues.Count -eq 0)
        WorkerCount = $WorkerCount
        Issues = $issues.ToArray()
        ExpectedToken = $ExpectedToken
        BillableAuthorizationRequested = $false
    }
}

function Test-WzhkBrevBenchmarkAuthorization {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedToken,
        [Parameter(Mandatory = $true)][string]$EnteredToken,
        [switch]$PlanLocked,
        [switch]$FinalConfirmed,
        [ValidateRange(1, 256)][int]$WorkerCount = 1
    )

    $planLock = Test-WzhkBrevBenchmarkPlanLock -ExpectedToken $ExpectedToken -EnteredToken $EnteredToken -WorkerCount $WorkerCount -PlanLocked:$PlanLocked
    $issues = New-Object System.Collections.Generic.List[string]
    foreach ($issue in @($planLock.Issues)) { $issues.Add([string]$issue) }
    if (-not $FinalConfirmed) { $issues.Add("[Y] PROVISION BILLABLE GPU WORKERS was not confirmed.") }
    return [pscustomobject][ordered]@{
        Valid = ($issues.Count -eq 0)
        WorkerCount = $WorkerCount
        Issues = $issues.ToArray()
        ExpectedToken = $ExpectedToken
        BillableAuthorizationRequested = $true
    }
}

function Get-WzhkBrevReadiness {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [switch]$InspectInstalledCli
    )

    $executable = Find-WzhkBrevExecutable
    if ([string]::IsNullOrWhiteSpace($executable)) {
        return [pscustomobject][ordered]@{
            Ready = $false
            Installed = $false
            Inspected = $false
            Executable = ""
            CliVersion = ""
            ProvisioningEnabled = $false
            Detail = "Brev CLI is not installed or not on PATH. No provider command was run."
            Payload = $null
        }
    }
    if (-not $InspectInstalledCli) {
        return [pscustomobject][ordered]@{
            Ready = $false
            Installed = $true
            Inspected = $false
            Executable = $executable
            CliVersion = "not inspected"
            ProvisioningEnabled = $false
            Detail = "Brev CLI was found. Select INSPECT INSTALLED BREV CLI to run only local version/help capability inspection."
            Payload = $null
        }
    }

    $result = Invoke-WzhkCloudCli -RepositoryRoot $RepositoryRoot -Command "brev-readiness" -Arguments @("--executable", $executable)
    $version = "unknown"
    if ($result.Ok -and $null -ne $result.Data) {
        if ($null -ne $result.Data.PSObject.Properties["cli_version"]) { $version = [string]$result.Data.cli_version }
        elseif ($null -ne $result.Data.PSObject.Properties["cliVersion"]) { $version = [string]$result.Data.cliVersion }
    }
    return [pscustomobject][ordered]@{
        Ready = [bool]$result.Ok
        Installed = $true
        Inspected = $true
        Executable = $executable
        CliVersion = $version
        ProvisioningEnabled = $false
        Detail = $(if ($result.Ok) { "Brev CLI capability inspection completed. Provisioning remains disabled." } else { "Brev CLI capability inspection failed closed." })
        Payload = $result.Payload
    }
}

function Get-WzhkBrevSetupGuidance {
    [CmdletBinding()]
    param()

    return @(
        "Install and authenticate the current official NVIDIA Brev CLI outside Mission Control.",
        "Use full GPU VM mode for Blender 5.2; NVIDIA NIM inference containers are not supported.",
        "Return to Mission Control and run INSPECT INSTALLED BREV CLI before discovery.",
        "Run one bounded benchmark worker before approving any fleet.",
        "Keep source audio local and mux it only after verified video-only output returns."
    )
}

Export-ModuleMember -Function `
    Find-WzhkBrevExecutable, `
    Get-WzhkBrevCandidateGpuNames, `
    New-WzhkBrevBenchmarkAuthorizationToken, `
    Test-WzhkBrevBenchmarkPlanLock, `
    Test-WzhkBrevBenchmarkAuthorization, `
    Get-WzhkBrevReadiness, `
    Get-WzhkBrevSetupGuidance
