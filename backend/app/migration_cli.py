"""Run schema migrations, then reconcile image-dependent database features."""

import asyncio
import json
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.database import close_database
from app.database_features import reconcile_optional_database_features

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _upgrade_schema() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(config, "head")


async def _reconcile_and_close() -> dict[str, str]:
    try:
        return dict(await reconcile_optional_database_features())
    finally:
        await close_database()


def main() -> None:
    _upgrade_schema()
    status = asyncio.run(_reconcile_and_close())
    print(json.dumps({"bm25": status}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
