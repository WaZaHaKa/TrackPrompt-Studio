[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$GcpProjectId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$')]
    [string]$BucketName,

    [ValidateNotNullOrEmpty()]
    [string]$Region = 'us-central1',

    [ValidateNotNullOrEmpty()]
    [string]$BucketLocation = 'US-CENTRAL1',

    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Invoke-Gcloud {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & $script:Gcloud @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud failed with exit code $LASTEXITCODE: gcloud $($Arguments -join ' ')"
    }
}

$gcloudCommand = Get-Command gcloud -ErrorAction SilentlyContinue
if (-not $gcloudCommand) {
    throw 'Google Cloud CLI (gcloud) is required and was not found on PATH.'
}
$script:Gcloud = $gcloudCommand.Source

$activeAccount = (& $script:Gcloud auth list --filter='status:ACTIVE' --format='value(account)' 2>$null | Select-Object -First 1)
if (-not $activeAccount) {
    throw 'No active gcloud account. Run: gcloud auth login'
}

Write-Host "Active account: $activeAccount"
Write-Host "GCP project:    $GcpProjectId"
Write-Host "GCS bucket:    gs://$BucketName"
Write-Host "Vertex region: $Region"

& $script:Gcloud projects describe $GcpProjectId --format='value(projectId)' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "The active account cannot describe project '$GcpProjectId'."
}

$commands = @(
    @('config', 'set', 'project', $GcpProjectId),
    @('services', 'enable', 'aiplatform.googleapis.com', 'storage.googleapis.com', "--project=$GcpProjectId")
)

$bucketExists = $false
& $script:Gcloud storage buckets describe "gs://$BucketName" --format='value(name)' *> $null
if ($LASTEXITCODE -eq 0) {
    $bucketExists = $true
}

if (-not $bucketExists) {
    $commands += ,@(
        'storage', 'buckets', 'create', "gs://$BucketName",
        "--project=$GcpProjectId",
        "--location=$BucketLocation",
        '--uniform-bucket-level-access'
    )
}

if (-not $Apply) {
    Write-Host ''
    Write-Host 'Read-only setup preview. No API was enabled and no bucket was created.' -ForegroundColor Yellow
    foreach ($command in $commands) {
        Write-Host ("gcloud " + ($command -join ' '))
    }
    Write-Host ''
    Write-Host 'Run again with -Apply to perform these bounded setup actions.'
    return
}

foreach ($command in $commands) {
    if ($PSCmdlet.ShouldProcess($GcpProjectId, "gcloud $($command -join ' ')")) {
        Invoke-Gcloud -Arguments $command
    }
}

Write-Host ''
Write-Host 'GCP fast-lane setup completed.' -ForegroundColor Green
Write-Host 'No Veo generation request was submitted.'
Write-Host 'No service-account key was created or written to disk.'
