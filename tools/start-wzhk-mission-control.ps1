[CmdletBinding()]
param(
    [string]$RepositoryRoot = "",
    [ValidateRange(1024, 65535)][int]$PreferredPort = 8765,
    [switch]$NoBrowser,
    [switch]$NoNativeErrorDialog,
    [switch]$ValidateOnly,
    [switch]$ImportOnly
)

# TrackPrompt bootstrap: explicitly load Microsoft.PowerShell.Utility.
$script:TrackPromptUtilityManifest = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1'

if (-not (Test-Path -LiteralPath $script:TrackPromptUtilityManifest -PathType Leaf)) {
    throw "Required PowerShell utility module was not found: $script:TrackPromptUtilityManifest"
}

Import-Module `
    -Name $script:TrackPromptUtilityManifest `
    -Force `
    -ErrorAction Stop


Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Split-Path -Parent $PSScriptRoot
}
$RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)
$backendRoot = Join-Path $RepositoryRoot "backend"
$frontendRoot = Join-Path $RepositoryRoot "frontend"
$staticRoot = Join-Path $frontendRoot "dist"
$stateRoot = Join-Path $RepositoryRoot ".trackprompt-data\mission-control"
$descriptorPath = Join-Path $stateRoot "instance.json"
$stdoutPath = Join-Path $stateRoot "backend.stdout.log"
$stderrPath = Join-Path $stateRoot "backend.stderr.log"
$serverModule = Join-Path $backendRoot "app\mission_control\server.py"
$legacyLauncher = Join-Path $RepositoryRoot "WZHK-Media-Launcher-Legacy.cmd"
$frontendPackage = Join-Path $frontendRoot "package.json"
$buildMarker = Join-Path $staticRoot ".mission-control-build.json"
$script:LauncherMutex = $null
$script:LauncherMutexOwned = $false
$script:LaunchId = ""
$script:SpawnedProcessId = 0
$script:SpawnedPort = 0
$script:SpawnedStartedAt = [DateTime]::MinValue

function Get-WzhkPropertyValue {
    param(
        [AllowNull()][object]$InputObject,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if ($null -eq $InputObject) { return $null }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Read-WzhkJsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -ErrorAction Stop
    }
    catch { return $null }
}

function Write-WzhkAtomicJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$Value
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $temporary = Join-Path $parent ([IO.Path]::GetRandomFileName())
    try {
        [IO.File]::WriteAllText(
            $temporary,
            ($Value | ConvertTo-Json -Depth 8),
            (New-Object Text.UTF8Encoding($false))
        )
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function Show-WzhkStartupError {
    param([Parameter(Mandatory = $true)][string]$Message)

    [Console]::Error.WriteLine($Message)
    if ($NoNativeErrorDialog) { return }
    try {
        Add-Type -AssemblyName PresentationFramework -ErrorAction Stop
        [System.Windows.MessageBox]::Show(
            $Message,
            "WZHK Media Mission Control",
            [System.Windows.MessageBoxButton]::OK,
            [System.Windows.MessageBoxImage]::Error
        ) | Out-Null
    }
    catch {
        # The console error remains available on Windows editions without WPF.
    }
}

function ConvertTo-WzhkNativeArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $builder = New-Object Text.StringBuilder
    $null = $builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $backslashes += 1; continue }
        if ($character -eq '"') {
            if ($backslashes -gt 0) { $null = $builder.Append(('\' * ($backslashes * 2))) }
            $null = $builder.Append('\"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) { $null = $builder.Append(('\' * $backslashes)); $backslashes = 0 }
        $null = $builder.Append($character)
    }
    if ($backslashes -gt 0) { $null = $builder.Append(('\' * ($backslashes * 2))) }
    $null = $builder.Append('"')
    return $builder.ToString()
}

function Test-WzhkPythonRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateRange(1000, 30000)][int]$TimeoutMilliseconds = 15000
    )

    if ([string]::IsNullOrWhiteSpace($Path) -or
        -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }

    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Path
    $startInfo.Arguments = "-m app.mission_control.server --help"
    $startInfo.WorkingDirectory = $backendRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { return $false }
        $process.StandardInput.Close()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            try { $process.Kill() } catch { }
            try { $process.WaitForExit() } catch { }
            return $false
        }
        $process.WaitForExit()
        $null = $stdoutTask.Result
        $null = $stderrTask.Result
        return $process.ExitCode -eq 0
    }
    catch { return $false }
    finally { $process.Dispose() }
}

function Resolve-WzhkPython {
    param([AllowEmptyCollection()][string[]]$Candidates = @())

    if (@($Candidates).Count -eq 0) {
        $Candidates = @(
            (Join-Path $backendRoot ".venv\Scripts\python.exe"),
            (Join-Path $RepositoryRoot ".venv\Scripts\python.exe")
        )
        foreach ($name in @("python.exe", "python")) {
            $command = Get-Command $name -ErrorAction SilentlyContinue
            if ($null -ne $command) { $Candidates += [string]$command.Source }
        }
    }

    $seen = @{}
    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $normalized = [IO.Path]::GetFullPath($candidate)
        if ($seen.ContainsKey($normalized)) { continue }
        $seen[$normalized] = $true
        if (Test-WzhkPythonRuntime -Path $normalized) { return $normalized }
    }
    return ""
}

function Resolve-WzhkNpm {
    foreach ($name in @("npm.cmd", "npm")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $command) { return $command.Source }
    }
    return ""
}

function Get-WzhkFrontendBuildInputFiles {
    param([string]$Root = $frontendRoot)

    $files = @(
        Get-ChildItem -LiteralPath $Root -File -Force -ErrorAction SilentlyContinue
        foreach ($directoryName in @("src", "public")) {
            $directory = Join-Path $Root $directoryName
            if (Test-Path -LiteralPath $directory -PathType Container) {
                Get-ChildItem -LiteralPath $directory -Recurse -File -Force -ErrorAction Stop
            }
        }
    )
    return @($files | Sort-Object -Property FullName -Unique)
}

function Get-WzhkFrontendBuildFingerprint {
    param([string]$Root = $frontendRoot)

    $trimCharacters = [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd($trimCharacters)
    $rootPrefix = $fullRoot + [IO.Path]::DirectorySeparatorChar
    $entries = @(
        foreach ($file in @(Get-WzhkFrontendBuildInputFiles -Root $fullRoot)) {
            $relativePath = $file.FullName.Substring($rootPrefix.Length).Replace('\', '/')
            $contentHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
            "$relativePath`:$contentHash"
        }
    )
    $payload = [Text.Encoding]::UTF8.GetBytes(($entries -join "`n"))
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($payload))).Replace("-", "").ToLowerInvariant()
    }
    finally { $sha256.Dispose() }
}

