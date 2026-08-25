import json
import logging
import uuid

from app.ingestion.contracts import ExtractionWarning
from app.observability import (
    OBSERVATION_PREFIX,
    emit_observation,
    rounded_percentage,
    token_distribution,
    warning_page_count,
)
from app.rag.retrieval import _emit_retrieval_observation
from app.rag.retrieval_types import Candidate, RetrievalMetrics


def test_ingestion_metric_helpers_are_deterministic() -> None:
    warnings = [
        ExtractionWarning("empty_page", "empty", 2),
        ExtractionWarning("empty_page", "duplicate warning", 2),
        ExtractionWarning("empty_page", "unknown page"),
        ExtractionWarning("ocr_fallback_used", "fallback", 3),
    ]

    assert warning_page_count(warnings, "empty_page") == 2
    assert warning_page_count(warnings, "ocr_fallback_used") == 1
    assert token_distribution([10, 20, 30, 40]) == {
        "count": 4,
        "min": 10,
        "p50": 20,
        "p95": 40,
        "max": 40,
        "mean": 25.0,
    }
    assert rounded_percentage(1, 4) == 25.0
    assert rounded_percentage(0, 0) == 0.0


def test_observation_is_one_machine_parseable_json_record(
    caplog,
) -> None:
    logger = logging.getLogger("tests.observability")
    with caplog.at_level(logging.INFO, logger=logger.name):
        emit_observation(
            logger,
            "example",
            document_id=str(uuid.uuid4()),
            count=3,
            duration_ms=12.5,
        )

    message = caplog.records[-1].getMessage()
    assert message.startswith(OBSERVATION_PREFIX)
    payload = json.loads(message.removeprefix(OBSERVATION_PREFIX))
    assert payload["event"] == "example"
    assert payload["count"] == 3


def test_retrieval_observation_records_decisions_without_document_content(
    caplog,
) -> None:
    candidate = Candidate(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_name="private.pdf",
        locator={"page": 1},
        text="sensitive document text must never be logged",
        chunking_version="hierarchical_v1",
        token_count=7,
    )
    metrics = RetrievalMetrics(
        candidate_generation_ms=12.34,
        semantic_query_ms=7.01,
        lexical_query_ms=4.02,
        reranking_ms=5.67,
        semantic_candidate_count=50,
        lexical_candidate_count=50,
        fused_candidate_count=20,
        reranked_candidate_count=20,
        neighbor_expansion_count=1,
        evidence_token_count=7,
        drop_counts={"token_budget": 2},
    )

    with caplog.at_level(logging.INFO, logger="app.rag.retrieval"):
        _emit_retrieval_observation(
            uuid.uuid4(),
            "hierarchical",
            [candidate],
            True,
            metrics,
        )

    message = caplog.records[-1].getMessage()
    payload = json.loads(message.removeprefix(OBSERVATION_PREFIX))
    assert payload["candidate_generation_duration_ms"] == 12.34
    assert payload["semantic_query_duration_ms"] == 7.01
    assert payload["lexical_query_duration_ms"] == 4.02
    assert payload["neighbor_expansion_count"] == 1
    assert payload["evidence_token_count"] == 7
    assert payload["selected_chunking_versions"] == {"hierarchical_v1": 1}
    assert "sensitive document text" not in message
    assert "private.pdf" not in message
