"""Tests for the pure chunking-profile comparison and acceptance gate."""

import pytest

from app.acceptance import AcceptanceThresholds
from app.evaluation.compare import (
    build_comparison_report,
    render_compare_markdown,
)
from app.evaluation.contracts import (
    EvaluationCaseRecord,
    EvaluationRunRecord,
)

THRESHOLDS = AcceptanceThresholds(
    minimum_quality_improvement=0.0,
    retrieval_p95_target_ms=1500.0,
    evidence_token_budget=2000,
    worker_memory_limit_bytes=1_000_000_000,
    maximum_document_pages=500,
)


def _case(
    case_id: str,
    *,
    reranked: tuple[str, ...] = ("a",),
    required: tuple[str, ...] = ("a",),
    latency_ms: float = 10.0,
    evidence_tokens: int = 100,
    split: str = "test",
    category: str = "narrow_fact",
) -> EvaluationCaseRecord:
    return EvaluationCaseRecord(
        case_id=case_id,
        category=category,
        query=f"Question {case_id}?",
        expects_supported_answer=True,
        relevant_passage_ids=required,
        required_passage_ids=required,
        expected_citation_pages=(),
        filters={},
        retrieved_passage_ids=reranked,
        reranked_passage_ids=reranked,
        citation_pages=(),
        sufficient=True,
        retrieval_passed=set(required) <= set(reranked),
        answer_faithful=True,
        retrieval_latency_ms=latency_ms,
        end_to_end_latency_ms=latency_ms + 5,
        evidence_tokens=evidence_tokens,
        used_summary_path=False,
        retrieval_metrics={},
        evidence=(),
        split=split,
        relevance_grades={passage_id: 3 for passage_id in required},
        filter_correct=None,
    )


def _run(profile: str, cases: tuple[EvaluationCaseRecord, ...]) -> EvaluationRunRecord:
    return EvaluationRunRecord(
        schema_version=1,
        run_id=f"{profile}-run",
        status="completed",
        evaluation_set_version="gold_v1",
        started_at="2026-08-27T00:00:00Z",
        completed_at="2026-08-27T00:01:00Z",
        knowledge_base_id=None,
        knowledge_base_name=None,
        knowledge_base_cleaned_up=True,
        include_answers=False,
        configuration={"chunking_version": profile},
        documents=(),
        cases=cases,
        chunking_config_hash="c" * 64,
        ingestion_duration_ms=100.0,
        embedding_input_count=10,
        total_chunk_count=5,
        storage_estimate_bytes=1024,
    )


def test_comparison_reports_metrics_and_per_split_views() -> None:
    baseline = _run(
        "legacy_char_v1",
        tuple(_case(f"case-{i}", split="test") for i in range(3))
        + (_case("train-case", split="train"),),
    )
    report = build_comparison_report(
        runs={"legacy_char_v1": baseline},
        thresholds=THRESHOLDS,
        baseline_profile="legacy_char_v1",
        headline_split="test",
    )

    row = report["comparison"]["legacy_char_v1"]
    assert row["is_baseline"] is True
    assert row["case_count"] == 3
    assert row["metrics"]["recall_at_10"] == 1
    assert row["by_split"]["test"]["recall_at_10"] == 1
    assert row["by_split"]["train"]["recall_at_10"] == 1
    assert row["total_chunk_count"] == 5
    assert row["storage_estimate_bytes"] == 1024
    assert row["acceptance"] is None
    assert report["headline_split"] == "test"
    assert report["baseline_profile"] == "legacy_char_v1"


def test_candidate_clearing_acceptance_gate_is_ready_for_default() -> None:
    baseline = _run(
        "legacy_char_v1",
        tuple(
            _case(f"case-{i}", reranked=("x",) if i == 0 else ("a",))
            for i in range(4)
        ),
    )
    candidate = _run(
        "hierarchical_v1",
        tuple(_case(f"case-{i}") for i in range(4)),
    )

    report = build_comparison_report(
        runs={"legacy_char_v1": baseline, "hierarchical_v1": candidate},
        thresholds=THRESHOLDS,
        baseline_profile="legacy_char_v1",
        headline_split="test",
    )

    acceptance = report["comparison"]["hierarchical_v1"]["acceptance"]
    assert acceptance["ready_for_default_rollout"] is True
    statuses = {gate["criterion"]: gate["status"] for gate in acceptance["gates"]}
    assert statuses["retrieval_quality_materially_improves"] == "pass"
    assert statuses["retrieval_p95_latency"] == "pass"
    assert statuses["average_evidence_tokens"] == "pass"