function Test-WzhkFrontendBuildCurrent {
    param(
        [string]$Root = $frontendRoot,
        [string]$StaticDirectory = $staticRoot,
        [string]$MarkerPath = $buildMarker
    )

    $indexPath = Join-Path $StaticDirectory "index.html"
    if (
        -not (Test-Path -LiteralPath $indexPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)
    ) { return $false }
    try {
        $marker = Get-Content -Raw -LiteralPath $MarkerPath | ConvertFrom-Json -ErrorAction Stop
        $recordedFingerprint = [string](Get-WzhkPropertyValue -InputObject $marker -Name "sourceFingerprint")
        if ([string]::IsNullOrWhiteSpace($recordedFingerprint)) { return $false }
        $currentFingerprint = Get-WzhkFrontendBuildFingerprint -Root $Root
        return [string]::Equals(
            $recordedFingerprint,
            $currentFingerprint,
            [StringComparison]::OrdinalIgnoreCase
        )
    }
    catch { return $false }
}

function Test-WzhkFrontendPreparationAvailable {
    param(
        [AllowEmptyString()][string]$NpmPath,
        [string]$Root = $frontendRoot,
        [string]$StaticDirectory = $staticRoot,
        [string]$MarkerPath = $buildMarker
    )

    if (Test-WzhkFrontendBuildCurrent -Root $Root -StaticDirectory $StaticDirectory -MarkerPath $MarkerPath) {
        return $true
    }
    return -not [string]::IsNullOrWhiteSpace($NpmPath)
}

