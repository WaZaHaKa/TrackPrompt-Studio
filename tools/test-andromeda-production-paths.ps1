[CmdletBinding()]
param(
    [string]$PythonExecutable = "",
    [Alias("HorizontalRenderProfilePath")]
    [string]$RenderProfilePath = "",
    [string]$VerticalRenderProfilePath = "",
    [string]$HorizontalScenePath = "",
    [string]$VerticalScenePath = "",
    [switch]$KeepHarnessFiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$sourceWrapper = Join-Path (
    $repositoryRoot
) "production\andromeda-v2\invoke-production.ps1"
$harnessRoot = Join-Path (
    [IO.Path]::GetTempPath()
) ("trackprompt-andromeda-path-harness-{0}" -f [Guid]::NewGuid().ToString("N"))

function Resolve-RequiredHarnessFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is unavailable: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Assert-Equal {
    param(
        [AllowNull()][object]$Actual,
        [AllowNull()][object]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ([string]$Actual -cne [string]$Expected) {
        throw "$Label mismatch. Expected '$Expected'; found '$Actual'."
    }
}

function New-HarnessInput {
    param(
        [AllowEmptyString()][string]$SuppliedPath,
        [Parameter(Mandatory = $true)][string]$DefaultPath,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not [string]::IsNullOrWhiteSpace($SuppliedPath)) {
        return Resolve-RequiredHarnessFile -Path $SuppliedPath -Label $Label
    }
    $parent = Split-Path -Parent $DefaultPath
    $null = New-Item -ItemType Directory -Path $parent -Force
    [IO.File]::WriteAllText(
        $DefaultPath,
        "$Label harness fixture`n",
        [Text.UTF8Encoding]::new($false)
    )
    return (Resolve-Path -LiteralPath $DefaultPath).Path
}

$harnessSucceeded = $false
try {
    $wrapperDirectory = Join-Path $harnessRoot "production\andromeda-v2"
    $toolsDirectory = Join-Path $harnessRoot "tools"
    $null = New-Item -ItemType Directory -Path $wrapperDirectory -Force
    $null = New-Item -ItemType Directory -Path $toolsDirectory -Force
    Copy-Item -LiteralPath $sourceWrapper -Destination (
        Join-Path $wrapperDirectory "invoke-production.ps1"
    )

    $stubRenderer = @'
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ApprovedScenePath,
    [Parameter(Mandatory = $true)][string]$RenderProfilePath,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$AuthorizationToken = "",
    [switch]$DryRun,
    [switch]$Preflight,
    [string]$MissionControlJobId = "",
    [string]$BlenderExecutable = "",
    [string]$PythonExecutable = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (-not $DryRun -and -not $Preflight) {
    throw "The path harness forbids StartOrResume."
}
$mode = if ($Preflight) { "Preflight" } else { "Inspect" }
[pscustomobject]@{
    schemaVersion = "1.0.0"
    kind = "trackprompt-andromeda-path-harness-plan"
    mode = $mode
    approvedScenePath = (Resolve-Path -LiteralPath $ApprovedScenePath).Path
    renderProfilePath = (Resolve-Path -LiteralPath $RenderProfilePath).Path
    outputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
    missionControlJobId = $MissionControlJobId
    blenderExecutable = $BlenderExecutable
    productionRenderStarted = $false
    encodeStarted = $false
} | ConvertTo-Json -Compress
'@
    [IO.File]::WriteAllText(
        (Join-Path $harnessRoot "render-trackprompt-final.ps1"),
        $stubRenderer,
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllText(
        (Join-Path $toolsDirectory "andromeda_operator_authorization.py"),
        "# Path harness placeholder; the Preflight action must not invoke this file.`n",
        [Text.UTF8Encoding]::new($false)
    )

    $freshRoot = Join-Path $harnessRoot "test-output\fresh-release"
    $resolvedHorizontalProfile = New-HarnessInput `
        -SuppliedPath $RenderProfilePath `
        -DefaultPath (
            Join-Path $freshRoot (
                "profiles\andromeda-v2-horizontal-1080p-final-r14-harness.json"
            )
        ) `
        -Label "Fresh horizontal render profile"
    $resolvedVerticalProfile = New-HarnessInput `
        -SuppliedPath $VerticalRenderProfilePath `
        -DefaultPath (
            Join-Path $freshRoot (
                "profiles\andromeda-v2-vertical-1080x1920-final-r14-harness.json"
            )
        ) `
        -Label "Fresh vertical render profile"
    $resolvedHorizontalScene = New-HarnessInput `
        -SuppliedPath $HorizontalScenePath `
        -DefaultPath (
            Join-Path $freshRoot "scenes\andromeda-v2-master-horizontal.blend"
        ) `
        -Label "Fresh horizontal scene"
    $resolvedVerticalScene = New-HarnessInput `
        -SuppliedPath $VerticalScenePath `
        -DefaultPath (
            Join-Path $freshRoot "scenes\andromeda-v2-master-vertical.blend"
        ) `
        -Label "Fresh vertical scene"

    if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
        $repositoryPython = Join-Path (
            $repositoryRoot
        ) "backend\.venv\Scripts\python.exe"
        if (Test-Path -LiteralPath $repositoryPython -PathType Leaf) {
            $PythonExecutable = (Resolve-Path -LiteralPath $repositoryPython).Path
        }
        else {
            $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
            if ($null -eq $pythonCommand) {
                throw "Python was not found for the path harness."
            }
            $PythonExecutable = $pythonCommand.Source
        }
    }
    $resolvedPython = Resolve-RequiredHarnessFile `
        -Path $PythonExecutable `
        -Label "Python executable"
    $powerShellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if ($null -eq $powerShellCommand) {
        throw "Windows PowerShell 5.1 was not found for the path harness."
    }

    $wrapperPath = Join-Path $wrapperDirectory "invoke-production.ps1"
    $captured = @(
        & $powerShellCommand.Source `
            -NoProfile `
            -ExecutionPolicy Bypass `
            -File $wrapperPath `
            -Action Preflight `
            -EnableVertical `
            -ScenePath $resolvedHorizontalScene `
            -VerticalScenePath $resolvedVerticalScene `
            -RenderProfilePath $resolvedHorizontalProfile `
            -VerticalRenderProfilePath $resolvedVerticalProfile `
            -OutputDirectory (Join-Path $harnessRoot "output\horizontal") `
            -VerticalOutputDirectory (Join-Path $harnessRoot "output\vertical") `
            -BlenderExecutable "HARNESS-MUST-NOT-START-BLENDER" `
            -PythonExecutable $resolvedPython `
            2>&1
    )
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw (
            "Production-wrapper path harness failed with exit code {0}. {1}" -f `
                $exitCode,
                (@($captured | ForEach-Object { [string]$_ }) -join [Environment]::NewLine)
        )
    }
    $payload = (
        @($captured | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
    ) | ConvertFrom-Json

    Assert-Equal -Actual $payload.action -Expected "Preflight" -Label "Wrapper action"
    Assert-Equal `
        -Actual $payload.productionStartAttempted `
        -Expected $false `
        -Label "Production-start flag"
    Assert-Equal `
        -Actual @($payload.targets).Count `
        -Expected 2 `
        -Label "Enabled target count"

    $horizontal = @(
        $payload.targets |
            Where-Object { [string]$_.variantId -eq "horizontal-16x9-1080p" }
    )
    $vertical = @(
        $payload.targets |
            Where-Object { [string]$_.variantId -eq "vertical-9x16-1080p" }
    )
    Assert-Equal -Actual $horizontal.Count -Expected 1 -Label "Horizontal target"
    Assert-Equal -Actual $vertical.Count -Expected 1 -Label "Vertical target"
    Assert-Equal `
        -Actual $horizontal[0].renderProfilePath `
        -Expected $resolvedHorizontalProfile `
        -Label "Horizontal wrapper profile"
    Assert-Equal `
        -Actual $horizontal[0].plan.renderProfilePath `
        -Expected $resolvedHorizontalProfile `
        -Label "Horizontal low-level profile"
    Assert-Equal `
        -Actual $vertical[0].renderProfilePath `
        -Expected $resolvedVerticalProfile `
        -Label "Vertical wrapper profile"
    Assert-Equal `
        -Actual $vertical[0].plan.renderProfilePath `
        -Expected $resolvedVerticalProfile `
        -Label "Vertical low-level profile"
    foreach ($target in @($horizontal[0], $vertical[0])) {
        Assert-Equal `
            -Actual $target.plan.mode `
            -Expected "Preflight" `
            -Label "$($target.variantId) low-level mode"
        Assert-Equal `
            -Actual $target.plan.productionRenderStarted `
            -Expected $false `
            -Label "$($target.variantId) render-start flag"
        Assert-Equal `
            -Actual $target.plan.encodeStarted `
            -Expected $false `
            -Label "$($target.variantId) encode-start flag"
        Assert-Equal `
            -Actual $target.plan.blenderExecutable `
            -Expected "HARNESS-MUST-NOT-START-BLENDER" `
            -Label "$($target.variantId) Blender sentinel"
    }

    $harnessSucceeded = $true
    [pscustomobject]@{
        ok = $true
        schemaVersion = "1.0.0"
        kind = "trackprompt-andromeda-production-path-harness"
        action = "Preflight"
        enabledVariantIds = @(
            "horizontal-16x9-1080p",
            "vertical-9x16-1080p"
        )
        horizontalRenderProfilePath = $resolvedHorizontalProfile
        verticalRenderProfilePath = $resolvedVerticalProfile
        productionRenderStarted = $false
        blenderStarted = $false
        encodeStarted = $false
    } | ConvertTo-Json -Depth 10
}
finally {
    if (-not $KeepHarnessFiles -and (Test-Path -LiteralPath $harnessRoot)) {
        $resolvedHarnessRoot = [IO.Path]::GetFullPath($harnessRoot)
        $temporaryPrefix = (
            [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
        )
        if (
            -not $resolvedHarnessRoot.StartsWith(
                $temporaryPrefix,
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            -not (Split-Path -Leaf $resolvedHarnessRoot).StartsWith(
                "trackprompt-andromeda-path-harness-",
                [StringComparison]::Ordinal
            )
        ) {
            throw "Refusing to remove an unexpected path-harness directory."
        }
        Remove-Item -LiteralPath $resolvedHarnessRoot -Recurse -Force
    }
    if (-not $harnessSucceeded -and $KeepHarnessFiles) {
        Write-Warning "Path harness files were preserved at $harnessRoot"
    }
}
