import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = uuid.uuid4().hex[:8]
DATABASES = (
    f"mikurag_migration_clean_{RUN_ID}",
    f"mikurag_migration_previous_{RUN_ID}",
)


def _run(arguments: list[str], *, capture: bool = False, env=None) -> str:
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        check=True,
        env=env,
        text=True,
        capture_output=capture,
    )
    return result.stdout if capture else ""


def _database_url(base_url: str, database: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit(parsed._replace(path=f"/{database}"))


def _alembic(base_url: str, database: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["MIKURAG_DATABASE_URL"] = _database_url(base_url, database)
    _run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "MIKURAG_DATABASE_URL",
            "migrate",
            "alembic",
            *arguments,
        ],
        env=environment,
    )


def main() -> None:
    if not (ROOT / ".env").exists():
        raise SystemExit("Copy .env.example to .env and replace its placeholders first")
    config = json.loads(
        _run(
            ["docker", "compose", "--profile", "tools", "config", "--format", "json"],
            capture=True,
        )
    )
    postgres_environment = config["services"]["postgres"]["environment"]
    migrate_environment = config["services"]["migrate"]["environment"]
    postgres_user = postgres_environment["POSTGRES_USER"]
    main_database = postgres_environment["POSTGRES_DB"]
    base_url = migrate_environment["MIKURAG_DATABASE_URL"]

    # A long-running development stack can leave the one-off migration image
    # behind the working tree. Rebuild it so the smoke test always exercises
    # the migrations currently checked out on disk.
    _run(["docker", "compose", "--profile", "tools", "build", "migrate"])
    _run(["docker", "compose", "up", "-d", "postgres"])
    for _ in range(30):
        ready = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "pg_isready",
                "-U",
                postgres_user,
                "-d",
                main_database,
            ],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if ready.returncode == 0:
            break
        time.sleep(2)
    else:
        raise SystemExit("PostgreSQL did not become ready within 60 seconds")

    try:
        for database in DATABASES:
            _run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "dropdb",
                    "--if-exists",
                    "-U",
                    postgres_user,
                    database,
                ]
            )
            _run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "createdb",
                    "-U",
                    postgres_user,
                    database,
                ]
            )

        _alembic(base_url, DATABASES[0], "upgrade", "head")
        _alembic(base_url, DATABASES[0], "downgrade", "0005")
        _alembic(base_url, DATABASES[0], "upgrade", "head")

        _alembic(base_url, DATABASES[1], "upgrade", "0005")
        _alembic(base_url, DATABASES[1], "upgrade", "head")
        print("Migration smoke passed: clean, 0005 upgrade, 0007 rollback, and re-upgrade")
    finally:
        for database in DATABASES:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "dropdb",
                    "--if-exists",
                    "--force",
                    "-U",
                    postgres_user,
                    database,
                ],
                cwd=ROOT,
                check=False,
            )


if __name__ == "__main__":
    main()
