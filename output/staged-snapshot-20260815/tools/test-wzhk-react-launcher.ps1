[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-WzhkLauncher {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if (-not $Condition) { throw $Message }
}

function Write-WzhkLauncherTestFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding($false)))
}

function Invoke-WzhkBoundedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label,
        [ValidateRange(1000, 120000)][int]$TimeoutMilliseconds = 30000
    )

    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = $Arguments
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { throw "$Label did not start." }
        $process.StandardInput.Close()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            try { $process.Kill() } catch { }
            $process.WaitForExit()
            throw "$Label timed out; an interactive prompt may have been reached."
        }
        $process.WaitForExit()
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            StandardOutput = [string]$stdoutTask.Result
            StandardError = [string]$stderrTask.Result
        }
    }
    finally { $process.Dispose() }
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$bootstrap = Join-Path $PSScriptRoot "start-wzhk-mission-control.ps1"
$primary = Join-Path $repositoryRoot "WZHK-Media-Launcher.cmd"
$legacy = Join-Path $repositoryRoot "WZHK-Media-Launcher-Legacy.cmd"

Assert-WzhkLauncher `
    -Condition (
        $PSVersionTable.PSEdition -eq "Desktop" -and
        $PSVersionTable.PSVersion.Major -eq 5 -and
        $PSVersionTable.PSVersion.Minor -eq 1
    ) `
    -Message "Launcher validation must run under Windows PowerShell 5.1."

$tokens = $null
$parseErrors = $null
[Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors) | Out-Null
Assert-WzhkLauncher -Condition (@($parseErrors).Count -eq 0) -Message "The React launcher bootstrap does not parse under Windows PowerShell 5.1."

$primaryText = [IO.File]::ReadAllText($primary)
$legacyText = [IO.File]::ReadAllText($legacy)
$bootstrapText = [IO.File]::ReadAllText($bootstrap)
Assert-WzhkLauncher -Condition ($primaryText.Contains("start-wzhk-mission-control.ps1")) -Message "The primary CMD does not launch React Mission Control."
Assert-WzhkLauncher -Condition (-not $primaryText.Contains("wzhk-media-control-center.ps1")) -Message "The primary CMD still launches the legacy TUI."
Assert-WzhkLauncher -Condition ($primaryText.Contains('-RepositoryRoot "%~dp0."')) -Message "The primary CMD does not protect its trailing-backslash repository path."
Assert-WzhkLauncher -Condition (-not $primaryText.Contains("pause")) -Message "The primary CMD can deadlock unattended startup validation on pause."
Assert-WzhkLauncher -Condition ($legacyText.Contains("wzhk-media-control-center.ps1")) -Message "The explicit legacy fallback is not wired to the TUI."
foreach ($contractToken in @(
    '"-m", "app.mission_control.server"',
    '"--host", "127.0.0.1"',
    '"--port", [string]$port',
    '"--static-dir", $staticRoot',
    '"--instance-descriptor", $descriptorPath'
)) {
    Assert-WzhkLauncher -Condition ($bootstrapText.Contains($contractToken)) -Message "Backend CLI contract token is missing: $contractToken"
}

. $bootstrap -RepositoryRoot $repositoryRoot -PreferredPort 18765 -ImportOnly
Assert-WzhkLauncher -Condition ((ConvertTo-WzhkNativeArgument -Value "plain") -eq "plain") -Message "Plain native argument quoting changed unexpectedly."
Assert-WzhkLauncher `
    -Condition ((ConvertTo-WzhkNativeArgument -Value 'C:\Program Files\TrackPrompt\') -eq '"C:\Program Files\TrackPrompt\\"') `
    -Message "Trailing backslashes are not doubled before the native closing quote."
Assert-WzhkLauncher `
    -Condition ((ConvertTo-WzhkNativeArgument -Value 'scene "approved"') -eq '"scene \"approved\""') `
    -Message "Embedded native argument quotes are not escaped correctly."

$selectedPython = Resolve-WzhkPython
Assert-WzhkLauncher `
    -Condition (-not [string]::IsNullOrWhiteSpace($selectedPython)) `
    -Message "No Python runtime could import the Mission Control backend."
Assert-WzhkLauncher `
    -Condition (Test-WzhkPythonRuntime -Path $selectedPython) `
    -Message "The selected Python runtime failed the Mission Control import probe."
$nonExecutableCandidate = Join-Path $repositoryRoot "frontend\package.json"
Assert-WzhkLauncher `
    -Condition (-not (Test-WzhkPythonRuntime -Path $nonExecutableCandidate)) `
    -Message "A merely existing non-executable file passed the Python runtime probe."
$fallbackPython = Resolve-WzhkPython -Candidates @($nonExecutableCandidate, $selectedPython)
Assert-WzhkLauncher `
    -Condition ([string]::Equals($fallbackPython, $selectedPython, [StringComparison]::OrdinalIgnoreCase)) `
    -Message "Python resolution did not skip an unusable earlier candidate."

