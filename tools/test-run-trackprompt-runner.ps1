#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-RunnerTest {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if (-not $Condition) {
        throw "Runner test failed: $Message"
    }
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$runnerPath = Join-Path $repoRoot "run-trackprompt-to-blender.ps1"
$manualBuilderPath = Join-Path $repoRoot "build-trackprompt-visualizer.ps1"
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $runnerPath,
    [ref]$tokens,
    [ref]$parseErrors
)

if ($parseErrors.Count -gt 0) {
    throw "The canonical runner does not parse: $($parseErrors[0].Message)"
}

$manualTokens = $null
$manualParseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    $manualBuilderPath,
    [ref]$manualTokens,
    [ref]$manualParseErrors
)

if ($manualParseErrors.Count -gt 0) {
    throw "The manual visualizer builder does not parse: $($manualParseErrors[0].Message)"
}

$runnerText = Get-Content -LiteralPath $runnerPath -Raw
Assert-RunnerTest `
    ([regex]::IsMatch(
        $runnerText,
        '(?s)if \(\$BuildStack\).*?"--build".*?"--force-recreate".*?"backend".*?"frontend"'
    )) `
    "BuildStack must rebuild and recreate both current application services"
Assert-RunnerTest `
    ([regex]::IsMatch(
        $runnerText,
        '(?s)AutoRebuildStaleBackend -and -not \$BuildStack.*?"--no-deps".*?"backend"'
    )) `
    "automatic stale repair must be one backend-only no-dependency recreation path"
Assert-RunnerTest `
    ($runnerText.Contains('-not $visualizerContractCurrent -and $AutoRebuildStaleBackend')) `
    "the one-attempt stale repair must gate on routes and visualizer capabilities together"
Assert-RunnerTest `
    ([regex]::IsMatch(
        $runnerText,
        '(?s)One-time stale TrackPrompt backend repair.*?Get-LiveOpenApi.*?Get-LiveCapabilities'
    )) `
    "the bounded repair must refetch both OpenAPI and capabilities"
Assert-RunnerTest `
    (-not $runnerText.Contains('"--volumes"')) `
    "the canonical runner must never remove named volumes"
Assert-RunnerTest `
    ($runnerText.Contains('scene build; local audio input redacted')) `
    "the local audio path must be redacted from native command display"
Assert-RunnerTest `
    (([regex]::Matches($runnerText, '"--python-exit-code", "1"')).Count -eq 2) `
    "both Blender Python invocations must request a nonzero Python failure exit"
Assert-RunnerTest `
    ($runnerText.Contains('[ValidateSet("abstract-geometry", "space-journey", "space-journey-story")]')) `
    "the runner must constrain visualizer presets to the supported registry"
Assert-RunnerTest `
    ($runnerText.Contains('[string]$VisualizerPreset = "abstract-geometry"')) `
    "Abstract Geometry must remain the canonical runner default"
Assert-RunnerTest `
    ($runnerText.Contains('"--config", $resolvedVisualizerConfigPath')) `
    "the resolved configuration artifact must be passed to Blender"
Assert-RunnerTest `
    ($runnerText.Contains('/api/visualizer/config/resolve')) `
    "the runner must resolve configurations through the typed backend endpoint"
Assert-RunnerTest `
    ($runnerText.Contains('-Arguments @("config", "--quiet")')) `
    "the combined base and full-GPU Compose configuration must be validated before stack work"
Assert-RunnerTest `
    (([regex]::Matches($runnerText, 'Wait-ComposeServiceHealthy -Service "frontend"')).Count -ge 3) `
    "the frontend must reach its Compose healthcheck after rebuild, startup, and bounded repair paths"

$manualBuilderText = Get-Content -LiteralPath $manualBuilderPath -Raw
Assert-RunnerTest `
    ($manualBuilderText.Contains('[string]$VisualizerPreset = "abstract-geometry"')) `
    "the manual builder must preserve Abstract Geometry as its default preset"
Assert-RunnerTest `
    ($manualBuilderText.Contains('/api/visualizer/config/resolve')) `
    "the manual builder must use the typed configuration resolver"
Assert-RunnerTest `
    ($manualBuilderText.Contains('"--config", $resolvedConfigPath')) `
    "the manual builder must pass the resolved configuration to Blender"
Assert-RunnerTest `
    ($manualBuilderText.Contains('trip-to-andromeda-space-journey.blend')) `
    "the manual builder must avoid cross-preset output collisions"
Assert-RunnerTest `
    ($manualBuilderText.Contains('Join-Path $legacyOutputDirectory "space-journey"')) `
    "Space Journey manual outputs must be isolated from legacy Abstract outputs"
Assert-RunnerTest `
    (([regex]::Matches($manualBuilderText, '"--python-exit-code", "1"')).Count -eq 2) `
    "both manual Blender invocations must surface Python failures"
Assert-RunnerTest `
    ($manualBuilderText.Contains('scene build; local audio input redacted')) `
    "the manual builder must redact the private audio path from displayed commands"