def test_candidate_regressing_quality_fails_the_gate() -> None:
    baseline = _run(
        "legacy_char_v1",
        tuple(_case(f"case-{i}") for i in range(4)),
    )
    candidate = _run(
        "hierarchical_v1",
        tuple(
            _case(f"case-{i}", reranked=("x",) if i == 0 else ("a",))
            for i in range(4)
        ),
    )

    report = build_comparison_report(
        runs={"legacy_char_v1": baseline, "hierarchical_v1": candidate},
        thresholds=THRESHOLDS,
        baseline_profile="legacy_char_v1",
        headline_split="test",
    )

    acceptance = report["comparison"]["hierarchical_v1"]["acceptance"]
    assert acceptance["ready_for_default_rollout"] is False
    statuses = {gate["criterion"]: gate["status"] for gate in acceptance["gates"]}
    assert statuses["retrieval_quality_materially_improves"] == "fail"


def test_candidate_exceeding_evidence_budget_fails_the_gate() -> None:
    baseline = _run(
        "legacy_char_v1",
        tuple(_case(f"case-{i}") for i in range(4)),
    )
    candidate = _run(
        "hierarchical_v1",
        tuple(
            _case(f"case-{i}", evidence_tokens=3000) for i in range(4)
        ),
    )

    report = build_comparison_report(
        runs={"legacy_char_v1": baseline, "hierarchical_v1": candidate},
        thresholds=THRESHOLDS,
        baseline_profile="legacy_char_v1",
        headline_split="test",
    )

    acceptance = report["comparison"]["hierarchical_v1"]["acceptance"]
    assert acceptance["ready_for_default_rollout"] is False
    statuses = {gate["criterion"]: gate["status"] for gate in acceptance["gates"]}
    assert statuses["average_evidence_tokens"] == "fail"


def test_winners_by_category_pick_the_better_profile() -> None:
    baseline = _run(
        "legacy_char_v1",
        tuple(
            _case(
                f"case-{i}",
                category="semantic_paraphrase",
                reranked=("x",) if i == 0 else ("a",),
            )
            for i in range(4)
        ),
    )
    candidate = _run(
        "token_recursive_v1",
        tuple(
            _case(f"case-{i}", category="semantic_paraphrase")
            for i in range(4)
        ),
    )

    report = build_comparison_report(
        runs={
            "legacy_char_v1": baseline,
            "token_recursive_v1": candidate,
        },
        thresholds=THRESHOLDS,
        baseline_profile="legacy_char_v1",
        headline_split="test",
    )

    winner = report["winners_by_category"]["semantic_paraphrase"]
    assert winner["profile"] == "token_recursive_v1"
    assert winner["ndcg_at_10"] == 1


def test_comparison_rejects_failed_runs() -> None:
    failed = EvaluationRunRecord(
        schema_version=1,
        run_id="failed-run",
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
        cases=(_case("case-0"),),
        safe_error="boom",
    )
    with pytest.raises(ValueError, match="failed"):
        build_comparison_report(
            runs={"legacy_char_v1": failed},
            thresholds=THRESHOLDS,
            baseline_profile="legacy_char_v1",
        )


def test_comparison_rejects_runs_over_different_evaluation_sets() -> None:
    other = EvaluationRunRecord(
        schema_version=1,
        run_id="other-run",
        status="completed",
        evaluation_set_version="executable_v1",
        started_at="2026-08-27T00:00:00Z",
        completed_at="2026-08-27T00:01:00Z",
        knowledge_base_id=None,
        knowledge_base_name=None,
        knowledge_base_cleaned_up=True,
        include_answers=False,
        configuration={},
        documents=(),
        cases=(_case("case-0"),),
    )
    with pytest.raises(ValueError, match="same evaluation set"):
        build_comparison_report(
            runs={"legacy_char_v1": _run("legacy_char_v1", (_case("case-0"),)), "other": other},
            thresholds=THRESHOLDS,
            baseline_profile="legacy_char_v1",
        )


def test_render_compare_markdown_lists_profiles_and_acceptance() -> None:
    baseline = _run(
        "legacy_char_v1",
        tuple(_case(f"case-{i}") for i in range(3)),
    )
    candidate = _run(
        "hierarchical_v1",
        tuple(_case(f"case-{i}", latency_ms=5) for i in range(3)),
    )
    report = build_comparison_report(
        runs={"legacy_char_v1": baseline, "hierarchical_v1": candidate},
        thresholds=THRESHOLDS,
        baseline_profile="legacy_char_v1",
        headline_split="test",
    )

    markdown = render_compare_markdown(report)

    assert "# Chunking comparison" in markdown
    assert "`legacy_char_v1`" in markdown
    assert "`hierarchical_v1`" in markdown
    assert "Ready for default rollout" in markdown
    assert "narrow_fact" in markdown
