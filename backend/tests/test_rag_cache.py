import json
import uuid

import pytest
from redis.exceptions import RedisError

from app.config import Settings
from app.rag.cache import DerivedDataCache, cache_key, query_embedding
from app.rag.retrieval_types import RetrievalFilters


def _settings(**overrides) -> Settings:
    values = {
        "session_secret": "s" * 32,
        "encryption_master_key": "e" * 32,
        "query_embedding_cache_enabled": True,
        "retrieval_cache_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, bytes] = {}
        self.fail = fail

    async def get(self, key: str):
        if self.fail:
            raise RedisError("offline")
        return self.values.get(key)

    async def set(self, key: str, value: bytes, *, ex: int):
        if self.fail:
            raise RedisError("offline")
        assert ex > 0
        self.values[key] = value

    async def aclose(self) -> None:
        return None


def test_cache_key_is_private_scoped_and_generation_versioned() -> None:
    settings = _settings()
    kb = uuid.uuid4()
    query = "What is SECRET-417?"
    base = dict(
        kind="retrieval-result",
        knowledge_base_id=kb,
        query_text=query,
        filters=RetrievalFilters(tags=("Security",)),
        settings=settings,
        mode="hybrid_rrf",
    )

    first = cache_key(index_generation=1, **base)
    equivalent = cache_key(
        index_generation=1,
        **{**base, "query_text": "  what  IS secret-417?  "},
    )
    next_generation = cache_key(index_generation=2, **base)
    other_kb = cache_key(index_generation=1, **{**base, "knowledge_base_id": uuid.uuid4()})

    assert first == equivalent
    assert first != next_generation
    assert first != other_kb
    assert query not in first
    assert "secret-417" not in first.casefold()


@pytest.mark.asyncio
async def test_cache_enforces_entry_limit_and_fails_open() -> None:
    bounded = DerivedDataCache(FakeRedis(), ttl_seconds=60, max_entry_bytes=20)
    assert await bounded.set_json("large", {"content": "x" * 100}) == "oversize"

    offline = DerivedDataCache(FakeRedis(fail=True), ttl_seconds=60, max_entry_bytes=1000)
    assert await offline.get_json("key") == (None, "error")
    assert await offline.set_json("key", {"ok": True}) == "error"


@pytest.mark.asyncio
async def test_query_embedding_cache_avoids_recomputation() -> None:
    client = FakeRedis()
    cache = DerivedDataCache(client, ttl_seconds=60, max_entry_bytes=20_000)
    settings = _settings()
    calls = 0

    async def compute() -> list[float]:
        nonlocal calls
        calls += 1
        return [0.1] * 768

    kwargs = dict(
        cache=cache,
        knowledge_base_id=uuid.uuid4(),
        index_generation=4,
        query_text="Policy?",
        filters=RetrievalFilters.empty(),
        settings=settings,
        mode="vector",
        compute=compute,
    )
    cold, cold_status = await query_embedding(**kwargs)
    warm, warm_status = await query_embedding(**kwargs)

    assert cold == warm == [0.1] * 768
    assert (cold_status, warm_status) == ("miss", "hit")
    assert calls == 1
    assert all("Policy" not in key for key in client.values)
    assert json.loads(next(iter(client.values.values()))) == [0.1] * 768