Assert-RunnerTest `
    ($manualBuilderText.Contains('Assert-CurrentBuildArtifacts')) `
    "the manual builder must validate a fresh build manifest and resolved config"
Assert-RunnerTest `
    ($manualBuilderText.Contains('Assert-CurrentPreviewArtifacts')) `
    "the manual builder must validate fresh preview artifacts and probe evidence"
Assert-RunnerTest `
    ($manualBuilderText.Contains('$item.LastWriteTimeUtc -lt $StartedAt')) `
    "the manual builder must reject stale artifacts from earlier invocations"

$functionNames = @(
    "Invoke-NativeCaptured",
    "Assert-NativeJsonSuccess",
    "Test-ApiHealth",
    "Wait-ApiHealth",
    "Get-DirectPropertyValue",
    "Test-GuidString",
    "Get-LifecycleNodes",
    "Get-LifecyclePropertyValue",
    "Get-JobState",
    "Resolve-JsonPointer",
    "Merge-OpenApiSchema",
    "Get-UploadContract",
    "Get-SchemaPrimitiveType",
    "Get-SchemaFormat",
    "Get-SchemaDefault",
    "Convert-FormValue",
    "New-UploadFieldPlan",
    "Test-OpenApiOperation",
    "Get-VisualizerRouteStatus",
    "Get-VisualizerCapabilitiesStatus",
    "Test-FiniteNumber",
    "Get-VisualizerOutputNames",
    "Read-VisualizerConfigRequest",
    "Assert-ResolvedVisualizerConfig",
    "Resolve-VisualizerConfig",
    "ConvertTo-CanonicalJsonValue",
    "Test-VisualizerConfigMatch",
    "Assert-PublicCueValue",
    "Assert-VisualCueSheet",
    "Assert-NonEmptyFile",
    "Assert-ManifestSuccess",
    "Assert-BlenderBuildArtifacts",
    "Assert-PreviewArtifacts",
    "Assert-CanonicalRunOutputs",
    "Resolve-FfmpegExecutable"
)

$functionAsts = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
        },
        $true
    )
)

foreach ($name in $functionNames) {
    $definition = $functionAsts |
        Where-Object { $_.Name -eq $name } |
        Select-Object -First 1

    if ($null -eq $definition) {
        throw "Runner test could not find function $name."
    }

    Invoke-Expression $definition.Extent.Text
}

$script:ApiBase = "http://127.0.0.1:8000"
$script:MockHealthResponse = [pscustomobject]@{ status = "degraded" }

function Invoke-RestMethod {
    param(
        [string]$Method,
        [string]$Uri,
        [int]$TimeoutSec
    )

    return $script:MockHealthResponse
}

function Start-Sleep {
    param([int]$Seconds)
}

Assert-RunnerTest `
    ($null -eq (Test-ApiHealth)) `
    "a reachable degraded API must not be treated as healthy"
Assert-RunnerTest `
    ($script:LastApiHealthObservation.status -eq "degraded") `
    "the last degraded health observation must remain available for diagnostics"

$degradedWaitRejected = $false

try {
    [void](Wait-ApiHealth -Attempts 1)
}
catch {
    $degradedWaitRejected = $_.Exception.Message -like "*status 'degraded'*"
}

Assert-RunnerTest `
    $degradedWaitRejected `
    "the health wait must reject a degraded response and report its status"

$script:MockHealthResponse = [pscustomobject]@{ status = "ok" }
Assert-RunnerTest `
    ($null -ne (Test-ApiHealth)) `
    "an explicit status=ok response must pass the health gate"

$openApi = @'
{
  "openapi": "3.1.0",
  "paths": {
    "/api/analyses": {
      "post": {
        "requestBody": {
          "content": {
            "multipart/form-data; boundary=ignored": {
              "schema": {"$ref": "#/components/schemas/Upload"}
            }
          }
        }
      }
    },
    "/api/analyses/{job_id}/visual-cues": {"post": {}},
    "/api/analyses/{job_id}/visual-cues/export": {"get": {}},
    "/api/visualizer/config/resolve": {"post": {}}
  },
  "components": {
    "schemas": {
      "Upload": {
        "allOf": [
          {
            "type": "object",
            "required": ["file", "permissionConfirmed"],
            "properties": {
              "file": {"$ref": "#/components/schemas/BinaryUpload"},
              "mode": {"type": "string", "default": "fast"},
              "permissionConfirmed": {"type": "boolean"},
              "enableLyricalAnalysis": {"type": "boolean", "default": false},
              "enableGenreAnalysis": {"type": "boolean", "default": false},
              "lyricsConsentConfirmed": {"type": "boolean", "default": false},
              "deriveLyricalThemes": {"type": "boolean", "default": false},
              "allowFeatureFallback": {"type": "boolean", "default": false}
            }
          }
        ]
      },
      "BinaryUpload": {
        "anyOf": [
          {"type": "string", "format": "binary"},
          {"type": "null"}
        ]
      }
    }
  }
}
'@ | ConvertFrom-Json

