from app.config import Settings
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
    assert record["cost"]["unpriced_token_count"] == 12
    assert "query" not in record
    assert "evidence_text" not in record
