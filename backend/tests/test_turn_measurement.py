from app.config import Settings
from app.rag import turn_measurement as turn_measurement_module
from app.rag.retrieval_types import RetrievalMetrics
from app.rag.turn_measurement import build_turn_measurement


def test_turn_measurement_is_redacted_and_records_local_api_spend() -> None:
    settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        generation_base_url="http://localhost:11434/v1",
    )
    retrieval = RetrievalMetrics(
        rewrite_latency_ms=3,
        query_embedding_ms=4,
        semantic_query_ms=5,
        lexical_query_ms=6,
        fusion_ms=1,
        reranking_ms=7,
        expansion_ms=2,
        retrieval_duration_ms=21,
        evidence_token_count=80,
        query_embedding_cache_status="hit",
        retrieval_cache_status="miss",
    )

    record = build_turn_measurement(
        settings=settings,
        retrieval=retrieval,
        usage={"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125},
        embedding_tokens=12,
        generation_ms=30,
        validation_ms=2,
        persistence_ms=4,
        total_ms=61,
    )

    assert record["latency_ms"]["total"] == 61
    assert record["cache"] == {"query_embedding": "hit", "retrieval": "miss"}
    assert record["cost"]["local_generation_api_spend"] == 0
    assert record["cost"]["local_compute_cost_included"] is False
    assert record["tokens"]["query_embedding"] == 12
    assert record["tokens"]["query_embedding_billable"] == 0
    assert record["cost"]["billable_embedding_tokens"] == 0
    assert record["cost"]["unpriced_token_count"] == 0
    assert record["cost"]["estimate_complete"] is True
    assert "query" not in record
    assert "evidence_text" not in record


def test_external_embedding_price_is_applied_only_on_cache_miss(monkeypatch) -> None:
    monkeypatch.setattr(
        turn_measurement_module,
        "_load_pricing",
        lambda: {
            "schema_version": 1,
            "version": "test-prices",
            "currency": "USD",
            "models": [
                {
                    "kind": "embedding",
                    "provider": "external",
                    "model": "embed-v1",
                    "input_usd_per_million_tokens": 2.0,
                },
                {
                    "kind": "generation",
                    "provider": "local",
                    "model": "*",
                    "input_usd_per_million_tokens": 0.0,
                    "output_usd_per_million_tokens": 0.0,
                },
            ],
        },
    )
    settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        embedding_model_id="embed-v1",
        generation_base_url="http://localhost:11434/v1",
    )
    retrieval = RetrievalMetrics(query_embedding_cache_status="miss")

    record = build_turn_measurement(
        settings=settings,
        retrieval=retrieval,
        usage={},
        embedding_tokens=12,
        generation_ms=0,
        validation_ms=0,
        persistence_ms=0,
        total_ms=1,
    )

    assert record["tokens"]["query_embedding_billable"] == 12
    assert record["cost"]["embedding_api_spend"] == 0.000024
    assert record["cost"]["estimated_api_spend"] == 0.000024
    assert record["cost"]["unpriced_token_count"] == 0
    assert record["cost"]["estimate_complete"] is True