function Build-WzhkFrontend {
    if (Test-WzhkFrontendBuildCurrent) { return }
    $npm = Resolve-WzhkNpm
    if ([string]::IsNullOrWhiteSpace($npm)) {
        throw "Node.js/npm is required to build the local Mission Control interface. Install Node.js and try again."
    }
    Write-Host "Preparing Mission Control..."
    Push-Location $frontendRoot
    try {
        # Windows PowerShell 5.1 turns redirected native stderr into error
        # records. Keep collecting it so a failed build produces one bounded,
        # useful startup error instead of escaping at the first stderr line.
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $output = @(& $npm run build 2>&1)
            $buildExitCode = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previousErrorActionPreference }
        if ($buildExitCode -ne 0) {
            throw "The React interface build failed.`n$($output -join [Environment]::NewLine)"
        }
    }
    finally { Pop-Location }
    Write-WzhkAtomicJson -Path $buildMarker -Value ([ordered]@{
        schemaVersion = "2.0.0"
        sourceFingerprint = Get-WzhkFrontendBuildFingerprint
        builtAt = [DateTime]::UtcNow.ToString("o")
    })
}

function Test-WzhkLoopbackPortFree {
    param([Parameter(Mandatory = $true)][int]$Port)
    $listener = $null
    try {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    }
    catch { return $false }
    finally {
        if ($null -ne $listener) { $listener.Stop() }
    }
}

function Move-WzhkPreviousBackendLogs {
    $suffix = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmssfff") + "-" + [Guid]::NewGuid().ToString("N").Substring(0, 8)
    foreach ($path in @($stdoutPath, $stderrPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $directory = Split-Path -Parent $path
        $name = [IO.Path]::GetFileNameWithoutExtension($path)
        $extension = [IO.Path]::GetExtension($path)
        $archive = Join-Path $directory ($name + "." + $suffix + $extension)
        Move-Item -LiteralPath $path -Destination $archive
    }
}

function Find-WzhkAvailablePort {
    for ($port = $PreferredPort; $port -le [Math]::Min(65535, $PreferredPort + 100); $port += 1) {
        if (Test-WzhkLoopbackPortFree -Port $port) { return $port }
    }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return [int]$listener.LocalEndpoint.Port
    }
    finally { $listener.Stop() }
}

function Test-WzhkHealthMatchesDescriptor {
    param(
        [AllowNull()][object]$Descriptor,
        [AllowNull()][object]$Health
    )

    if ($null -eq $Descriptor -or $null -eq $Health) { return $false }
    try {
        $portValue = Get-WzhkPropertyValue -InputObject $Descriptor -Name "port"
        $pidValue = Get-WzhkPropertyValue -InputObject $Descriptor -Name "pid"
        $instanceId = [string](Get-WzhkPropertyValue -InputObject $Descriptor -Name "instanceId")
        $descriptorHost = [string](Get-WzhkPropertyValue -InputObject $Descriptor -Name "host")
        $port = [int]$portValue
        $processId = [int]$pidValue
        if ($port -lt 1024 -or $port -gt 65535) { return $false }
        if ($processId -le 0) { return $false }
        if ([string]::IsNullOrWhiteSpace($instanceId)) { return $false }
        if ([string]::IsNullOrWhiteSpace($descriptorHost)) { $descriptorHost = "127.0.0.1" }
        if ($descriptorHost -ne "127.0.0.1") { return $false }
        if ([string](Get-WzhkPropertyValue -InputObject $Health -Name "status") -ne "ok") { return $false }
        $healthIdentity = Get-WzhkPropertyValue -InputObject $Health -Name "instance"
        if ($null -eq $healthIdentity) { $healthIdentity = $Health }
        if ([string](Get-WzhkPropertyValue -InputObject $healthIdentity -Name "instanceId") -ne $instanceId) { return $false }
        if ([int](Get-WzhkPropertyValue -InputObject $healthIdentity -Name "pid") -ne $processId) { return $false }
        if ([string](Get-WzhkPropertyValue -InputObject $healthIdentity -Name "host") -ne $descriptorHost) { return $false }
        if ([int](Get-WzhkPropertyValue -InputObject $healthIdentity -Name "port") -ne $port) { return $false }
        return $true
    }
    catch { return $false }
}

function Get-WzhkHealthyInstance {
    $descriptor = Read-WzhkJsonFile -Path $descriptorPath
    if ($null -eq $descriptor) { return $null }
    try {
        $port = [int](Get-WzhkPropertyValue -InputObject $descriptor -Name "port")
        if ($port -lt 1024 -or $port -gt 65535) { return $null }
        $expectedUrl = "http://127.0.0.1:$port"
        if ([string](Get-WzhkPropertyValue -InputObject $descriptor -Name "url") -ne $expectedUrl) { return $null }
        $health = Invoke-RestMethod -Method Get -Uri "$expectedUrl/api/mission-control/health" -TimeoutSec 2
        if (-not (Test-WzhkHealthMatchesDescriptor -Descriptor $descriptor -Health $health)) { return $null }
        return [pscustomobject]@{ Url = $expectedUrl; Descriptor = $descriptor; Health = $health }
    }
    catch { return $null }
}

