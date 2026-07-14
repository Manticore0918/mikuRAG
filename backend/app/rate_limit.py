from fastapi import HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import get_settings


class LoginRateLimiter:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = Redis.from_url(self.settings.redis_url, decode_responses=True)

    async def ensure_allowed(self, key: str) -> None:
        try:
            attempts = await self.client.get(key)
        except RedisError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service temporarily unavailable",
            ) from error
        if attempts is not None and int(attempts) >= self.settings.login_attempt_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Try again later.",
            )

    async def record_failure(self, key: str) -> None:
        try:
            async with self.client.pipeline(transaction=True) as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, self.settings.login_attempt_window_seconds)
                await pipeline.execute()
        except RedisError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service temporarily unavailable",
            ) from error

    async def clear(self, key: str) -> None:
        try:
            await self.client.delete(key)
        except RedisError:
            pass

    async def close(self) -> None:
        await self.client.aclose()


login_rate_limiter = LoginRateLimiter()