$routeStatus = Get-VisualizerRouteStatus -OpenApi $openApi
Assert-RunnerTest $routeStatus.Complete "both visual-cue methods and config resolution must be detected"
$openApi.paths.PSObject.Properties.Remove("/api/visualizer/config/resolve")
$routeStatus = Get-VisualizerRouteStatus -OpenApi $openApi
Assert-RunnerTest (-not $routeStatus.Complete) "a missing config resolver must mark the contract stale"
$openApi.paths | Add-Member `
    -NotePropertyName "/api/visualizer/config/resolve" `
    -NotePropertyValue ([pscustomobject]@{ post = [pscustomobject]@{} })

$currentCapabilities = [pscustomobject]@{
    blenderVisualizerPreset = "abstract-geometry"
    blenderVisualizerDefaultPreset = "abstract-geometry"
    blenderVisualizerPresets = @("abstract-geometry", "space-journey", "space-journey-story")
    blenderVisualizerConfigSchemaVersion = "1.0.0"
}
$capabilitiesStatus = Get-VisualizerCapabilitiesStatus `
    -Capabilities $currentCapabilities `
    -RequestedPreset "space-journey"
Assert-RunnerTest `
    ($capabilitiesStatus.Complete -and $capabilitiesStatus.RequestedPresetAdvertised) `
    "the current typed visualizer capability contract must pass"

$staleSchemaCapabilities = $currentCapabilities |
    ConvertTo-Json -Depth 10 |
    ConvertFrom-Json
$staleSchemaCapabilities.blenderVisualizerConfigSchemaVersion = "0.9.0"
$capabilitiesStatus = Get-VisualizerCapabilitiesStatus `
    -Capabilities $staleSchemaCapabilities `
    -RequestedPreset "space-journey"
Assert-RunnerTest `
    (-not $capabilitiesStatus.Complete) `
    "a stale visualizer config schema must consume the shared compatibility repair gate"

$missingPresetCapabilities = $currentCapabilities |
    ConvertTo-Json -Depth 10 |
    ConvertFrom-Json
$missingPresetCapabilities.blenderVisualizerPresets = @("abstract-geometry")
$capabilitiesStatus = Get-VisualizerCapabilitiesStatus `
    -Capabilities $missingPresetCapabilities `
    -RequestedPreset "space-journey"
Assert-RunnerTest `
    (-not $capabilitiesStatus.Complete -and -not $capabilitiesStatus.RequestedPresetAdvertised) `
    "a missing requested preset must remain an honest capability failure after repair"

$openApi.paths.PSObject.Properties.Remove("/api/analyses/{job_id}/visual-cues")
$routeStatus = Get-VisualizerRouteStatus -OpenApi $openApi
Assert-RunnerTest (-not $routeStatus.Complete) "a missing POST route must mark the contract stale"

# Restore the POST path for the upload-contract tests.
$openApi.paths | Add-Member `
    -NotePropertyName "/api/analyses/{job_id}/visual-cues" `
    -NotePropertyValue ([pscustomobject]@{ post = [pscustomobject]@{} })

$script:EnableLyrics = $true
$script:EnableGenre = $false
$script:ConfirmLyricsConsent = $true
$script:ConfirmPermission = $true
$script:Mode = "deep"
$script:DeriveAbstractThemes = $true
$contract = Get-UploadContract -OpenApi $openApi
$plan = New-UploadFieldPlan `
    -OpenApi $openApi `
    -Contract $contract `
    -Overrides @{ allowFeatureFallback = $true }

Assert-RunnerTest ($plan.FileField -eq "file") "binary fields behind refs/anyOf must resolve"
Assert-RunnerTest ($plan.FormFields["mode"] -eq "deep") "mode must follow the requested run"
Assert-RunnerTest ($plan.FormFields["permissionConfirmed"] -eq "true") "permission must map by alias"
Assert-RunnerTest ($plan.FormFields["enableLyricalAnalysis"] -eq "true") "lyrics enablement must map by alias"
Assert-RunnerTest ($plan.FormFields["lyricsConsentConfirmed"] -eq "true") "lyrics consent must map by alias"
Assert-RunnerTest ($plan.FormFields["deriveLyricalThemes"] -eq "true") "theme derivation must not be mistaken for lyrics enablement"
Assert-RunnerTest ($plan.FormFields["allowFeatureFallback"] -eq "true") "explicit safe overrides must survive mapping"

$unknownOverrideRejected = $false

try {
    [void](New-UploadFieldPlan `
        -OpenApi $openApi `
        -Contract $contract `
        -Overrides @{ absentFromContract = "value" })
}
catch {
    $unknownOverrideRejected = $true
}

Assert-RunnerTest $unknownOverrideRejected "unknown multipart overrides must be rejected"

