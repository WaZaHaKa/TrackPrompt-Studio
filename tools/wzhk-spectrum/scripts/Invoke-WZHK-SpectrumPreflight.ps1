#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ProjectRoot = 'C:\Users\theon\GitHub\TrackPrompt-Studio'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Checks = [System.Collections.Generic.List[object]]::new()

function Add-Check {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][bool]$Passed,
        [Parameter(Mandatory = $true)][bool]$Required,
        [Parameter(Mandatory = $true)][string]$Detail
    )

    [void]$script:Checks.Add([pscustomobject]@{
        Check = $Name
        Passed = $Passed
        Required = $Required
        Detail = $Detail
    })
}

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "TrackPrompt Studio root does not exist: $ProjectRoot"
}

$root = (Resolve-Path -LiteralPath $ProjectRoot).Path

$requiredPaths = [ordered]@{
    'TrackPrompt AGENTS.md' = 'AGENTS.md'
    'Vendor visualizer root' = 'vendor\wzhk-spectrum-visualizer'
    'Vendor visualizer.ini' = 'vendor\wzhk-spectrum-visualizer\visualizer.ini'
    'Vendor variables.ini' = 'vendor\wzhk-spectrum-visualizer\@Resources\variables.ini'
    'Upstream MIT license' = 'vendor\wzhk-spectrum-visualizer\LICENSE'
    'Upstream provenance' = 'vendor\wzhk-spectrum-visualizer\UPSTREAM-SOURCE.json'
    'Scattered contract' = 'tools\wzhk-spectrum\config\scattered.wzhk-spectrum.json'
    'Scattered visual preset' = 'tools\wzhk-spectrum\config\scattered.visual-preset.json'
    'Geometry runtime HTML' = 'tools\wzhk-spectrum\runtime\index.html'
    'Geometry runtime JavaScript' = 'tools\wzhk-spectrum\runtime\runtime.js'
    'Geometry vertex shader' = 'tools\wzhk-spectrum\runtime\shaders\neopixel.vert.glsl'
    'Geometry fragment shader' = 'tools\wzhk-spectrum\runtime\shaders\neopixel.frag.glsl'
    'Codex task' = 'tools\wzhk-spectrum\CODEX_TASK.md'
}

foreach ($entry in $requiredPaths.GetEnumerator()) {
    $path = Join-Path $root $entry.Value
    $pathType = if ($entry.Key -eq 'Vendor visualizer root') {
        'Container'
    }
    else {
        'Leaf'
    }

    Add-Check `
        -Name $entry.Key `
        -Passed (Test-Path -LiteralPath $path -PathType $pathType) `
        -Required $true `
        -Detail $path
}

