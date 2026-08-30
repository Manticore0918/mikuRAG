#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COMMAND=${1:-checks}
cd "$PROJECT_ROOT"

case "$COMMAND" in
  setup)
    test -f .env || {
      echo "Copy .env.example to .env and replace every placeholder first." >&2
      exit 1
    }
    test -x .venv/bin/python || python3 -m venv .venv
    .venv/bin/python -m pip install -e './backend[dev]'
    npm --prefix frontend ci
    docker compose --profile tools run --build --rm migrate
    docker compose up --build -d
    ;;
  checks)
    (cd backend && ../.venv/bin/python -m pytest)
    (cd backend && ../.venv/bin/python -m ruff check --no-cache .)
    (cd frontend && npm test)
    (cd frontend && npm run lint)
    (cd frontend && npm run build)
    ;;
  migrations)
    python3 scripts/migration_smoke.py
    ;;
  evaluate)
    docker compose --profile tools run --rm evaluate
    ;;
  seed)
    docker compose run --rm \
      -e MIKURAG_DEMO_ADMIN_PASSWORD \
      -e MIKURAG_DEMO_USER_PASSWORD \
      backend python -m app.demo_seed --wait
    ;;
  smoke)
    docker compose run --rm backend python -m app.chunking_smoke
    docker compose run --rm backend python -m app.demo_smoke --retrieval
    ;;
  restart-smoke)
    .venv/bin/python scripts/restart_smoke.py
    ;;
  compose-smoke)
    .venv/bin/python scripts/compose_smoke.py
    ;;
  *)
    echo "Usage: $0 {setup|checks|migrations|seed|smoke|restart-smoke|compose-smoke|evaluate}" >&2
    exit 2
    ;;
esac
