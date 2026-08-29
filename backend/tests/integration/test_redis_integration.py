"""Redis integration: derived-cache roundtrip, key privacy, and fail-open."""

import uuid

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.rag.cache import CACHE_SCHEMA_VERSION, DerivedDataCache, cache_key
from app.rag.retrieval_types import RetrievalFilters

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def redis_client(settings):
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
    except Exception:
        pytest.skip("Redis is not reachable; integration tests need a live Redis")
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def derived_cache(redis_client, settings):
    cache = DerivedDataCache(
        redis_client,
        ttl_seconds=settings.rag_cache_ttl_seconds,
        max_entry_bytes=settings.rag_cache_max_entry_bytes,
    )
    yield cache
    await redis_client.flushdb()


async def test_cache_roundtrip_and_key_privacy(derived_cache, settings) -> None:
    secret_query = "confidential query MH-4021 must not appear in Redis keys"
    key = cache_key(
        "query-embedding",
        knowledge_base_id=uuid.uuid4(),
        index_generation=3,
        query_text=secret_query,
        filters=RetrievalFilters(),
        settings=settings,
        mode="hybrid_rrf",
    )
    write_status = await derived_cache.set_json(key, [0.1, 0.2, 0.3])
    assert write_status == "written"

    payload, read_status = await derived_cache.get_json(key)
    assert read_status == "hit"
    assert payload == [0.1, 0.2, 0.3]

    assert secret_query not in key
    assert "MH-4021" not in key
    assert key.startswith(f"mikurag:{CACHE_SCHEMA_VERSION}:query-embedding:")


async def test_cache_miss_and_ttl_are_enforced(derived_cache, settings) -> None:
    key = cache_key(
        "retrieval",
        knowledge_base_id=uuid.uuid4(),
        index_generation=1,
        query_text="anything",
        filters=RetrievalFilters(),
        settings=settings,
        mode="vector",
    )
    payload, status = await derived_cache.get_json(key)
    assert payload is None and status == "miss"
    ttl = await derived_cache.client.ttl(key)
    assert ttl == -2  # the key does not exist yet


async def test_cache_fail_open_when_redis_is_unreachable(settings) -> None:
    broken = DerivedDataCache(
        Redis.from_url("redis://127.0.0.1:1/0"),
        ttl_seconds=settings.rag_cache_ttl_seconds,
        max_entry_bytes=settings.rag_cache_max_entry_bytes,
    )
    try:
        payload, status = await broken.get_json("mikurag:v1:anything")
        assert payload is None and status == "error"
        assert await broken.set_json("mikurag:v1:anything", {"a": 1}) == "error"
    finally:
        await broken.client.aclose()
