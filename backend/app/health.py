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


async def readiness() -> tuple[bool, dict[str, DependencyHealth]]:
    database, redis = await asyncio.gather(check_database(), check_redis())
    dependencies = {"database": database, "redis": redis}
    return all(item["status"] == "ok" for item in dependencies.values()), dependencies