$visualPresetPath = Join-Path $root 'tools\wzhk-spectrum\config\scattered.visual-preset.json'
if (Test-Path -LiteralPath $visualPresetPath -PathType Leaf) {
    try {
        $visualPreset = Get-Content -LiteralPath $visualPresetPath -Raw | ConvertFrom-Json
        $visualSections = @($visualPreset.sections)
        $visualSectionRanges = @(
            [pscustomobject]@{ Id = 'intro'; StartSeconds = 0; EndSeconds = 64 }
            [pscustomobject]@{ Id = 'main'; StartSeconds = 64; EndSeconds = 176 }
            [pscustomobject]@{ Id = 'outro'; StartSeconds = 176; EndSeconds = 192 }
        )
        $visualSectionsValid = $visualSections.Count -eq 3
        if ($visualSectionsValid) {
            for ($index = 0; $index -lt $visualSections.Count; $index++) {
                $visualSection = $visualSections[$index]
                $expectedRange = $visualSectionRanges[$index]
                $visualSectionValid = (
                    [string]$visualSection.id -ceq $expectedRange.Id -and
                    [double]$visualSection.startSeconds -eq $expectedRange.StartSeconds -and
                    [double]$visualSection.endSeconds -eq $expectedRange.EndSeconds -and
                    [string]$visualSection.state.spectrumColor -match '^#[0-9A-Fa-f]{6}$' -and
                    [double]$visualSection.state.sensitivity -ge 20 -and
                    [double]$visualSection.state.sensitivity -le 60 -and
                    [double]$visualSection.state.fragmentDensity -ge 0 -and
                    [double]$visualSection.state.fragmentDensity -le 1 -and
                    [double]$visualSection.state.fragmentMotion -ge 0 -and
                    [double]$visualSection.state.fragmentMotion -le 1 -and
                    [double]$visualSection.state.lineIntensity -ge 0 -and
                    [double]$visualSection.state.lineIntensity -le 1
                )
                if (-not $visualSectionValid) {
                    $visualSectionsValid = $false
                }
            }
        }

        $spectrumWidth = (
            [int]$visualPreset.spectrum.barCount * [int]$visualPreset.spectrum.barWidth +
            ([int]$visualPreset.spectrum.barCount - 1) * [int]$visualPreset.spectrum.barGap
        )
        $postGridTailValid = (
            [string]$visualPreset.postGridTail.id -ceq 'post-grid-tail' -and
            [double]$visualPreset.postGridTail.startSeconds -eq 192 -and
            [string]$visualPreset.postGridTail.state.spectrumColor -match '^#[0-9A-Fa-f]{6}$' -and
            [string]$visualPreset.postGridTail.endState.spectrumColor -match '^#[0-9A-Fa-f]{6}$' -and
            [double]$visualPreset.postGridTail.state.fragmentDensity -ge 0 -and
            [double]$visualPreset.postGridTail.state.fragmentDensity -le 1 -and
            [double]$visualPreset.postGridTail.endState.fragmentDensity -ge 0 -and
            [double]$visualPreset.postGridTail.endState.fragmentDensity -le 1
        )
        $fragmentFieldValid = (
            [int64]$visualPreset.background.fragmentSeed -ge 0 -and
            [int64]$visualPreset.background.fragmentSeed -le 2147483647 -and
            [int]$visualPreset.background.fragmentCount -ge 12 -and
            [int]$visualPreset.background.fragmentCount -le 36 -and
            [int]$visualPreset.background.depthLayers -eq 3 -and
            [double]$visualPreset.background.maxMotionPixels -ge 0 -and
            [double]$visualPreset.background.maxMotionPixels -le 18 -and
            [double]$visualPreset.background.strokeWidth -ge 0.5 -and
            [double]$visualPreset.background.strokeWidth -le 3
        )
        $geometry = $visualPreset.generativeGeometry
        $geometryShapes = @($geometry.shapeLibrary)
        $geometryProfiles = @($geometry.performanceProfiles)
        $geometryTransitions = @($geometry.choreography.transitions)
        $trustedShapePattern = '^(sparse-field|lissajous|matrix-field|wave-surface|torus|twisted-torus|trefoil-knot|superformula|spherical-lattice|dispersed-field)$'
        $geometryShapesValid = $geometryShapes.Count -ge 6
        foreach ($shape in $geometryShapes) {
            if ([string]$shape -notmatch $trustedShapePattern) {
                $geometryShapesValid = $false
            }
        }
        $geometryTransitionsValid = $geometryTransitions.Count -ge 6
        foreach ($transition in $geometryTransitions) {
            if (
                [string]$transition.shapeA.shapeId -notmatch $trustedShapePattern -or
                [string]$transition.shapeB.shapeId -notmatch $trustedShapePattern -or
                [double]$transition.duration.value -le 0
            ) {
                $geometryTransitionsValid = $false
            }
        }
        $geometryValid = (
            [string]$visualPreset.background.mode -ceq 'generative-geometry' -and
            [string]$geometry.schemaVersion -ceq '1.0.0' -and
            [string]$geometry.subsystemId -ceq 'wzhk-generative-geometry' -and
            [bool]$geometry.enabled -and
            [string]$geometry.renderMode -ceq 'neopixel-points' -and
            [string]$geometry.fallbackMode -ceq 'static-structured' -and
            [int64]$geometry.seed -ge 0 -and
            [int64]$geometry.seed -le 2147483647 -and
            [int]$geometry.pointDomain.pointCount -ge 1024 -and
            [int]$geometry.pointDomain.pointCount -le 8192 -and
            [int]$geometry.pointDomain.columns -ge 2 -and
            $geometryProfiles.Count -eq 3 -and
            $geometryShapesValid -and
            $geometryTransitionsValid -and
            [double]$geometry.choreography.bpm -eq 120 -and
            [int]$geometry.choreography.beatsPerBar -eq 4 -and
            [double]$geometry.choreography.gridDurationSeconds -eq 192 -and
            [double]$geometry.choreography.masterDurationSeconds -ge 192
        )
        $composition = $visualPreset.composition
        $productionElements = $composition.production
        $productionElementsValid = $true
        foreach ($name in @('logoVisible', 'artistVisible', 'titleVisible')) {
            if ($productionElements.$name -isnot [bool] -or $productionElements.$name -ne $true) {
                $productionElementsValid = $false
            }
        }
        foreach ($name in @('spectrumBarsVisible', 'spectralRibbonVisible', 'technicalMetadataVisible', 'sectionLabelsVisible')) {
            if ($productionElements.$name -isnot [bool] -or $productionElements.$name -ne $false) {
                $productionElementsValid = $false
            }
        }
        $readability = $composition.readability
        $zones = @($readability.zones)
        $zoneIds = @($zones | ForEach-Object { [string]$_.id } | Sort-Object -Unique)
        $readabilityValid = (
            [string]$readability.mode -ceq 'soft-ellipses' -and
            [double]$readability.minimumBrightness -ge 0.25 -and
            [double]$readability.minimumBrightness -le 1 -and
            [double]$readability.haloSuppression -ge 0 -and
            [double]$readability.haloSuppression -le 1 -and
            $zones.Count -eq 2 -and
            $zoneIds.Count -eq 2 -and
            $zoneIds -ccontains 'logo' -and $zoneIds -ccontains 'identity'
        )
        foreach ($zone in $zones) {
            $zoneValid = (
                @($zone.center).Count -eq 2 -and @($zone.radius).Count -eq 2 -and
                [double]$zone.strength -ge 0 -and [double]$zone.strength -le 0.75
            )
            foreach ($coordinate in @($zone.center)) {
                $zoneValid = $zoneValid -and ([double]$coordinate -ge 0 -and [double]$coordinate -le 1)
            }
            foreach ($radius in @($zone.radius)) {
                $zoneValid = $zoneValid -and ([double]$radius -ge 0.02 -and [double]$radius -le 0.25)
            }
            $readabilityValid = $readabilityValid -and $zoneValid
        }
        $framing = $composition.framing
        $framingValid = (
            @($framing.center).Count -eq 2 -and
            [double]$framing.shapeScale -ge 0.25 -and [double]$framing.shapeScale -le 2 -and
            [double]$framing.depthStrength -ge 0 -and [double]$framing.depthStrength -le 1
        )
        foreach ($coordinate in @($framing.center)) {
            $framingValid = $framingValid -and ([double]$coordinate -ge 0 -and [double]$coordinate -le 1)
        }
        $envelope = @($composition.envelope)
        $envelopeValid = (
            $envelope.Count -ge 2 -and $envelope.Count -le 64 -and
            [double]$envelope[0].timeSeconds -eq 0 -and
            [math]::Abs([double]$envelope[-1].timeSeconds - 196.619796) -le 0.000001 -and
            [double]$envelope[-1].density -eq 0 -and [double]$envelope[-1].brightness -eq 0
        )
        $previousEnvelopeTime = -1.0
        foreach ($point in $envelope) {
            $envelopeValid = $envelopeValid -and (
                [double]$point.timeSeconds -gt $previousEnvelopeTime -and
                [double]$point.timeSeconds -le 196.619796 -and
                [double]$point.density -ge 0 -and [double]$point.density -le 1 -and
                [double]$point.brightness -ge 0 -and [double]$point.brightness -le 1 -and
                [double]$point.scale -ge 0.25 -and [double]$point.scale -le 2 -and
                [double]$point.deformation -ge 0 -and [double]$point.deformation -le 2
            )
            $previousEnvelopeTime = [double]$point.timeSeconds
        }
        $compositionValid = (
            [string]$composition.schemaVersion -ceq '1.0.0' -and
            [string]$composition.revision -ceq 'scattered-geometry-first-3.7' -and
            [string]$composition.geometryCoverage -ceq 'full-frame' -and
            $productionElementsValid -and $readabilityValid -and $framingValid -and $envelopeValid
        )
        $visualPresetChecks = @(
            ([string]$visualPreset.schemaVersion -ceq '3.1.0')
            ([string]$visualPreset.presetId -ceq 'scattered')
            ([int]$visualPreset.render.width -eq 1920)
            ([int]$visualPreset.render.height -eq 1080)
            ([int]$visualPreset.render.fps -eq 60)
            ([int]$visualPreset.spectrum.barCount -ge 24)
            ([int]$visualPreset.spectrum.barCount -le 100)
            (([int]$visualPreset.spectrum.x + $spectrumWidth) -le (
                [int]$visualPreset.render.width - [int]$visualPreset.render.safeMargin
            ))
            ([string]$visualPreset.controller.previewTimingSource -ceq 'external-media-player-position')
            ([string]$visualPreset.controller.productionTimingSource -ceq 'trackprompt-production-clock')
            ([string]$visualPreset.controller.previewAccuracy -ceq 'preview-level')
            ([string]$visualPreset.controller.productionAccuracy -ceq 'host-monotonic-process-boundary')
            ([double]$visualPreset.transitions.finalFadeSeconds -eq 4)
            $fragmentFieldValid
            $geometryValid
            $compositionValid
            $postGridTailValid
            $visualSectionsValid
        )
        $visualPresetValid = $visualPresetChecks -notcontains $false

        Add-Check `
            -Name 'Scattered visual design' `
            -Passed $visualPresetValid `
            -Required $true `
            -Detail 'Expected a typed 1920x1080/60 geometry-first Scattered preset, identity-only production, bounded local soft masks, full-frame 4096-point WebGL2 choreography, exact 0/64/176/192 grid plus resolving media tail, retained static fallback, and TrackPrompt production clock.'
    }
    catch {
        Add-Check `
            -Name 'Scattered visual design' `
            -Passed $false `
            -Required $true `
            -Detail $_.Exception.Message
    }
}
else {
    Add-Check `
        -Name 'Scattered visual design' `
        -Passed $false `
        -Required $true `
        -Detail "Visual preset file is missing: $visualPresetPath"
}

