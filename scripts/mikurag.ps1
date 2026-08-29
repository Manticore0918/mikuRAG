param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "checks", "migrations", "seed", "smoke", "restart-smoke", "compose-smoke", "evaluate")]
    [string]$Command = "checks"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Assert-NativeSuccess([string]$Description) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

Push-Location $ProjectRoot
try {
    switch ($Command) {
        "setup" {
            if (-not (Test-Path ".env")) {
                throw "Copy .env.example to .env and replace every placeholder first."
            }
            if (-not (Test-Path ".venv\Scripts\python.exe")) {
                python -m venv .venv
            }
            .\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
            Assert-NativeSuccess "Backend dependency installation"
            npm --prefix frontend ci
            Assert-NativeSuccess "Frontend dependency installation"
            docker compose --profile tools run --rm migrate
            Assert-NativeSuccess "Database migration"
            docker compose up --build -d
            Assert-NativeSuccess "Compose startup"
        }
        "checks" {
            Push-Location "backend"
            try {
                ..\.venv\Scripts\python.exe -m pytest
                Assert-NativeSuccess "Backend tests"
                ..\.venv\Scripts\python.exe -m ruff check --no-cache .
                Assert-NativeSuccess "Backend lint"
            }
            finally {
                Pop-Location
            }
            Push-Location "frontend"
            try {
                npm test
                Assert-NativeSuccess "Frontend tests"
                npm run lint
                Assert-NativeSuccess "Frontend lint"
                npm run build
                Assert-NativeSuccess "Frontend build"
            }
            finally {
                Pop-Location
            }
        }
        "migrations" {
            python scripts\migration_smoke.py
            Assert-NativeSuccess "Migration smoke"
        }
        "evaluate" {
            docker compose --profile tools run --rm evaluate
            Assert-NativeSuccess "Executable evaluation"
        }
        "seed" {
            docker compose run --rm `
                -e MIKURAG_DEMO_ADMIN_PASSWORD `
                -e MIKURAG_DEMO_USER_PASSWORD `
                backend python -m app.demo_seed --wait
            Assert-NativeSuccess "Demo seed"
        }
        "smoke" {
            docker compose run --rm backend python -m app.chunking_smoke
            Assert-NativeSuccess "Chunking smoke"
            docker compose run --rm backend python -m app.demo_smoke --retrieval
            Assert-NativeSuccess "Demo smoke"
        }
        "restart-smoke" {
            .\.venv\Scripts\python.exe scripts\restart_smoke.py
            Assert-NativeSuccess "Restart smoke"
        }
        "compose-smoke" {
            .\.venv\Scripts\python.exe scripts\compose_smoke.py
            Assert-NativeSuccess "Compose smoke"
        }
    }
}
finally {
    Pop-Location
}