function Test-WzhkDescriptorProcessAlive {
    param([AllowNull()][object]$Descriptor)

    if ($null -eq $Descriptor) { return $false }
    try {
        $recordedPid = [int](Get-WzhkPropertyValue -InputObject $Descriptor -Name "pid")
        if ($recordedPid -le 0) { return $false }
        $recordedProcess = Get-Process -Id $recordedPid -ErrorAction SilentlyContinue
        if ($null -eq $recordedProcess) { return $false }

        $startedAtValue = [string](Get-WzhkPropertyValue -InputObject $Descriptor -Name "startedAt")
        if ([string]::IsNullOrWhiteSpace($startedAtValue)) {
            return -not [string]::IsNullOrWhiteSpace(
                [string](Get-WzhkPropertyValue -InputObject $Descriptor -Name "instanceId")
            )
        }
        $recordedStart = [DateTimeOffset]::Parse(
            $startedAtValue,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).UtcDateTime
        $actualStart = $recordedProcess.StartTime.ToUniversalTime()
        return [Math]::Abs(($recordedStart - $actualStart).TotalSeconds) -le 300
    }
    catch { return $false }
}

function Wait-WzhkHealthyInstance {
    param(
        [Parameter(Mandatory = $true)][DateTime]$Deadline,
        [AllowNull()][Diagnostics.Process]$Process
    )

    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($null -ne $Process -and $Process.HasExited) { return $null }
        $healthy = Get-WzhkHealthyInstance
        if ($null -ne $healthy) { return $healthy }
        Start-Sleep -Milliseconds 400
    }
    return $null
}

function Remove-WzhkOwnedDescriptor {
    param(
        [AllowEmptyString()][string]$LaunchId = "",
        [int]$ProcessId = 0
    )

    $descriptor = Read-WzhkJsonFile -Path $descriptorPath
    if ($null -eq $descriptor) { return }
    $descriptorLaunchId = [string](Get-WzhkPropertyValue -InputObject $descriptor -Name "launchId")
    $descriptorPid = 0
    try { $descriptorPid = [int](Get-WzhkPropertyValue -InputObject $descriptor -Name "pid") } catch { }
    $owned = (
        -not [string]::IsNullOrWhiteSpace($LaunchId) -and
        $descriptorLaunchId -eq $LaunchId
    ) -or ($ProcessId -gt 0 -and $descriptorPid -eq $ProcessId)
    if ($owned -and (Test-Path -LiteralPath $descriptorPath -PathType Leaf)) {
        Remove-Item -LiteralPath $descriptorPath -Force -ErrorAction SilentlyContinue
    }
}

function Stop-WzhkProcessBounded {
    param([int]$ProcessId)

    if ($ProcessId -le 0) { return }
    $target = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $target) { return }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    try { $null = $target.WaitForExit(5000) } catch { }
}

function Get-WzhkOwnedBackendPid {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][DateTime]$LaunchStartedAt
    )

    $descriptor = Read-WzhkJsonFile -Path $descriptorPath
    if ($null -eq $descriptor) { return 0 }
    try {
        if ([int](Get-WzhkPropertyValue -InputObject $descriptor -Name "port") -ne $Port) { return 0 }
        $descriptorPid = [int](Get-WzhkPropertyValue -InputObject $descriptor -Name "pid")
        if ($descriptorPid -le 0) { return 0 }
        $startedAt = [DateTimeOffset]::Parse(
            [string](Get-WzhkPropertyValue -InputObject $descriptor -Name "startedAt"),
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).UtcDateTime
        if ($startedAt -lt $LaunchStartedAt.AddSeconds(-5)) { return 0 }
        return $descriptorPid
    }
    catch { return 0 }
}

function Get-WzhkBackendDiagnostics {
    if (Test-Path -LiteralPath $stderrPath -PathType Leaf) {
        return ((Get-Content -LiteralPath $stderrPath -Tail 12 -ErrorAction SilentlyContinue) -join [Environment]::NewLine)
    }
    return "No backend diagnostics were written."
}

function Test-WzhkPortConflictDiagnostics {
    param([AllowEmptyString()][string]$Details)
    return $Details -match '(?i)(address already in use|only one usage of each socket address|\b10048\b|\berrno 98\b)'
}

