[CmdletBinding()]
param(
    [switch]$Start,
    [switch]$Once,
    [switch]$CheckOnly,
    [ValidatePattern('^[0-9a-f]{12}$')][string]$RunId = '344ab3d2444c'
)
$ErrorActionPreference = 'Stop'
if (([int]$Start.IsPresent + [int]$Once.IsPresent + [int]$CheckOnly.IsPresent) -gt 1) {
    throw 'Choose Start, Once, or CheckOnly.'
}
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe'
$entrypoint = Join-Path $repository 'tools/hayale-wnp.py'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'The existing stable Python 3.12 installation is required.'
}
if (-not $Start) {
    $mode = if ($Once) { '--once' } else { '--check-only' }
    & $python -B $entrypoint $mode --run-id $RunId
    exit $LASTEXITCODE
}
# Start is explicit; it has no render or provider-control capability.
$runtime = Join-Path $repository '.trackprompt-data/hayale-v21/wnp-observer'
$worker = Join-Path $runtime 'worker.json'
if (Test-Path -LiteralPath $worker -PathType Leaf) {
    $record = Get-Content -LiteralPath $worker -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($existing) {
        $expected = if ($record.started_at -is [DateTime]) { $record.started_at.ToUniversalTime() } else { [DateTimeOffset]::Parse([string]$record.started_at).UtcDateTime }
        if ([Math]::Abs(($existing.StartTime.ToUniversalTime() - $expected).TotalMilliseconds) -lt 10 -and $record.run -eq $RunId) {
            throw 'The recorded observer is already running; preserve it.'
        }
    }
}
& $python -B $entrypoint --check-only --run-id $RunId
if ($LASTEXITCODE -ne 0) { throw 'Observer checks failed; nothing was launched.' }
# The native helper explicitly breaks away from any disposable caller Job Object.
# It refuses launch when breakaway is denied; there is no inherited-job fallback.
& $python -B $entrypoint --launch --run-id $RunId
exit $LASTEXITCODE
