[CmdletBinding()]
param(
    [string]$ComfyUIRoot = $(if ($env:TRACKPROMPT_COMFYUI_ROOT) { $env:TRACKPROMPT_COMFYUI_ROOT } else { 'D:\TrackPrompt-ComfyUI' }),
    [int]$Port = 8188,
    [int]$StartupTimeoutSeconds = 240
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Port -lt 1 -or $Port -gt 65535) {
    throw 'Port must be in the range 1..65535.'
}
$root = [System.IO.Path]::GetFullPath($ComfyUIRoot).TrimEnd('\')
$python = Join-Path $root '.venv\Scripts\python.exe'
$main = Join-Path $root 'main.py'
$gguf = Join-Path $root 'custom_nodes\ComfyUI-GGUF'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $main -PathType Leaf)) {
    throw 'Managed ComfyUI or its Python environment is unavailable.'
}
if (-not (Test-Path -LiteralPath $gguf -PathType Container)) {
    throw 'The pinned ComfyUI-GGUF custom node is unavailable.'
}

$baseUri = "http://127.0.0.1:$Port"
function Invoke-ComfyJson {
    param([Parameter(Mandatory = $true)][string]$Path)
    return Invoke-RestMethod -Method Get -Uri "$baseUri$Path" -TimeoutSec 10
}
function Test-ComfyReady {
    try {
        $stats = Invoke-ComfyJson -Path '/system_stats'
        $objects = Invoke-ComfyJson -Path '/object_info'
        $queue = Invoke-ComfyJson -Path '/queue'
        return $null -ne $stats -and @($objects.PSObject.Properties).Count -gt 0 -and $null -ne $queue
    } catch {
        return $false
    }
}
function Test-ComfyWebSocket {
    $socket = [System.Net.WebSockets.ClientWebSocket]::new()
    $cancellation = [System.Threading.CancellationTokenSource]::new([TimeSpan]::FromSeconds(10))
    try {
        $clientId = [Guid]::NewGuid().ToString()
        $socket.ConnectAsync([Uri]"ws://127.0.0.1:$Port/ws?clientId=$clientId", $cancellation.Token).GetAwaiter().GetResult()
        return $socket.State -eq [System.Net.WebSockets.WebSocketState]::Open
    } catch {
        return $false
    } finally {
        $socket.Dispose()
        $cancellation.Dispose()
    }
}

if ((Test-ComfyReady) -and (Test-ComfyWebSocket)) {
    Write-Host "ComfyUI is already ready at $baseUri"
    exit 0
}
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port $Port is already occupied by a service that is not a ready ComfyUI API."
}

$logRoot = Join-Path $root 'trackprompt\logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$stamp = [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')
$stdout = Join-Path $logRoot "comfyui-$stamp.stdout.log"
$stderr = Join-Path $logRoot "comfyui-$stamp.stderr.log"
$arguments = @(
    $main,
    '--listen', '127.0.0.1',
    '--port', [string]$Port,
    '--cuda-device', '0',
    '--disable-auto-launch',
    '--disable-manager-ui',
    '--disable-api-nodes',
    '--disable-metadata',
    '--preview-method', 'none',
    '--enable-dynamic-vram',
    '--async-offload', '2',
    '--reserve-vram', '1.0'
)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
$pidRecord = [ordered]@{
    pid = $process.Id
    startedAt = [DateTimeOffset]::UtcNow.ToString('o')
    endpoint = $baseUri
    stdout = $stdout
    stderr = $stderr
}
$pidRecord | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logRoot 'current-process.json') -Encoding UTF8

$deadline = [DateTimeOffset]::UtcNow.AddSeconds($StartupTimeoutSeconds)
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    if ($process.HasExited) {
        $tail = if (Test-Path -LiteralPath $stderr) { (Get-Content -LiteralPath $stderr -Tail 80) -join [Environment]::NewLine } else { '' }
        throw "ComfyUI exited during startup with code $($process.ExitCode). $tail"
    }
    if ((Test-ComfyReady) -and (Test-ComfyWebSocket)) {
        Write-Host "ComfyUI ready at $baseUri (PID $($process.Id))"
        Write-Host "stdout=$stdout"
        Write-Host "stderr=$stderr"
        exit 0
    }
    Start-Sleep -Seconds 2
}
throw "ComfyUI did not become API-ready within $StartupTimeoutSeconds seconds. Logs: $stdout ; $stderr"