function New-WzhkLauncherMutex {
    try { return [Threading.Mutex]::new($false, "Global\WZHKMediaMissionControlLauncher") }
    catch { return [Threading.Mutex]::new($false, "Local\WZHKMediaMissionControlLauncher") }
}

function Open-WzhkBrowser {
    param([Parameter(Mandatory = $true)][string]$Url)
    if ($NoBrowser) { return }
    try { Start-Process -FilePath $Url -ErrorAction Stop | Out-Null }
    catch {
        throw "Mission Control is running at $Url, but Windows could not open the default browser. Open that address manually."
    }
}

function Invoke-WzhkLauncherValidation {
    $failures = New-Object Collections.Generic.List[string]
    foreach ($required in @(
        $serverModule,
        $frontendPackage,
        $legacyLauncher,
        (Join-Path $RepositoryRoot "WZHK-Media-Launcher.cmd")
    )) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            $failures.Add("Required file is missing: $required")
        }
    }
    if ([string]::IsNullOrWhiteSpace((Resolve-WzhkPython))) {
        $failures.Add("A usable Python runtime with the Mission Control backend dependencies was not found.")
    }
    $npm = Resolve-WzhkNpm
    if (-not (Test-WzhkFrontendPreparationAvailable -NpmPath $npm)) {
        $failures.Add("The React build is absent or stale and npm was not found.")
    }
    $tokens = $null
    $parseErrors = $null
    [Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$tokens, [ref]$parseErrors) | Out-Null
    foreach ($parseError in @($parseErrors)) { $failures.Add([string]$parseError.Message) }

    Write-Host "WZHK Mission Control launcher validation"
    Write-Host "  Host: 127.0.0.1 only"
    Write-Host "  Preferred port: $PreferredPort (automatic fallback enabled)"
    Write-Host "  Legacy fallback: $(if (Test-Path -LiteralPath $legacyLauncher) { 'available' } else { 'missing' })"
    if ($failures.Count -gt 0) {
        Write-Host "  Status: FAILED" -ForegroundColor Red
        foreach ($failure in $failures) { Write-Host "  - $failure" -ForegroundColor Red }
        return $false
    }
    Write-Host "  Status: OK" -ForegroundColor Green
    return $true
}

if ($ImportOnly) { return }

if ($ValidateOnly) {
    if (Invoke-WzhkLauncherValidation) { exit 0 }
    exit 1
}

