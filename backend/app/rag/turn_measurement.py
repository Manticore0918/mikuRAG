"""Redacted per-turn latency, token, cache, and API-cost records."""

import json
from pathlib import Path
from urllib.parse import urlparse

from app.config import Settings
from app.rag.retrieval_types import RetrievalMetrics

MEASUREMENT_SCHEMA_VERSION = 1
_PRICING_PATH = Path(__file__).with_name("pricing_v1.json")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "ollama"}


def build_turn_measurement(
    *,
    settings: Settings,
    retrieval: RetrievalMetrics,
    usage: dict[str, int],
    embedding_tokens: int,
    generation_ms: float,
    validation_ms: float,
    persistence_ms: float,
    total_ms: float,
) -> dict[str, object]:
    pricing = _load_pricing()
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_generation_tokens = int(
        usage.get("total_tokens") or prompt_tokens + completion_tokens
    )
    cost = _estimate_cost(
        pricing,
        settings=settings,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_generation_tokens=total_generation_tokens,
        embedding_tokens=embedding_tokens,
    )
    return {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "pricing_version": pricing["version"],
        "latency_ms": {
            "rewrite": round(retrieval.rewrite_latency_ms, 2),
            "query_embedding": round(retrieval.query_embedding_ms, 2),
            "vector": round(retrieval.semantic_query_ms, 2),
            "bm25_or_fts": round(retrieval.lexical_query_ms, 2),
            "fusion": round(retrieval.fusion_ms, 2),
            "rerank": round(retrieval.reranking_ms, 2),
            "expansion": round(retrieval.expansion_ms, 2),
            "generation": round(generation_ms, 2),
            "validation": round(validation_ms, 2),
            "persistence": round(persistence_ms, 2),
            "retrieval": round(retrieval.retrieval_duration_ms, 2),
            "total": round(total_ms, 2),
        },
        "tokens": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "generation_total": total_generation_tokens,
            "query_embedding": embedding_tokens,
            "evidence": retrieval.evidence_token_count,
        },
        "candidate_counts": {
            "vector": retrieval.semantic_candidate_count,
            "lexical": retrieval.lexical_candidate_count,
            "fused": retrieval.fused_candidate_count,
            "reranked": retrieval.reranked_candidate_count,
        },
        "cache": {
            "query_embedding": retrieval.query_embedding_cache_status,
            "retrieval": retrieval.retrieval_cache_status,
        },
        "models": {
            "embedding": settings.embedding_model_id,
            "generation": settings.generation_model_id,
            "reranker_provider": retrieval.reranker_provider,
            "reranker_model": retrieval.reranker_model,
            "reranker_version": retrieval.reranker_version,
        },
        "cost": cost,
    }


def _load_pricing() -> dict[str, object]:
    payload = json.loads(_PRICING_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("version"), str):
        raise RuntimeError("The bundled pricing table is invalid")
    return payload


def _estimate_cost(
    pricing: dict[str, object],
    *,
    settings: Settings,
    prompt_tokens: int,
    completion_tokens: int,
    total_generation_tokens: int,
    embedding_tokens: int,
) -> dict[str, object]:
    generation_provider = (
        "local"
        if urlparse(settings.generation_base_url).hostname in _LOCAL_HOSTS
        else "external"
    )
    generation_entry = _pricing_entry(
        pricing,
        kind="generation",
        provider=generation_provider,
        model=settings.generation_model_id,
    )
    known_spend = 0.0
    unpriced_tokens = embedding_tokens
    unclassified_generation_tokens = max(
        0,
        total_generation_tokens - prompt_tokens - completion_tokens,
    )
    if generation_entry is None:
        unpriced_tokens += total_generation_tokens
    else:
        known_spend += prompt_tokens * float(
            generation_entry["input_usd_per_million_tokens"]
        ) / 1_000_000
        known_spend += completion_tokens * float(
            generation_entry["output_usd_per_million_tokens"]
        ) / 1_000_000
        unpriced_tokens += unclassified_generation_tokens
    return {
        "currency": pricing.get("currency", "USD"),
        "estimated_api_spend": round(known_spend, 8),
        "estimate_complete": unpriced_tokens == 0,
        "unpriced_token_count": unpriced_tokens,
        "local_generation_api_spend": (
            0.0 if generation_provider == "local" else None
        ),
        "local_compute_cost_included": False,
    }


def _pricing_entry(
    pricing: dict[str, object],
    *,
    kind: str,
    provider: str,
    model: str,
) -> dict[str, object] | None:
    entries = pricing.get("models")
    if not isinstance(entries, list):
        return None
    for item in entries:
        if (
            isinstance(item, dict)
            and item.get("kind") == kind
            and item.get("provider") == provider
            and item.get("model") in {model, "*"}
        ):
            return item
    return None