$nestedGit = Join-Path $root 'vendor\wzhk-spectrum-visualizer\.git'

Add-Check `
    -Name 'No nested Git metadata' `
    -Passed (-not (Test-Path -LiteralPath $nestedGit)) `
    -Required $true `
    -Detail $nestedGit

$contractPath = Join-Path $root 'tools\wzhk-spectrum\config\scattered.wzhk-spectrum.json'

if (Test-Path -LiteralPath $contractPath -PathType Leaf) {
    try {
        $contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
        $sections = @($contract.sections)
        $expectedSections = @(
            [pscustomobject]@{
                Id = 'intro'
                Label = 'Intro'
                StartBar = 1
                EndBar = 33
                StartSeconds = 0
                EndSeconds = 64
            }
            [pscustomobject]@{
                Id = 'main'
                Label = 'Main'
                StartBar = 33
                EndBar = 89
                StartSeconds = 64
                EndSeconds = 176
            }
            [pscustomobject]@{
                Id = 'outro'
                Label = 'Outro'
                StartBar = 89
                EndBar = 97
                StartSeconds = 176
                EndSeconds = 192
            }
        )

        $sectionsValid = $sections.Count -eq $expectedSections.Count
        $nextExpectedBar = 1
        $coveredBars = 0

        if ($sectionsValid) {
            for ($index = 0; $index -lt $sections.Count; $index++) {
                $section = $sections[$index]
                $expected = $expectedSections[$index]
                $startBar = [int]$section.startBarInclusive
                $endBar = [int]$section.endBarExclusive

                $sectionValid = (
                    [string]$section.id -ceq $expected.Id -and
                    [string]$section.label -ceq $expected.Label -and
                    $startBar -eq $expected.StartBar -and
                    $endBar -eq $expected.EndBar -and
                    [double]$section.startSeconds -eq $expected.StartSeconds -and
                    [double]$section.endSeconds -eq $expected.EndSeconds -and
                    $startBar -eq $nextExpectedBar -and
                    $endBar -gt $startBar
                )

                $sectionsValid = $sectionsValid -and $sectionValid
                $coveredBars += $endBar - $startBar
                $nextExpectedBar = $endBar
            }
        }

        $sectionsValid = (
            $sectionsValid -and
            $coveredBars -eq 96 -and
            $nextExpectedBar -eq 97
        )

        $timingValid = (
            [string]$contract.project.artist -ceq 'DJ WaZaHaKa' -and
            [string]$contract.project.title -ceq 'Scattered' -and
            [double]$contract.track.bpm -eq 120 -and
            [int]$contract.track.timeSignature.numerator -eq 4 -and
            [int]$contract.track.timeSignature.denominator -eq 4 -and
            [int]$contract.track.totalBars -eq 96 -and
            [double]$contract.track.gridDurationSeconds -eq 192 -and
            $coveredBars -eq [int]$contract.track.totalBars -and
            $sectionsValid
        )

        Add-Check `
            -Name 'Scattered timing contract' `
            -Passed $timingValid `
            -Required $true `
            -Detail 'Expected DJ WaZaHaKa - Scattered, 120 BPM, 4/4, 96 bars, a 192-second musical grid, and ranges [1,33), [33,89), [89,97).'
    }
    catch {
        Add-Check `
            -Name 'Scattered timing contract' `
            -Passed $false `
            -Required $true `
            -Detail $_.Exception.Message
    }
}
else {
    Add-Check `
        -Name 'Scattered timing contract' `
        -Passed $false `
        -Required $true `
        -Detail "Contract file is missing: $contractPath"
}