try {
    $script:LauncherMutex = New-WzhkLauncherMutex
    try {
        $script:LauncherMutexOwned = $script:LauncherMutex.WaitOne([TimeSpan]::FromSeconds(45))
    }
    catch [Threading.AbandonedMutexException] {
        $script:LauncherMutexOwned = $true
    }
    if (-not $script:LauncherMutexOwned) {
        throw "Another Mission Control launcher is taking too long to finish. Try again in a moment."
    }

    $existing = Get-WzhkHealthyInstance
    if ($null -ne $existing) {
        Write-Host "Mission Control is already running."
        Write-Host "Opening Mission Control..."
        Open-WzhkBrowser -Url $existing.Url
        exit 0
    }
    if (Test-Path -LiteralPath $descriptorPath -PathType Leaf) {
        $unhealthyDescriptor = Read-WzhkJsonFile -Path $descriptorPath
        if (Test-WzhkDescriptorProcessAlive -Descriptor $unhealthyDescriptor) {
            $existing = Wait-WzhkHealthyInstance -Deadline ([DateTime]::UtcNow.AddSeconds(8)) -Process $null
            if ($null -ne $existing) {
                Write-Host "Mission Control recovered."
                Write-Host "Opening Mission Control..."
                Open-WzhkBrowser -Url $existing.Url
                exit 0
            }
            throw "An existing Mission Control backend is still running but is not responding. Its process was left untouched. Try again in a moment."
        }
    }
    if (Test-Path -LiteralPath $descriptorPath -PathType Leaf) {
        Remove-Item -LiteralPath $descriptorPath -Force
    }

    if (-not (Test-Path -LiteralPath $serverModule -PathType Leaf)) {
        throw "The Mission Control backend is missing. Reinstall or repair TrackPrompt Studio."
    }
    $python = Resolve-WzhkPython
    if ([string]::IsNullOrWhiteSpace($python)) {
        throw "A usable local Python runtime with the Mission Control backend dependencies is unavailable. Run the TrackPrompt setup, then try again."
    }
    Build-WzhkFrontend

    if (-not (Test-Path -LiteralPath $stateRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    }
    $healthy = $null
    $process = $null
    for ($attempt = 1; $attempt -le 3 -and $null -eq $healthy; $attempt += 1) {
        $port = Find-WzhkAvailablePort
        $url = "http://127.0.0.1:$port"
        $script:LaunchId = [Guid]::NewGuid().ToString("N")
        $script:SpawnedProcessId = 0
        $launchStartedAt = [DateTime]::UtcNow
        $script:SpawnedPort = $port
        $script:SpawnedStartedAt = $launchStartedAt
        Move-WzhkPreviousBackendLogs
        Write-Host "Starting WZHK Media Mission Control..."
        $arguments = @(
            "-m", "app.mission_control.server",
            "--host", "127.0.0.1",
            "--port", [string]$port,
            "--static-dir", $staticRoot,
            "--instance-descriptor", $descriptorPath
        )
        $argumentString = ($arguments | ForEach-Object { ConvertTo-WzhkNativeArgument -Value ([string]$_) }) -join " "
        Write-WzhkAtomicJson -Path $descriptorPath -Value ([ordered]@{
            schemaVersion = "1.0.0"
            kind = "trackprompt-mission-control-starting"
            status = "starting"
            launchId = $script:LaunchId
            launcherPid = $PID
            host = "127.0.0.1"
            port = $port
            url = $url
            startedAt = $launchStartedAt.ToString("o")
        })
        try {
            $process = Start-Process -FilePath $python -ArgumentList $argumentString -WorkingDirectory $backendRoot `
                -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
            $script:SpawnedProcessId = $process.Id
        }
        catch {
            Remove-WzhkOwnedDescriptor -LaunchId $script:LaunchId
            throw
        }

        $healthy = Wait-WzhkHealthyInstance -Deadline ([DateTime]::UtcNow.AddSeconds(45)) -Process $process
        if ($null -ne $healthy) {
            # A Windows venv launcher may remain as a small parent process while
            # the descriptor correctly identifies the child Python server.
            break
        }

        $backendProcessId = Get-WzhkOwnedBackendPid -Port $port -LaunchStartedAt $launchStartedAt
        Stop-WzhkProcessBounded -ProcessId $backendProcessId
        Stop-WzhkProcessBounded -ProcessId $process.Id
        $details = Get-WzhkBackendDiagnostics
        Remove-WzhkOwnedDescriptor -LaunchId $script:LaunchId -ProcessId $backendProcessId
        if ($attempt -lt 3 -and (Test-WzhkPortConflictDiagnostics -Details $details)) {
            Write-Host "The selected local port became busy. Trying the next available port..."
            continue
        }
        throw "Mission Control did not become ready.`n`n$details"
    }
    if ($null -eq $healthy) {
        throw "Mission Control did not become ready after trying three local ports."
    }

    # Once health and descriptor identity agree, the backend owns its lease.
    # A later browser-open error must not remove that live descriptor.
    $script:LaunchId = ""
    $script:SpawnedProcessId = 0
    $script:SpawnedPort = 0
    $script:SpawnedStartedAt = [DateTime]::MinValue
    Write-Host "Backend ready."
    Write-Host "Opening Mission Control..."
    Open-WzhkBrowser -Url $healthy.Url
    exit 0
}
catch {
    if ($script:SpawnedProcessId -gt 0 -and $script:SpawnedPort -gt 0) {
        $ownedBackendPid = Get-WzhkOwnedBackendPid `
            -Port $script:SpawnedPort `
            -LaunchStartedAt $script:SpawnedStartedAt
        Stop-WzhkProcessBounded -ProcessId $ownedBackendPid
        Stop-WzhkProcessBounded -ProcessId $script:SpawnedProcessId
        Remove-WzhkOwnedDescriptor -LaunchId $script:LaunchId -ProcessId $ownedBackendPid
    }
    else {
        Remove-WzhkOwnedDescriptor -LaunchId $script:LaunchId
    }
    Show-WzhkStartupError -Message ([string]$_.Exception.Message)
    exit 1
}
finally {
    if ($null -ne $script:LauncherMutex) {
        if ($script:LauncherMutexOwned) {
            try { $script:LauncherMutex.ReleaseMutex() } catch { }
        }
        $script:LauncherMutex.Dispose()
    }
}
