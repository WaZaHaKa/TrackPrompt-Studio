[CmdletBinding()]
param(
    [ValidateSet(
        "help", "setup", "dev-backend", "dev-frontend", "fixtures", "test",
        "lint", "typecheck", "build", "e2e", "check", "compose-config",
        "compose-up", "compose-down"
    )]
    [string] $Task = "help",
    [string] $Python = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$PythonCommand = $Python
if (-not [System.IO.Path]::IsPathRooted($Python)) {
    $RepositoryPython = Join-Path $RepoRoot $Python
    if (Test-Path -LiteralPath $RepositoryPython -PathType Leaf) {
        $PythonCommand = (Resolve-Path -LiteralPath $RepositoryPython).Path
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory)] [scriptblock] $Command,
        [Parameter(Mandatory)] [string] $Description
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Invoke-InDirectory {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [scriptblock] $Command,
        [Parameter(Mandatory)] [string] $Description
    )

    Push-Location (Join-Path $RepoRoot $Path)
    try {
        Invoke-Checked -Command $Command -Description $Description
    }
    finally {
        Pop-Location
    }
}

function Install-Dependencies {
    Invoke-InDirectory "backend" { & $PythonCommand -m pip install -e ".[dev]" } "Backend setup"
    Invoke-InDirectory "frontend" { npm.cmd ci } "Frontend setup"
}

function Invoke-Tests {
    Invoke-InDirectory "backend" { & $PythonCommand -m pytest } "Backend tests"
    Invoke-InDirectory "frontend" { npm.cmd test -- --run } "Frontend tests"
}

function Invoke-Lint {
    Invoke-InDirectory "backend" { & $PythonCommand -m ruff check . } "Backend lint"
    Invoke-InDirectory "frontend" { npm.cmd run lint } "Frontend lint"
}

function Invoke-Typecheck {
    Invoke-InDirectory "backend" { & $PythonCommand -m mypy app } "Backend type check"
    Invoke-InDirectory "frontend" { npm.cmd run typecheck } "Frontend type check"
}

function Invoke-Build {
    Invoke-InDirectory "frontend" { npm.cmd run build } "Frontend build"
}

function Set-DefaultDataEnvironment {
    if (-not (Test-Path Env:TRACKPROMPT_DATA_DIR)) {
        $env:TRACKPROMPT_DATA_DIR = Join-Path $RepoRoot ".trackprompt-data"
    }
    if (-not (Test-Path Env:MODEL_CACHE_DIR)) {
        $env:MODEL_CACHE_DIR = Join-Path $env:TRACKPROMPT_DATA_DIR "models"
    }
}

switch ($Task) {
    "help" {
        @"
TrackPrompt Studio tasks:
  setup           Install backend and frontend development dependencies
  dev-backend     Run FastAPI on http://localhost:8000
  dev-frontend    Run Vite on http://localhost:5173
  fixtures        Generate local synthetic audio fixtures
  test            Run backend and frontend unit tests
  lint            Run Python and TypeScript linting
  typecheck       Run Python and TypeScript type checks
  build           Build the production frontend
  e2e             Run browser end-to-end tests
  check           Run required non-E2E checks
  compose-config  Validate the Compose model
  compose-up      Build and start the Docker application
  compose-down    Stop the Docker application
"@
    }
    "setup" { Install-Dependencies }
    "dev-backend" {
        Set-DefaultDataEnvironment
        Invoke-InDirectory "backend" {
            & $PythonCommand -m uvicorn app.main:app --host 127.0.0.1 --port 8000
        } "Backend development server"
    }
    "dev-frontend" { Invoke-InDirectory "frontend" { npm.cmd run dev } "Frontend development server" }
    "fixtures" {
        Push-Location $RepoRoot
        try {
            Invoke-Checked { & $PythonCommand tools/generate_test_audio.py } "Fixture generation"
        }
        finally {
            Pop-Location
        }
    }
    "test" { Invoke-Tests }
    "lint" { Invoke-Lint }
    "typecheck" { Invoke-Typecheck }
    "build" { Invoke-Build }
    "e2e" {
        $env:PYTHON = $PythonCommand
        Invoke-InDirectory "frontend" { npm.cmd run test:e2e } "End-to-end tests"
    }
    "compose-config" {
        Push-Location $RepoRoot
        try { Invoke-Checked { docker compose config } "Compose validation" }
        finally { Pop-Location }
    }
    "check" {
        Invoke-Tests
        Invoke-Lint
        Invoke-Typecheck
        Invoke-Build
        Push-Location $RepoRoot
        try { Invoke-Checked { docker compose config } "Compose validation" }
        finally { Pop-Location }
    }
    "compose-up" {
        Push-Location $RepoRoot
        try { Invoke-Checked { docker compose up --build } "Compose startup" }
        finally { Pop-Location }
    }
    "compose-down" {
        Push-Location $RepoRoot
        try { Invoke-Checked { docker compose down } "Compose shutdown" }
        finally { Pop-Location }
    }
}
