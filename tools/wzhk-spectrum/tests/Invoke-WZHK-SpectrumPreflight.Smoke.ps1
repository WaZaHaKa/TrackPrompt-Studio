#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-True {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Condition,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not $Condition) {
        throw "Assertion failed: $Message"
    }
}

function Invoke-PreflightProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PowerShellPath,

        [Parameter(Mandatory = $true)]
        [string]$PreflightPath,

        [Parameter(Mandatory = $true)]
        [string]$FixtureRoot
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $commandOutput = @(
            & $PowerShellPath -NoProfile -ExecutionPolicy Bypass `
                -File $PreflightPath -ProjectRoot $FixtureRoot 2>&1
        )
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = ($commandOutput | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
    }
}

$spectrumRoot = Split-Path -Parent $PSScriptRoot
$preflightPath = Join-Path $spectrumRoot 'scripts\Invoke-WZHK-SpectrumPreflight.ps1'
$powerShellCommand = Get-Command pwsh -ErrorAction SilentlyContinue
if ($null -eq $powerShellCommand) {
    $powerShellCommand = Get-Command powershell -ErrorAction Stop
}

$fixtureRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    'trackprompt-wzhk-spectrum-preflight-' + [guid]::NewGuid().ToString('N')
)

try {
    $fixtureDirectories = @(
        'vendor\wzhk-spectrum-visualizer\@Resources'
        'tools\wzhk-spectrum\config'
        'tools\wzhk-spectrum\runtime\shaders'
        'tools\wzhk-spectrum'
        '.trackprompt-data\wzhk-spectrum\assets\logo'
        '.trackprompt-data\wzhk-spectrum\assets\track'
    )
    foreach ($relativeDirectory in $fixtureDirectories) {
        New-Item -ItemType Directory -Path (Join-Path $fixtureRoot $relativeDirectory) -Force |
            Out-Null
    }

    $fixtureFiles = @(
        'AGENTS.md'
        'vendor\wzhk-spectrum-visualizer\visualizer.ini'
        'vendor\wzhk-spectrum-visualizer\@Resources\variables.ini'
        'vendor\wzhk-spectrum-visualizer\LICENSE'
        'tools\wzhk-spectrum\CODEX_TASK.md'
    )
    foreach ($relativeFile in $fixtureFiles) {
        Set-Content -LiteralPath (Join-Path $fixtureRoot $relativeFile) `
            -Value 'synthetic preflight fixture' -Encoding UTF8
    }

    $contract = @{
        project = @{
            artist = 'DJ WaZaHaKa'
            title = 'Scattered'
        }
        track = @{
            bpm = 120
            timeSignature = @{
                numerator = 4
                denominator = 4
            }
            totalBars = 96
            gridDurationSeconds = 192
        }
        sections = @(
            @{
                id = 'intro'
                label = 'Intro'
                startBarInclusive = 1
                endBarExclusive = 33
                startSeconds = 0
                endSeconds = 64
            }
            @{
                id = 'main'
                label = 'Main'
                startBarInclusive = 33
                endBarExclusive = 89
                startSeconds = 64
                endSeconds = 176
            }
            @{
                id = 'outro'
                label = 'Outro'
                startBarInclusive = 89
                endBarExclusive = 97
                startSeconds = 176
                endSeconds = 192
            }
        )
    }
    $contract | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath (
            Join-Path $fixtureRoot 'tools\wzhk-spectrum\config\scattered.wzhk-spectrum.json'
        ) -Encoding UTF8

    Copy-Item -LiteralPath (
        Join-Path $spectrumRoot 'config\scattered.visual-preset.json'
    ) -Destination (
        Join-Path $fixtureRoot 'tools\wzhk-spectrum\config\scattered.visual-preset.json'
    )

    Copy-Item -Path (Join-Path $spectrumRoot 'runtime\*') -Destination (
        Join-Path $fixtureRoot 'tools\wzhk-spectrum\runtime'
    ) -Recurse -Force

    $provenance = @{
        repository = 'https://example.invalid/synthetic-visualizer.git'
        commit = '553aa755ef0cc394259fb1a55560f1b31864d2e0'
        vendoredWithoutGitMetadata = $true
    }
    $provenance | ConvertTo-Json |
        Set-Content -LiteralPath (
            Join-Path $fixtureRoot 'vendor\wzhk-spectrum-visualizer\UPSTREAM-SOURCE.json'
        ) -Encoding UTF8

    $validResult = Invoke-PreflightProcess `
        -PowerShellPath $powerShellCommand.Source `
        -PreflightPath $preflightPath -FixtureRoot $fixtureRoot

    Assert-True -Condition ($validResult.ExitCode -eq 0) `
        -Message "An empty initial check collection must accept its first item. Output: $($validResult.Output)"
    Assert-True -Condition ($validResult.Output -notmatch 'Cannot bind argument to parameter') `
        -Message 'The empty-collection parameter-binding failure must not recur.'
    Assert-True -Condition ($validResult.Output -match 'TrackPrompt AGENTS.md') `
        -Message 'The first required check must be retained.'
    Assert-True -Condition ($validResult.Output -match 'Checks evaluated: 19') `
        -Message 'All 19 required, runtime, contract, design, provenance, and optional checks must be retained.'
    Assert-True -Condition ($validResult.Output -match 'Scattered visual design') `
        -Message 'The section-aware visual-preset validation must be retained.'
    Assert-True -Condition ($validResult.Output -match 'WZHK logo available:') `
        -Message 'The missing logo must emit its own optional warning.'
    Assert-True -Condition ($validResult.Output -match 'Scattered master available:') `
        -Message 'The missing master track must emit its own optional warning.'
    Assert-True -Condition (
        $validResult.Output -match 'Status: Scaffold valid, assets still required'
    ) -Message 'Missing optional assets must produce the asset-warning status.'

    $fixturePresetPath = Join-Path $fixtureRoot 'tools\wzhk-spectrum\config\scattered.visual-preset.json'
    $originalPresetText = Get-Content -LiteralPath $fixturePresetPath -Raw
    foreach ($invalidComposition in @('production-bars', 'string-boolean', 'wide-mask', 'blackout', 'tail-not-resolved')) {
        $changedPreset = $originalPresetText | ConvertFrom-Json
        switch ($invalidComposition) {
            'production-bars' { $changedPreset.composition.production.spectrumBarsVisible = $true }
            'string-boolean' { $changedPreset.composition.production.technicalMetadataVisible = 'false' }
            'wide-mask' { $changedPreset.composition.readability.zones[0].radius[0] = 0.8 }
            'blackout' { $changedPreset.composition.readability.minimumBrightness = 0 }
            'tail-not-resolved' { $changedPreset.composition.envelope[-1].brightness = 0.5 }
        }
        $changedPreset | ConvertTo-Json -Depth 30 |
            Set-Content -LiteralPath $fixturePresetPath -Encoding UTF8
        $compositionResult = Invoke-PreflightProcess `
            -PowerShellPath $powerShellCommand.Source `
            -PreflightPath $preflightPath -FixtureRoot $fixtureRoot
        Assert-True -Condition ($compositionResult.ExitCode -ne 0) `
            -Message "Invalid geometry-first composition must fail: $invalidComposition"
        Assert-True -Condition ($compositionResult.Output -match 'Scattered visual design') `
            -Message 'Invalid composition must be reported through the visual design check.'
    }
    Set-Content -LiteralPath $fixturePresetPath -Value $originalPresetText -Encoding UTF8

    $missingVendorFile = Join-Path (
        $fixtureRoot
    ) 'vendor\wzhk-spectrum-visualizer\@Resources\variables.ini'
    Remove-Item -LiteralPath $missingVendorFile -Force

    $invalidResult = Invoke-PreflightProcess `
        -PowerShellPath $powerShellCommand.Source `
        -PreflightPath $preflightPath -FixtureRoot $fixtureRoot

    Assert-True -Condition ($invalidResult.ExitCode -ne 0) `
        -Message 'A missing required vendor file must produce a non-zero exit code.'
    Assert-True -Condition ($invalidResult.Output -match 'Vendor variables.ini') `
        -Message 'The missing required vendor file check must be retained in output.'
    Assert-True -Condition ($invalidResult.Output -match 'Status: Scaffold invalid') `
        -Message 'A required failure must report the scaffold-invalid status.'

    Write-Host 'PASS: WZHK Spectrum preflight regression smoke checks passed.' -ForegroundColor Green
}
finally {
    if (Test-Path -LiteralPath $fixtureRoot -PathType Container) {
        $resolvedFixtureRoot = (Resolve-Path -LiteralPath $fixtureRoot).Path
        $resolvedTempRoot = (Resolve-Path -LiteralPath ([System.IO.Path]::GetTempPath())).Path.TrimEnd('\') + '\'
        if (
            -not $resolvedFixtureRoot.StartsWith($resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
            (Split-Path -Leaf $resolvedFixtureRoot) -notmatch '^trackprompt-wzhk-spectrum-preflight-[a-f0-9]{32}$'
        ) {
            throw 'Refusing to clean a preflight fixture outside its exact temporary scope.'
        }
        Remove-Item -LiteralPath $resolvedFixtureRoot -Recurse -Force
    }
}