$expectedUpstreamCommit = '553aa755ef0cc394259fb1a55560f1b31864d2e0'
$provenancePath = Join-Path $root 'vendor\wzhk-spectrum-visualizer\UPSTREAM-SOURCE.json'

if (Test-Path -LiteralPath $provenancePath -PathType Leaf) {
    try {
        $provenance = Get-Content -LiteralPath $provenancePath -Raw | ConvertFrom-Json

        $repository = [string]$provenance.repository
        $commit = [string]$provenance.commit

        $provenanceValid = (
            -not [string]::IsNullOrWhiteSpace($repository) -and
            -not [string]::IsNullOrWhiteSpace($commit) -and
            $commit -ceq $expectedUpstreamCommit -and
            $provenance.vendoredWithoutGitMetadata -eq $true
        )

        Add-Check `
            -Name 'Upstream provenance contents' `
            -Passed $provenanceValid `
            -Required $true `
            -Detail "Expected commit $expectedUpstreamCommit with a repository URL and vendoredWithoutGitMetadata=true."
    }
    catch {
        Add-Check `
            -Name 'Upstream provenance contents' `
            -Passed $false `
            -Required $true `
            -Detail $_.Exception.Message
    }
}
else {
    Add-Check `
        -Name 'Upstream provenance contents' `
        -Passed $false `
        -Required $true `
        -Detail "Provenance file is missing: $provenancePath"
}

