"""Optional Redis caches for rebuildable RAG derived data.

Keys contain only opaque hashes and versioned identity fields. Values never
contain query text or final answers. Every operation is fail-open so Redis is
not part of the correctness path.
"""

import hashlib
import hmac
import json
import logging
import math
import uuid
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings
from app.ingestion.embeddings import EMBEDDING_DIMENSION
from app.rag.retrieval_types import RetrievalFilters, RetrievalMode

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = "v1"


class DerivedDataCache:
    def __init__(self, client: Redis, *, ttl_seconds: int, max_entry_bytes: int) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.max_entry_bytes = max_entry_bytes

    async def get_json(self, key: str) -> tuple[object | None, str]:
        try:
            raw = await self.client.get(key)
        except (RedisError, OSError):
            logger.warning("Redis derived-cache read failed", exc_info=True)
            return None, "error"
        if raw is None:
            return None, "miss"
        if len(raw) > self.max_entry_bytes:
            return None, "oversize"
        try:
            return json.loads(raw), "hit"
        except (TypeError, ValueError, UnicodeDecodeError):
            logger.warning("Redis derived-cache entry was invalid")
            return None, "invalid"

    async def set_json(self, key: str, value: object) -> str:
        raw = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(raw) > self.max_entry_bytes:
            return "oversize"
        try:
            await self.client.set(key, raw, ex=self.ttl_seconds)
        except (RedisError, OSError):
            logger.warning("Redis derived-cache write failed", exc_info=True)
            return "error"
        return "written"

    async def close(self) -> None:
        await self.client.aclose()


_cache: DerivedDataCache | None = None


def get_derived_cache(settings: Settings) -> DerivedDataCache | None:
    global _cache
    if not (settings.query_embedding_cache_enabled or settings.retrieval_cache_enabled):
        return None
    if _cache is None:
        _cache = DerivedDataCache(
            Redis.from_url(settings.redis_url),
            ttl_seconds=settings.rag_cache_ttl_seconds,
            max_entry_bytes=settings.rag_cache_max_entry_bytes,
        )
    return _cache


async def close_derived_cache() -> None:
    global _cache
    if _cache is None:
        return
    await _cache.close()
    _cache = None


def cache_key(
    kind: str,
    *,
    knowledge_base_id: uuid.UUID,
    index_generation: int,
    query_text: str,
    filters: RetrievalFilters,
    settings: Settings,
    mode: RetrievalMode | str,
) -> str:
    """Build a privacy-safe key containing every invalidation dimension."""

    active_mode = mode.value if isinstance(mode, RetrievalMode) else str(mode)
    query_hash = hmac.new(
        settings.session_secret.encode("utf-8"),
        _normalize_query(query_text).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    identity = {
        "schema": CACHE_SCHEMA_VERSION,
        "kind": kind,
        "knowledge_base_id": str(knowledge_base_id),
        "index_generation": index_generation,
        "filters": normalized_filters(filters),
        "query_hash": query_hash,
        "embedding_model": settings.embedding_model_id,
        "chunking_version": settings.chunking_version,
        "retrieval_configuration_version": retrieval_configuration_version(
            settings, active_mode
        ),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"mikurag:{CACHE_SCHEMA_VERSION}:{kind}:{knowledge_base_id}:{index_generation}:{digest}"


def normalized_filters(filters: RetrievalFilters) -> dict[str, object]:
    return {
        "document_ids": sorted(str(item) for item in filters.document_ids),
        "tags": sorted(item.casefold() for item in filters.tags),
        "source_kinds": sorted(item.casefold() for item in filters.source_kinds),
        "languages": sorted(item.casefold() for item in filters.languages),
        "ingested_after": (
            filters.ingested_after.isoformat() if filters.ingested_after is not None else None
        ),
        "ingested_before": (
            filters.ingested_before.isoformat() if filters.ingested_before is not None else None
        ),
    }


def retrieval_configuration_version(settings: Settings, mode: str) -> str:
    configuration = {
        "version": "retrieval-config-v1",
        "mode": mode,
        "semantic_candidates": settings.retrieval_semantic_candidates,
        "lexical_candidates": settings.retrieval_lexical_candidates,
        "rerank_candidates": settings.retrieval_rerank_candidates,
        "evidence_limit": settings.retrieval_evidence_limit,
        "max_chunks_per_document": settings.retrieval_max_chunks_per_document,
        "evidence_token_budget": settings.retrieval_evidence_token_budget,
        "rrf_k": settings.retrieval_rrf_k,
        "rrf_semantic_weight": settings.retrieval_rrf_semantic_weight,
        "rrf_lexical_weight": settings.retrieval_rrf_lexical_weight,
        "min_semantic_similarity": settings.retrieval_min_semantic_similarity,
        "min_lexical_score": settings.retrieval_min_lexical_score,
        "bm25_hybrid_enabled": settings.bm25_hybrid_enabled,
        "reranker_provider": settings.reranker_provider,
        "reranker_model": settings.reranker_model,
    }
    return hashlib.sha256(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def query_embedding(
    cache: DerivedDataCache | None,
    *,
    knowledge_base_id: uuid.UUID,
    index_generation: int,
    query_text: str,
    filters: RetrievalFilters,
    settings: Settings,
    mode: RetrievalMode | str,
    compute: Callable[[], Awaitable[list[float]]],
) -> tuple[list[float], str]:
    if cache is None or not settings.query_embedding_cache_enabled:
        return await compute(), "disabled"
    key = cache_key(
        "query-embedding",
        knowledge_base_id=knowledge_base_id,
        index_generation=index_generation,
        query_text=query_text,
        filters=filters,
        settings=settings,
        mode=mode,
    )
    payload, status = await cache.get_json(key)
    if status == "hit" and _valid_vector(payload):
        return [float(item) for item in payload], "hit"  # type: ignore[arg-type]
    vector = await compute()
    write_status = await cache.set_json(key, vector)
    return vector, "miss" if write_status == "written" else write_status


def _valid_vector(value: object) -> bool:
    return isinstance(value, list) and len(value) == EMBEDDING_DIMENSION and all(
        isinstance(item, (int, float))
        and not isinstance(item, bool)
        and math.isfinite(item)
        for item in value
    )


def _normalize_query(value: str) -> str:
    return " ".join(value.casefold().split())
