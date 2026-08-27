"""Tests for the expanded aggregate report and Markdown summary."""

import json
from pathlib import Path

from app.evaluation.contracts import (
    EvaluationCaseRecord,
    EvaluationRunRecord,
)
from app.evaluation.reporting import (
    build_aggregate_report,
    write_evaluation_artifacts,
)

CHUNKING_CONFIG_HASH = "a" * 64


def _case(
    case_id: str,
    *,
    split: str = "test",
    category: str = "narrow_fact",
) -> EvaluationCaseRecord:
    return EvaluationCaseRecord(
        case_id=case_id,
        category=category,
        query=f"Question {case_id}?",
        expects_supported_answer=True,
        relevant_passage_ids=("a",),
        required_passage_ids=("a",),
        expected_citation_pages=(),
        filters={},
        retrieved_passage_ids=("a",),
        reranked_passage_ids=("a",),
        citation_pages=(),
        sufficient=True,
        retrieval_passed=True,
        answer_faithful=True,
        retrieval_latency_ms=10.0,
        end_to_end_latency_ms=15.0,
        evidence_tokens=100,
        used_summary_path=False,
        retrieval_metrics={},
        evidence=(),
        split=split,
        relevance_grades={"a": 3},
        filter_correct=None,
    )


def _run(*, include_answers: bool = False) -> EvaluationRunRecord:
    cases = tuple(
        [_case(f"case-{i}", split="test") for i in range(3)]
        + [_case("train-case", split="train")]
    )
    return EvaluationRunRecord(
        schema_version=1,
        run_id="report-run",
        status="completed",
        evaluation_set_version="gold_v1",
        started_at="2026-08-27T00:00:00Z",
        completed_at="2026-08-27T00:01:00Z",
        knowledge_base_id=None,
        knowledge_base_name=None,
        knowledge_base_cleaned_up=True,
        include_answers=include_answers,
        configuration={
            "chunking_version": "token_recursive_v1",
            "chunking_config_hash": CHUNKING_CONFIG_HASH,
            "embedding_model_id": "mock-embed",
            "bootstrap_samples": 500,
            "bootstrap_seed": 0,
        },
        documents=(),
        cases=cases,
        chunking_config_hash=CHUNKING_CONFIG_HASH,
        ingestion_duration_ms=250.0,
        embedding_input_count=8,
        total_chunk_count=12,
        storage_estimate_bytes=2048,
    )


def test_aggregate_report_includes_by_split_ingestion_and_confidence_intervals() -> None:
    report = build_aggregate_report(_run())

    assert report["metrics"]["recall_at_10"] == 1
    assert set(report["by_split"]) == {"test", "train"}
    assert report["by_split"]["test"]["recall_at_10"] == 1
    assert report["by_split"]["train"]["recall_at_10"] == 1
    assert report["ingestion"] == {
        "ingestion_duration_ms": 250.0,
        "total_chunk_count": 12,
        "embedding_input_count": 8,
        "storage_estimate_bytes": 2048,
        "chunking_config_hash": CHUNKING_CONFIG_HASH,
    }
    intervals = report["confidence_intervals"]
    assert isinstance(intervals, dict)
    assert "recall_at_1" in intervals
    assert 0.0 <= intervals["recall_at_1"]["ci_low"] <= 1.0
    assert intervals["recall_at_1"]["ci_high"] <= 1.0
    assert report["metrics"]["answer_faithfulness"] is None
    assert report["by_split"]["test"]["answer_faithfulness"] is None
    assert intervals["answer_faithfulness"] == {
        "mean": None,
        "ci_low": None,
        "ci_high": None,
    }


def test_aggregate_report_empty_run_has_no_by_split_metrics() -> None:
    empty = EvaluationRunRecord(
        schema_version=1,
        run_id="empty-run",
        status="failed",
        evaluation_set_version="gold_v1",
        started_at="2026-08-27T00:00:00Z",
        completed_at="2026-08-27T00:00:01Z",
        knowledge_base_id=None,
        knowledge_base_name=None,
        knowledge_base_cleaned_up=True,
        include_answers=False,
        configuration={},
        documents=(),
        cases=(),
        safe_error="boom",
    )
    report = build_aggregate_report(empty)
    assert report["metrics"] is None
    assert report["by_split"] == {}
    assert report["by_category"] == {}
    assert report["confidence_intervals"] is None


def test_markdown_report_covers_quality_ingestion_and_confidence(tmp_path: Path) -> None:
    run = _run(include_answers=True)
    aggregate = build_aggregate_report(run)
    artifacts = write_evaluation_artifacts(tmp_path, run, aggregate)

    markdown = artifacts.report_markdown.read_text(encoding="utf-8")
    assert "## Configuration" in markdown
    assert "## Ingestion and storage" in markdown
    assert "## Metrics by split" in markdown
    assert "## Bootstrap confidence intervals" in markdown
    assert "`token_recursive_v1`" in markdown
    assert CHUNKING_CONFIG_HASH[:16] in markdown
    assert "250" in markdown
    assert "## Aggregate metrics" in markdown

    report = json.loads(artifacts.report_json.read_text(encoding="utf-8"))
    assert report["ingestion"]["total_chunk_count"] == 12
    assert report["confidence_intervals"]["recall_at_1"]["mean"] > 0

    raw = json.loads(artifacts.raw_json.read_text(encoding="utf-8"))
    assert raw["chunking_config_hash"] == CHUNKING_CONFIG_HASH
    assert raw["ingestion_duration_ms"] == 250.0
    assert raw["cases"][0]["split"] == "test"


def test_markdown_report_empty_run_omits_metric_sections(tmp_path: Path) -> None:
    run = EvaluationRunRecord(
        schema_version=1,
        run_id="empty-md",
        status="failed",
        evaluation_set_version="gold_v1",
        started_at="2026-08-27T00:00:00Z",
        completed_at="2026-08-27T00:00:01Z",
        knowledge_base_id=None,
        knowledge_base_name=None,
        knowledge_base_cleaned_up=True,
        include_answers=False,
        configuration={},
        documents=(),
        cases=(),
        safe_error="boom",
    )
    aggregate = build_aggregate_report(run)
    artifacts = write_evaluation_artifacts(tmp_path / "empty", run, aggregate)

    markdown = artifacts.report_markdown.read_text(encoding="utf-8")
    assert "## Failure" in markdown
    assert "boom" in markdown
    assert "## Aggregate metrics" not in markdown