$nestedJob = [pscustomobject]@{
    status = "ok"
    data = [pscustomobject]@{
        response = [pscustomobject]@{
            lifecycle = [pscustomobject]@{
                status = "completed_with_warnings"
                progress = 100
            }
        }
    }
}

Assert-RunnerTest `
    ((Get-JobState -JobResponse $nestedJob) -eq "completed_with_warnings") `
    "nested completed_with_warnings must win over a generic wrapper status"

$jobId = "11111111-1111-4111-8111-111111111111"
$cue = @"
{
  "schemaVersion": "1.1.0",
  "source": {"jobId": "$jobId"},
  "timeline": {"durationSeconds": 2.0, "fps": 30, "frameStart": 1, "frameEnd": 60},
  "beats": [],
  "onsets": [{"timeSeconds": 1.0, "frame": 31}],
  "sections": [{"startSeconds": 0.0, "endSeconds": 2.0, "startFrame": 1, "endFrame": 60}],
  "transitions": [],
  "curves": {
    "masterEnergy": {"points": [[1, 0.1], [60, 0.8]]}
  }
}
"@ | ConvertFrom-Json

$summary = Assert-VisualCueSheet -Cue $cue -ExpectedJobId $jobId -ExpectedFps 30
Assert-RunnerTest ($summary.curves -contains "masterEnergy") "a valid cue sheet must pass before Blender"

$cue | Add-Member -NotePropertyName "filename" -NotePropertyValue "private.wav"
$privateCueRejected = $false

try {
    [void](Assert-VisualCueSheet -Cue $cue -ExpectedJobId $jobId -ExpectedFps 30)
}
catch {
    $privateCueRejected = $true
}

Assert-RunnerTest $privateCueRejected "private cue fields must be rejected before Blender"

$nativeSuccess = Assert-NativeJsonSuccess `
    -NativeResult ([pscustomobject]@{ Lines = @('{"ok":true,"outputFile":"scene.blend"}') }) `
    -Description "Mock Blender operation"
Assert-RunnerTest ($nativeSuccess.ok -eq $true) "structured Blender success must be accepted"

$nativeProgressSuccess = Assert-NativeJsonSuccess `
    -NativeResult ([pscustomobject]@{
        Lines = @(
            'Fra:7229 Mem:10.00M Time:00:06:00.00 {"ok":true,"manifest":"preview-manifest.json"} Blender quit'
        )
    }) `
    -Description "Mock progress-prefixed Blender operation"
Assert-RunnerTest `
    ($nativeProgressSuccess.manifest -eq "preview-manifest.json") `
    "Blender carriage-return progress must not hide structured completion"

$nativeFailureRejected = $false

try {
    [void](Assert-NativeJsonSuccess `
        -NativeResult ([pscustomobject]@{ Lines = @('{"ok":false,"error":{"code":"mock_failure"}}') }) `
        -Description "Mock Blender operation")
}
catch {
    $nativeFailureRejected = $true
}

Assert-RunnerTest $nativeFailureRejected "structured Blender failure must not become a false success"

Assert-ManifestSuccess `
    -Manifest ([pscustomobject]@{ ok = $true }) `
    -Description "Mock scene manifest"
$missingManifestOkRejected = $false

try {
    Assert-ManifestSuccess `
        -Manifest ([pscustomobject]@{ schemaVersion = "1.0.0" }) `
        -Description "Mock scene manifest"
}
catch {
    $missingManifestOkRejected = $true
}

Assert-RunnerTest $missingManifestOkRejected "scene manifests must declare top-level ok=true"

$mockOutputs = [ordered]@{}

foreach ($outputName in @(
    "runManifest",
    "healthBefore",
    "health",
    "healthAfter",
    "capabilities",
    "openApiBefore",
    "openApiAfter",
    "uploadContract",
    "uploadPlan",
    "uploadResponse",
    "jobId",
    "jobFinal",
    "analysisJson",
    "analysisMarkdown",
    "cueSheet",
    "cueSummary",
    "visualizerConfig",
    "blend",
    "sceneManifest",
    "previewManifest",
    "previewClip"
)) {
    $mockOutputs[$outputName] = $runnerPath
}

$mockOutputs["previewStills"] = @($runnerPath)
Assert-CanonicalRunOutputs -Outputs $mockOutputs -PreviewRequired $true

$contractRoot = Join-Path `
    $repoRoot `
    ("test-output\runner-contract-" + [Guid]::NewGuid().ToString("N"))