$fingerprintFixture = Join-Path ([IO.Path]::GetTempPath()) ("wzhk-launcher-fingerprint-" + [Guid]::NewGuid().ToString("N"))
$fingerprintFixture = [IO.Path]::GetFullPath($fingerprintFixture)
$fixtureFrontend = Join-Path $fingerprintFixture "frontend"
$fixtureStatic = Join-Path $fixtureFrontend "dist"
$fixtureMarker = Join-Path $fixtureStatic ".mission-control-build.json"
$fixtureSource = Join-Path $fixtureFrontend "src\app.ts"
$fixturePublicAsset = Join-Path $fixtureFrontend "public\mark.svg"
$fixtureIndex = Join-Path $fixtureFrontend "index.html"
try {
    Write-WzhkLauncherTestFile -Path (Join-Path $fixtureFrontend "package.json") -Content '{"scripts":{"build":"vite build"}}'
    Write-WzhkLauncherTestFile -Path $fixtureIndex -Content '<main id="root"></main>'
    Write-WzhkLauncherTestFile -Path $fixtureSource -Content 'export const version = 1;'
    Write-WzhkLauncherTestFile -Path $fixturePublicAsset -Content '<svg></svg>'
    Write-WzhkLauncherTestFile -Path (Join-Path $fixtureStatic "index.html") -Content '<main id="built"></main>'

    $initialFingerprint = Get-WzhkFrontendBuildFingerprint -Root $fixtureFrontend
    Assert-WzhkLauncher `
        -Condition ($initialFingerprint -match '^[0-9a-f]{64}$') `
        -Message "The frontend build fingerprint is not a lowercase SHA-256 value."
    Write-WzhkAtomicJson -Path $fixtureMarker -Value ([ordered]@{
        schemaVersion = "2.0.0"
        sourceFingerprint = $initialFingerprint
    })
    Assert-WzhkLauncher `
        -Condition (Test-WzhkFrontendBuildCurrent -Root $fixtureFrontend -StaticDirectory $fixtureStatic -MarkerPath $fixtureMarker) `
        -Message "A matching content-addressed frontend build was treated as stale."
    Assert-WzhkLauncher `
        -Condition (Test-WzhkFrontendPreparationAvailable -NpmPath "" -Root $fixtureFrontend -StaticDirectory $fixtureStatic -MarkerPath $fixtureMarker) `
        -Message "A current frontend build unexpectedly required npm."

    [IO.File]::Delete($fixtureSource)
    $deletedSourceFingerprint = Get-WzhkFrontendBuildFingerprint -Root $fixtureFrontend
    Assert-WzhkLauncher `
        -Condition ($deletedSourceFingerprint -ne $initialFingerprint) `
        -Message "Deleting a frontend source file did not change the build fingerprint."
    Assert-WzhkLauncher `
        -Condition (-not (Test-WzhkFrontendBuildCurrent -Root $fixtureFrontend -StaticDirectory $fixtureStatic -MarkerPath $fixtureMarker)) `
        -Message "Deleting a frontend source file left the prior build marked current."
    Assert-WzhkLauncher `
        -Condition (-not (Test-WzhkFrontendPreparationAvailable -NpmPath "" -Root $fixtureFrontend -StaticDirectory $fixtureStatic -MarkerPath $fixtureMarker)) `
        -Message "A stale frontend build without npm passed launcher validation."
    Assert-WzhkLauncher `
        -Condition (Test-WzhkFrontendPreparationAvailable -NpmPath "npm.cmd" -Root $fixtureFrontend -StaticDirectory $fixtureStatic -MarkerPath $fixtureMarker) `
        -Message "A stale frontend build with an available npm command was rejected."

    Write-WzhkLauncherTestFile -Path $fixtureSource -Content 'export const version = 1;'
    Write-WzhkAtomicJson -Path $fixtureMarker -Value ([ordered]@{
        schemaVersion = "2.0.0"
        sourceFingerprint = Get-WzhkFrontendBuildFingerprint -Root $fixtureFrontend
    })
    Write-WzhkLauncherTestFile -Path $fixturePublicAsset -Content '<svg><title>changed</title></svg>'
    Assert-WzhkLauncher `
        -Condition (-not (Test-WzhkFrontendBuildCurrent -Root $fixtureFrontend -StaticDirectory $fixtureStatic -MarkerPath $fixtureMarker)) `
        -Message "Changing a public asset left the prior build marked current."

    Write-WzhkLauncherTestFile -Path $fixturePublicAsset -Content '<svg></svg>'
    Write-WzhkAtomicJson -Path $fixtureMarker -Value ([ordered]@{
        schemaVersion = "2.0.0"
        sourceFingerprint = Get-WzhkFrontendBuildFingerprint -Root $fixtureFrontend
    })
    Write-WzhkLauncherTestFile -Path $fixtureIndex -Content '<main id="updated-root"></main>'
    Assert-WzhkLauncher `
        -Condition (-not (Test-WzhkFrontendBuildCurrent -Root $fixtureFrontend -StaticDirectory $fixtureStatic -MarkerPath $fixtureMarker)) `
        -Message "Changing frontend/index.html left the prior build marked current."
}
finally {
    $trimCharacters = [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd($trimCharacters)
    $temporaryPrefix = $temporaryRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $fingerprintFixture.StartsWith($temporaryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove launcher test fixture outside the temporary directory."
    }
    if (Test-Path -LiteralPath $fingerprintFixture -PathType Container) {
        Remove-Item -LiteralPath $fingerprintFixture -Recurse -Force
    }
}

$occupied = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 18765)
try {
    $occupied.Start()
    $selected = Find-WzhkAvailablePort
    Assert-WzhkLauncher -Condition ($selected -eq 18766) -Message "Dynamic port selection did not advance past an occupied preferred port."
}
finally { $occupied.Stop() }

