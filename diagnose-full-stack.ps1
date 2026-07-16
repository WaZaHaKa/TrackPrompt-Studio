#requires -Version 5.1
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
Set-Location -LiteralPath $RepoRoot
$compose = @("compose", "-f", "compose.yaml", "-f", "compose.full-gpu.yaml")
& nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader
& docker @($compose + @("ps"))
& docker @($compose + @("exec", "-T", "backend", "python", "-m", "app.diagnostics.capabilities"))
& docker @($compose + @("exec", "-T", "backend", "python", "-m", "app.diagnostics.gpu"))
& docker @($compose + @("logs", "--tail", "100", "backend", "prompt-writer"))