try {
    New-Item -ItemType Directory -Path $contractRoot -Force | Out-Null

    $abstractOutputNames = Get-VisualizerOutputNames -Preset "abstract-geometry"
    $spaceOutputNames = Get-VisualizerOutputNames -Preset "space-journey"
    $storyOutputNames = Get-VisualizerOutputNames -Preset "space-journey-story"
    Assert-RunnerTest `
        ($abstractOutputNames.BlendFile -eq "trackprompt-abstract.blend") `
        "the legacy Abstract Geometry blend filename must be preserved"
    Assert-RunnerTest `
        ($abstractOutputNames.PreviewClip -eq "trackprompt-preview.mp4") `
        "the legacy Abstract Geometry preview filename must be preserved"
    Assert-RunnerTest `
        ($spaceOutputNames.BlendFile -eq "trackprompt-space-journey.blend") `
        "Space Journey must use a non-colliding blend filename"
    Assert-RunnerTest `
        ($spaceOutputNames.PreviewClip -eq "space-journey-preview.mp4") `
        "Space Journey must use its preset-specific preview filename"
    Assert-RunnerTest `
        ($storyOutputNames.BlendFile -eq "trackprompt-space-journey-story.blend") `
        "Space Journey Story must use a non-colliding blend filename"
    Assert-RunnerTest `
        ($storyOutputNames.PreviewClip -eq "space-journey-story-preview.mp4") `
        "Space Journey Story must use its preset-specific preview filename"

    $abstractResolvedConfig = @'
{
  "schemaVersion": "1.0.0",
  "preset": "abstract-geometry",
  "parameters": {},
  "seed": 84291,
  "defaultedParameters": [],
  "warnings": []
}
'@ | ConvertFrom-Json
    $spaceResolvedConfig = @'
{
  "schemaVersion": "1.0.0",
  "preset": "space-journey",
  "parameters": {
    "cameraDistance": 18.0,
    "cameraOrbitSpeed": 0.15,
    "ringThickness": 0.06,
    "ringOcclusion": 0.20,
    "palette": "andromeda",
    "glowStrength": 1.8,
    "shardDensity": 0.35,
    "fogDepth": 0.50,
    "bassResponse": 1.2,
    "drumResponse": 0.9,
    "vocalResponse": 0.65
  },
  "seed": 84291,
  "defaultedParameters": [
    "bassResponse",
    "cameraDistance",
    "cameraOrbitSpeed",
    "drumResponse",
    "fogDepth",
    "glowStrength",
    "palette",
    "ringOcclusion",
    "ringThickness",
    "shardDensity",
    "vocalResponse"
  ],
  "warnings": []
}
'@ | ConvertFrom-Json

    [void](Assert-ResolvedVisualizerConfig `
        -Config $abstractResolvedConfig `
        -ExpectedPreset "abstract-geometry" `
        -ExpectedSeed 84291)
    [void](Assert-ResolvedVisualizerConfig `
        -Config $spaceResolvedConfig `
        -ExpectedPreset "space-journey" `
        -ExpectedSeed 84291)

    $reorderedSpaceConfig = @'
{
  "warnings": [],
  "defaultedParameters": [
    "bassResponse",
    "cameraDistance",
    "cameraOrbitSpeed",
    "drumResponse",
    "fogDepth",
    "glowStrength",
    "palette",
    "ringOcclusion",
    "ringThickness",
    "shardDensity",
    "vocalResponse"
  ],
  "seed": 84291,
  "parameters": {
    "vocalResponse": 0.65,
    "shardDensity": 0.35,
    "ringThickness": 0.06,
    "ringOcclusion": 0.20,
    "palette": "andromeda",
    "glowStrength": 1.8,
    "fogDepth": 0.50,
    "drumResponse": 0.9,
    "cameraOrbitSpeed": 0.15,
    "cameraDistance": 18.0,
    "bassResponse": 1.2
  },
  "preset": "space-journey",
  "schemaVersion": "1.0.0"
}
'@ | ConvertFrom-Json
    Assert-RunnerTest `
        (Test-VisualizerConfigMatch `
            -Actual $reorderedSpaceConfig `
            -Expected $spaceResolvedConfig `
            -Preset "space-journey" `
            -Seed 84291) `
        "manifest configuration comparison must ignore JSON object property order"

    $invalidSpaceConfig = (
        $spaceResolvedConfig | ConvertTo-Json -Depth 20 | ConvertFrom-Json
    )
    $invalidSpaceConfig.parameters.cameraDistance = 41.0
    $invalidSpaceRejected = $false

    try {
        [void](Assert-ResolvedVisualizerConfig `
            -Config $invalidSpaceConfig `
            -ExpectedPreset "space-journey" `
            -ExpectedSeed 84291)
    }
    catch {
        $invalidSpaceRejected = $true
    }

    Assert-RunnerTest `
        $invalidSpaceRejected `
        "resolved Space Journey parameters outside backend bounds must be rejected"

    $defaultConfigRequest = Read-VisualizerConfigRequest `
        -Preset "abstract-geometry" `
        -Seed 84291
    Assert-RunnerTest `
        ($defaultConfigRequest.preset -eq "abstract-geometry") `
        "omitting preset configuration must preserve Abstract Geometry"
    Assert-RunnerTest `
        (@($defaultConfigRequest.parameters.PSObject.Properties).Count -eq 0) `
        "the default Abstract Geometry request must have no public parameters"

    $configRequestPath = Join-Path $contractRoot "space-journey-config.json"
    [IO.File]::WriteAllText(
        $configRequestPath,
        ($spaceResolvedConfig | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )
    $fileConfigRequest = Read-VisualizerConfigRequest `
        -Preset "abstract-geometry" `
        -Seed 84291 `
        -ConfigPath $configRequestPath
    Assert-RunnerTest `
        ($fileConfigRequest.preset -eq "space-journey") `
        "a config file must select its preset when the CLI preset was omitted"
    Assert-RunnerTest `
        ([double]$fileConfigRequest.parameters.glowStrength -eq 1.8) `
        "a config file must preserve explicit preset parameters"

    $presetConflictRejected = $false

    try {
        [void](Read-VisualizerConfigRequest `
            -Preset "abstract-geometry" `
            -Seed 84291 `
            -ConfigPath $configRequestPath `
            -PresetWasExplicit $true)
    }
    catch {
        $presetConflictRejected = $true
    }

    Assert-RunnerTest `
        $presetConflictRejected `
        "an explicit CLI preset must not silently conflict with the config file"

    $seedConflictRejected = $false

    try {
        [void](Read-VisualizerConfigRequest `
            -Preset "space-journey" `
            -Seed 7 `
            -ConfigPath $configRequestPath `
            -SeedWasExplicit $true)
    }
    catch {
        $seedConflictRejected = $true
    }

    Assert-RunnerTest `
        $seedConflictRejected `
        "an explicit CLI seed must not silently conflict with the config file"

    $fakeBlend = Join-Path $contractRoot "scene.blend"
    $fakeSceneManifestPath = Join-Path $contractRoot "scene.manifest.json"
    [IO.File]::WriteAllBytes($fakeBlend, [byte[]](1, 2, 3, 4))
    $resolvedFakeBlend = (Resolve-Path -LiteralPath $fakeBlend).Path
    $sceneChecks = [ordered]@{}

    foreach ($checkName in @(
        "frameRange",
        "fps",
        "activeCamera",
        "collections",
        "audioBus",
        "audioBusControls",
        "audioBusFCurves",
        "sceneFCurves",
        "audioStrip",
        "outputFile"
    )) {
        $sceneChecks[$checkName] = $true
    }

    $sceneManifest = [ordered]@{
        ok = $true
        schemaVersion = "1.0.0"
        preset = "abstract-geometry"
        seed = 84291
        cueSheetSchemaVersion = "1.1.0"
        visualizerConfig = $abstractResolvedConfig
        checks = $sceneChecks
        scene = [ordered]@{
            ok = $true
            preset = "abstract-geometry"
            seed = 84291
            audioStripPresent = $true
            outputFile = $resolvedFakeBlend
            collections = @(
                "TP_WORLD", "TP_CAMERAS", "TP_LIGHTS",
                "TP_PRIMARY_GEOMETRY", "TP_RINGS", "TP_SHARDS",
                "TP_VOCAL_ELEMENTS", "TP_BACKGROUND", "TP_DEBUG"
            )
            controlProperties = @(
                "master_energy", "drum_energy", "bass_energy",
                "vocal_energy", "other_energy", "low_band", "mid_band",
                "high_band", "brightness", "transient_activity"
            )
            activeCamera = "TP_CAMERA"
            frameStart = 1
            frameEnd = 60
            objectCount = 2
            materialCount = 1
            fCurveCount = 10
        }
    }
    [IO.File]::WriteAllText(
        $fakeSceneManifestPath,
        ($sceneManifest | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )
    [void](Assert-BlenderBuildArtifacts `
        -BlendPath $fakeBlend `
        -ManifestPath $fakeSceneManifestPath `
        -ExpectedCueSchema "1.1.0" `
        -ExpectedSeed 84291 `
        -ExpectedPreset "abstract-geometry" `
        -ExpectedConfig $abstractResolvedConfig)

    $sceneManifest["checks"]["audioBus"] = $false
    [IO.File]::WriteAllText(
        $fakeSceneManifestPath,
        ($sceneManifest | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )
    $failedSceneCheckRejected = $false

    try {
        [void](Assert-BlenderBuildArtifacts `
            -BlendPath $fakeBlend `
            -ManifestPath $fakeSceneManifestPath `
            -ExpectedCueSchema "1.1.0" `
            -ExpectedSeed 84291 `
            -ExpectedPreset "abstract-geometry" `
            -ExpectedConfig $abstractResolvedConfig)
    }
    catch {
        $failedSceneCheckRejected = $true
    }

    Assert-RunnerTest `
        $failedSceneCheckRejected `
        "a failed scene-contract check must stop the runner"

    $sceneManifest["checks"]["audioBus"] = $true
    $sceneManifest["preset"] = "space-journey"
    $sceneManifest["visualizerConfig"] = $spaceResolvedConfig
    $sceneManifest["scene"]["preset"] = "space-journey"
    [IO.File]::WriteAllText(
        $fakeSceneManifestPath,
        ($sceneManifest | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )
    [void](Assert-BlenderBuildArtifacts `
        -BlendPath $fakeBlend `
        -ManifestPath $fakeSceneManifestPath `
        -ExpectedCueSchema "1.1.0" `
        -ExpectedSeed 84291 `
        -ExpectedPreset "space-journey" `
        -ExpectedConfig $spaceResolvedConfig)

    $previewDirectory = Join-Path $contractRoot "preview"
    New-Item -ItemType Directory -Path $previewDirectory -Force | Out-Null
    $clipPath = Join-Path $previewDirectory "space-journey-preview.mp4"
    $previewManifestPath = Join-Path $previewDirectory "preview-manifest.json"
    $previewFrames = @(1, 12, 24, 36, 48, 60)
    $previewRoles = @(
        "opening",
        "early-development",
        "main-groove",
        "breakdown",
        "peak",
        "outro"
    )
    $stillPaths = @()
    $stillEntries = @()
    $roleEntries = @()

    for ($index = 0; $index -lt $previewFrames.Count; $index++) {
        $frame = $previewFrames[$index]
        $stillPath = Join-Path $previewDirectory ("frame_{0:D6}.png" -f $frame)
        [IO.File]::WriteAllBytes($stillPath, [byte[]](1, 2, 3))
        $resolvedStillPath = (Resolve-Path -LiteralPath $stillPath).Path
        $roleEntry = [ordered]@{
            role = $previewRoles[$index]
            frame = $frame
            sectionId = "section-$index"
        }
        $stillPaths += $resolvedStillPath
        $roleEntries += $roleEntry
        $stillEntries += [ordered]@{
            frame = $frame
            path = $resolvedStillPath
            sizeBytes = 3
            role = $previewRoles[$index]
            sectionId = "section-$index"
        }
    }

    [IO.File]::WriteAllBytes($clipPath, [byte[]](4, 5, 6))
    $previewChecks = [ordered]@{}

    foreach ($checkName in @(
        "sceneFrameRange", "collections", "audioBus", "audioBusFCurves",
        "sceneFCurves", "audioStrip", "stills", "movie",
        "movieDuration", "audioMux"
    )) {
        $previewChecks[$checkName] = $true
    }

    $previewManifest = [ordered]@{
        ok = $true
        schemaVersion = "1.0.0"
        preset = "space-journey"
        visualizerConfig = $spaceResolvedConfig
        previewRoles = $roleEntries
        render = [ordered]@{ width = 640; height = 360; fps = 30.0 }
        scene = [ordered]@{
            preset = "space-journey"
            frameStart = 1
            frameEnd = 60
            fps = 30.0
        }
        checks = $previewChecks
        stills = [ordered]@{
            ok = $true
            plannedFrames = $previewFrames
            renderedFrames = $previewFrames
            stillFrames = $stillPaths
            stillRoles = $roleEntries
            stills = $stillEntries
        }
        clip = [ordered]@{
            ok = $true
            clip = (Resolve-Path -LiteralPath $clipPath).Path
            startFrame = 1
            endFrame = 60
            centerFrame = 30
            role = "representative-interior"
            plannedDurationSeconds = 2.0
            durationSeconds = 2.0
            audioMuxStatus = "verified-muxed"
            verification = [ordered]@{
                ok = $true
                plannedDurationSeconds = 2.0
                durationSeconds = 2.0
                durationMatches = $true
                width = 640
                height = 360
                fps = 30.0
                videoCodec = "h264"
                audioCodec = "aac"
                videoPresent = $true
                audioRequested = $true
                audioPresent = $true
                audioMatchesRequest = $true
            }
        }
    }
    [IO.File]::WriteAllText(
        $previewManifestPath,
        ($previewManifest | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )
    [void](Assert-PreviewArtifacts `
        -PreviewDirectory $previewDirectory `
        -ManifestPath $previewManifestPath `
        -ExpectedPreset "space-journey" `
        -ExpectedConfig $spaceResolvedConfig `
        -ExpectedClipName "space-journey-preview.mp4" `
        -ExpectedWidth 640 `
        -ExpectedHeight 360 `
        -ExpectedFps 30)

    $previewManifest["clip"]["verification"]["videoCodec"] = "vp9"
    [IO.File]::WriteAllText(
        $previewManifestPath,
        ($previewManifest | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )
    $wrongCodecRejected = $false

    try {
        [void](Assert-PreviewArtifacts `
            -PreviewDirectory $previewDirectory `
            -ManifestPath $previewManifestPath `
            -ExpectedPreset "space-journey" `
            -ExpectedConfig $spaceResolvedConfig `
            -ExpectedClipName "space-journey-preview.mp4" `
            -ExpectedWidth 640 `
            -ExpectedHeight 360 `
            -ExpectedFps 30)
    }
    catch {
        $wrongCodecRejected = $true
    }

    Assert-RunnerTest `
        $wrongCodecRejected `
        "Space Journey must reject preview clips without verified H.264/AAC evidence"
    $previewManifest["clip"]["verification"]["videoCodec"] = "h264"

    $previewManifest["stills"]["stills"][2]["role"] = "peak"
    [IO.File]::WriteAllText(
        $previewManifestPath,
        ($previewManifest | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )
    $wrongRoleRejected = $false

    try {
        [void](Assert-PreviewArtifacts `
            -PreviewDirectory $previewDirectory `
            -ManifestPath $previewManifestPath `
            -ExpectedPreset "space-journey" `
            -ExpectedConfig $spaceResolvedConfig `
            -ExpectedClipName "space-journey-preview.mp4" `
            -ExpectedWidth 640 `
            -ExpectedHeight 360 `
            -ExpectedFps 30)
    }
    catch {
        $wrongRoleRejected = $true
    }

    Assert-RunnerTest `
        $wrongRoleRejected `
        "Space Journey must reject missing or out-of-order still roles"
    $previewManifest["stills"]["stills"][2]["role"] = "main-groove"

    $previewManifest["stills"]["stillFrames"][0] = $resolvedFakeBlend
    $previewManifest["stills"]["stills"][0]["path"] = $resolvedFakeBlend
    [IO.File]::WriteAllText(
        $previewManifestPath,
        ($previewManifest | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )
    $outsidePreviewRejected = $false

    try {
        [void](Assert-PreviewArtifacts `
            -PreviewDirectory $previewDirectory `
            -ManifestPath $previewManifestPath `
            -ExpectedPreset "space-journey" `
            -ExpectedConfig $spaceResolvedConfig `
            -ExpectedClipName "space-journey-preview.mp4" `
            -ExpectedWidth 640 `
            -ExpectedHeight 360 `
            -ExpectedFps 30)
    }
    catch {
        $outsidePreviewRejected = $true
    }

    Assert-RunnerTest `
        $outsidePreviewRejected `
        "preview artifacts outside the run preview directory must be rejected"
}
finally {
    if (Test-Path -LiteralPath $contractRoot -PathType Container) {
        $resolvedContractRoot = (Resolve-Path -LiteralPath $contractRoot).Path
        $expectedParent = (Resolve-Path -LiteralPath (Join-Path $repoRoot "test-output")).Path
        $actualParent = [IO.Directory]::GetParent($resolvedContractRoot).FullName

        if (-not $actualParent.Equals($expectedParent, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a runner-test directory outside test-output."
        }

        Remove-Item -LiteralPath $resolvedContractRoot -Recurse -Force
    }
}

$verifiedBlenderRoot = Join-Path $repoRoot "test-output\blender-short-final"
$verifiedBlend = Join-Path $verifiedBlenderRoot "abstract-geometry.blend"
$verifiedSceneManifest = Join-Path $verifiedBlenderRoot "abstract-geometry.manifest.json"
$verifiedPreviewDirectory = Join-Path $verifiedBlenderRoot "preview"
$verifiedPreviewManifest = Join-Path $verifiedPreviewDirectory "preview-manifest.json"

if (
    (Test-Path -LiteralPath $verifiedBlend -PathType Leaf) -and
    (Test-Path -LiteralPath $verifiedSceneManifest -PathType Leaf)
) {
    $existingSceneManifest = Get-Content `
        -LiteralPath $verifiedSceneManifest `
        -Raw |
        ConvertFrom-Json
    [void](Assert-BlenderBuildArtifacts `
        -BlendPath $verifiedBlend `
        -ManifestPath $verifiedSceneManifest `
        -ExpectedCueSchema ([string]$existingSceneManifest.cueSheetSchemaVersion) `
        -ExpectedSeed ([int]$existingSceneManifest.seed))
}

if (Test-Path -LiteralPath $verifiedPreviewManifest -PathType Leaf) {
    [void](Assert-PreviewArtifacts `
        -PreviewDirectory $verifiedPreviewDirectory `
        -ManifestPath $verifiedPreviewManifest)
}

# Process execution outside the workspace is sandbox-dependent. Mock only the
# executable probe so this test exercises candidate ordering and WinGet package
# discovery without launching media tooling.
function Invoke-NativeCaptured {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Description,
        [switch]$AllowFailure,
        [switch]$Quiet
    )

    if ($Executable.EndsWith("\Microsoft\WinGet\Links\ffmpeg.exe", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Simulated broken WinGet app alias."
    }

    return [pscustomobject]@{
        ExitCode = 0
        Lines = @("ffmpeg version test")
        Text = "ffmpeg version test"
    }
}

$ffmpeg = Resolve-FfmpegExecutable

if (-not [string]::IsNullOrWhiteSpace($ffmpeg)) {
    Assert-RunnerTest `
        (-not $ffmpeg.EndsWith("\Microsoft\WinGet\Links\ffmpeg.exe", [StringComparison]::OrdinalIgnoreCase)) `
        "the broken WinGet Links alias must not be accepted"
}
else {
    Write-Host "FFmpeg discovery assertion skipped: no repository-independent candidate is installed."
}

Write-Host "TrackPrompt PowerShell runner tests passed." -ForegroundColor Green