$identity = [pscustomobject]@{
    instanceId = "launcher-test-instance"
    pid = 4242
    host = "127.0.0.1"
    port = 18766
}
$descriptor = [pscustomobject]@{
    instanceId = $identity.instanceId
    pid = $identity.pid
    host = $identity.host
    port = $identity.port
    url = "http://127.0.0.1:18766"
}
$nestedHealth = [pscustomobject]@{ status = "ok"; instance = $identity }
$flatHealth = [pscustomobject]@{
    status = "ok"
    instanceId = $identity.instanceId
    pid = $identity.pid
    host = $identity.host
    port = $identity.port
}
Assert-WzhkLauncher `
    -Condition (Test-WzhkHealthMatchesDescriptor -Descriptor $descriptor -Health $nestedHealth) `
    -Message "The launcher rejected the backend's nested health identity."
Assert-WzhkLauncher `
    -Condition (Test-WzhkHealthMatchesDescriptor -Descriptor $descriptor -Health $flatHealth) `
    -Message "The launcher rejected the compatible flat health identity."
$mismatchedHealth = [pscustomobject]@{
    status = "ok"
    instance = [pscustomobject]@{
        instanceId = "different-instance"
        pid = $identity.pid
        host = $identity.host
        port = $identity.port
    }
}
Assert-WzhkLauncher `
    -Condition (-not (Test-WzhkHealthMatchesDescriptor -Descriptor $descriptor -Health $mismatchedHealth)) `
    -Message "The launcher accepted a health response owned by a different instance."
$staleDescriptor = [pscustomobject]@{
    instanceId = "dead-instance"
    pid = 2147483646
    startedAt = "2000-01-01T00:00:00Z"
}
Assert-WzhkLauncher `
    -Condition (-not (Test-WzhkDescriptorProcessAlive -Descriptor $staleDescriptor)) `
    -Message "A dead descriptor PID was treated as a live backend owner."

$powershell = (Get-Command powershell.exe -CommandType Application -ErrorAction Stop).Source
$validationArguments = @(
    "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", $bootstrap, "-RepositoryRoot", $repositoryRoot, "-ValidateOnly"
)
$validationArgumentString = ($validationArguments | ForEach-Object { ConvertTo-WzhkNativeArgument -Value ([string]$_) }) -join " "
$validation = Invoke-WzhkBoundedProcess `
    -FilePath $powershell `
    -Arguments $validationArgumentString `
    -Label "Direct launcher -ValidateOnly"
Assert-WzhkLauncher -Condition ($validation.ExitCode -eq 0) -Message "Direct launcher -ValidateOnly failed with exit code $($validation.ExitCode)."

$commandProcessor = [string]$env:ComSpec
Assert-WzhkLauncher -Condition (-not [string]::IsNullOrWhiteSpace($commandProcessor)) -Message "ComSpec is unavailable."
$cmdArguments = '/d /s /c ""' + $primary.Replace('"', '""') + '" -ValidateOnly"'
$cmdValidation = Invoke-WzhkBoundedProcess `
    -FilePath $commandProcessor `
    -Arguments $cmdArguments `
    -Label "CMD launcher -ValidateOnly"
Assert-WzhkLauncher -Condition ($cmdValidation.ExitCode -eq 0) -Message "CMD launcher -ValidateOnly failed with exit code $($cmdValidation.ExitCode)."

$invalidCmdArguments = '/d /s /c ""' + $primary.Replace('"', '""') + '" -NotARealLauncherOption"'
$invalidValidation = Invoke-WzhkBoundedProcess `
    -FilePath $commandProcessor `
    -Arguments $invalidCmdArguments `
    -Label "CMD launcher invalid-argument failure" `
    -TimeoutMilliseconds 10000
Assert-WzhkLauncher -Condition ($invalidValidation.ExitCode -ne 0) -Message "Invalid launcher arguments unexpectedly succeeded."
$invalidOutput = $invalidValidation.StandardOutput + $invalidValidation.StandardError
Assert-WzhkLauncher -Condition (-not $invalidOutput.Contains("Press any key")) -Message "Invalid noninteractive launch reached a pause prompt."

Write-Host "React launcher PS 5.1 AST, quoting, runtime fallback, content fingerprints, stale-build prerequisites, CLI contract, dynamic-port fallback, descriptor identity, stale-PID, and bounded validation checks passed."
