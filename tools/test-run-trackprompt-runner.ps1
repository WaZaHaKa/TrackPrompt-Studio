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
    (-not $runnerText.Contains('"--volumes"')) `
    "the canonical runner must never remove named volumes"
Assert-RunnerTest `
    ($runnerText.Contains('scene build; local audio input redacted')) `
    "the local audio path must be redacted from native command display"
Assert-RunnerTest `
    (([regex]::Matches($runnerText, '"--python-exit-code", "1"')).Count -eq 2) `
    "both Blender Python invocations must request a nonzero Python failure exit"
Assert-RunnerTest `
    ($runnerText.Contains('-Arguments @("config", "--quiet")')) `
    "the combined base and full-GPU Compose configuration must be validated before stack work"
Assert-RunnerTest `
    (([regex]::Matches($runnerText, 'Wait-ComposeServiceHealthy -Service "frontend"')).Count -ge 3) `
    "the frontend must reach its Compose healthcheck after rebuild, startup, and bounded repair paths"

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
    "Test-FiniteNumber",
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
    "/api/analyses/{job_id}/visual-cues/export": {"get": {}}
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
Assert-RunnerTest $routeStatus.Complete "both visual-cue methods must be detected"
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
        checks = $sceneChecks
        scene = [ordered]@{
            ok = $true
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
        -ExpectedSeed 84291)

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
            -ExpectedSeed 84291)
    }
    catch {
        $failedSceneCheckRejected = $true
    }

    Assert-RunnerTest `
        $failedSceneCheckRejected `
        "a failed scene-contract check must stop the runner"

    $previewDirectory = Join-Path $contractRoot "preview"
    New-Item -ItemType Directory -Path $previewDirectory -Force | Out-Null
    $stillPath = Join-Path $previewDirectory "frame_000001.png"
    $clipPath = Join-Path $previewDirectory "preview.mp4"
    $previewManifestPath = Join-Path $previewDirectory "preview-manifest.json"
    [IO.File]::WriteAllBytes($stillPath, [byte[]](1, 2, 3))
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
        checks = $previewChecks
        stills = [ordered]@{
            ok = $true
            stillFrames = @((Resolve-Path -LiteralPath $stillPath).Path)
        }
        clip = [ordered]@{
            ok = $true
            clip = (Resolve-Path -LiteralPath $clipPath).Path
            verification = [ordered]@{ ok = $true }
        }
    }
    [IO.File]::WriteAllText(
        $previewManifestPath,
        ($previewManifest | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )
    [void](Assert-PreviewArtifacts `
        -PreviewDirectory $previewDirectory `
        -ManifestPath $previewManifestPath)

    $previewManifest["stills"]["stillFrames"] = @($resolvedFakeBlend)
    [IO.File]::WriteAllText(
        $previewManifestPath,
        ($previewManifest | ConvertTo-Json -Depth 20),
        (New-Object System.Text.UTF8Encoding($false))
    )
    $outsidePreviewRejected = $false

    try {
        [void](Assert-PreviewArtifacts `
            -PreviewDirectory $previewDirectory `
            -ManifestPath $previewManifestPath)
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