$logoDirectory = Join-Path $root '.trackprompt-data\wzhk-spectrum\assets\logo'
$trackDirectory = Join-Path $root '.trackprompt-data\wzhk-spectrum\assets\track'
$logoExtensions = @('.png', '.svg', '.webp', '.jpg', '.jpeg')
$trackExtensions = @('.wav', '.flac', '.aiff', '.aif', '.mp3')

$logoCount = 0
if (Test-Path -LiteralPath $logoDirectory -PathType Container) {
    $logoCount = @(
        Get-ChildItem -LiteralPath $logoDirectory -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension.ToLowerInvariant() -in $logoExtensions }
    ).Count
}

$trackCount = 0
if (Test-Path -LiteralPath $trackDirectory -PathType Container) {
    $trackCount = @(
        Get-ChildItem -LiteralPath $trackDirectory -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension.ToLowerInvariant() -in $trackExtensions }
    ).Count
}

Add-Check `
    -Name 'WZHK logo available' `
    -Passed ($logoCount -gt 0) `
    -Required $false `
    -Detail "$logoDirectory ($logoCount supported file(s); expected: $($logoExtensions -join ', '))"

Add-Check `
    -Name 'Scattered master available' `
    -Passed ($trackCount -gt 0) `
    -Required $false `
    -Detail "$trackDirectory ($trackCount supported file(s); expected: $($trackExtensions -join ', '))"

Write-Host ''
Write-Host 'WZHK Spectrum preflight' -ForegroundColor Cyan

@($script:Checks) |
    Format-Table Check, Passed, Required, Detail -AutoSize |
    Out-Host

Write-Host "Checks evaluated: $($script:Checks.Count)"

$requiredFailures = @(
    $script:Checks |
        Where-Object { $_.Required -and -not $_.Passed }
)

if ($requiredFailures.Count -gt 0) {
    Write-Host 'Status: Scaffold invalid' -ForegroundColor Red
    throw "WZHK Spectrum preflight failed: $($requiredFailures.Count) required check(s) did not pass."
}

$assetWarnings = @(
    $script:Checks |
        Where-Object { -not $_.Required -and -not $_.Passed }
)

if ($assetWarnings.Count -gt 0) {
    foreach ($assetWarning in $assetWarnings) {
        Write-Warning "$($assetWarning.Check): $($assetWarning.Detail)"
    }

    Write-Host 'Status: Scaffold valid, assets still required' -ForegroundColor Yellow
}
else {
    Write-Host 'Status: Scaffold valid, assets ready' -ForegroundColor Green
}

Write-Host 'Required WZHK Spectrum scaffold checks passed.' -ForegroundColor Green
