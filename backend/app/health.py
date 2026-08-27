import asyncio
from typing import TypedDict

from redis.asyncio import Redis
from sqlalchemy import text

from app.config import get_settings
from app.database import engine


class DependencyHealth(TypedDict):
    status: str
    detail: str | None


async def check_database() -> DependencyHealth:
    try:
        async with asyncio.timeout(2):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        return {"status": "ok", "detail": None}
    except Exception:
        return {"status": "error", "detail": "database unavailable"}


async def check_redis() -> DependencyHealth:
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        async with asyncio.timeout(2):
            await client.ping()
        return {"status": "ok", "detail": None}
    except Exception:
        return {"status": "error", "detail": "redis unavailable"}
    finally:
        await client.aclose()


async def check_bm25() -> DependencyHealth:
    """Report whether the pg_search BM25 index is present and usable.

    This is informational, not gating: the FTS baseline is always available, so
    a missing extension or index must not flip readiness. Status is "ok" when
    the fast BM25 path can run and "unavailable" when the FTS fallback is active.
    """
    try:
        async with asyncio.timeout(2):
            async with engine.connect() as connection:
                row = await connection.execute(
                    text(
                        "SELECT "
                        "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_search')"
                        " AND to_regclass('chunks_search_bm25') IS NOT NULL"
                    )
                )
                available = bool(row.scalar())
        if available:
            return {"status": "ok", "detail": None}
        return {
            "status": "unavailable",
            "detail": "pg_search BM25 index missing; using the FTS fallback path",
        }
    except Exception:
        return {"status": "unavailable", "detail": "bm25 availability check failed"}


async def readiness() -> tuple[bool, dict[str, DependencyHealth]]:
    database, redis, bm25 = await asyncio.gather(check_database(), check_redis(), check_bm25())
    # The BM25 leg is an optional accelerator; readiness is gated on the core
    # dependencies only, with bm25 reported alongside for observability.
    core = {"database": database, "redis": redis}
    dependencies = {**core, "bm25": bm25}
    return all(item["status"] == "ok" for item in core.values()), dependencies
