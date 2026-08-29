from pathlib import Path

import pytest

from app.rag.evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluationMetrics,
    RetrievalEvaluationObservation,
    bootstrap_confidence_intervals,
    compare_evaluations,
    evaluate_retrieval,
    load_evaluation_set,
)


def test_versioned_evaluation_set_covers_required_query_categories() -> None:
    path = Path(__file__).parents[1] / "evaluation_sets" / "retrieval_v1.json"

    version, cases = load_evaluation_set(path)

    assert version == "retrieval_v1"
    assert {case.category for case in cases} == {
        "narrow_fact",
        "lexical_exact",
        "semantic_paraphrase",
        "cross_page",
        "multi_section",
        "multi_document",
        "whole_document_summary",
        "unsupported",
    }
    assert any(not case.expects_supported_answer for case in cases)


def test_retrieval_evaluator_measures_quality_latency_and_evidence_usage() -> None:
    cases = [
        RetrievalEvaluationCase(
            case_id="one",
            category="narrow_fact",
            query="First?",
            relevant_passage_ids=("a", "b"),
            required_passage_ids=("a", "b"),
            expected_citation_pages=(1, 2),
            expects_supported_answer=True,
            relevance_grades={"a": 3, "b": 3},
        ),
        RetrievalEvaluationCase(
            case_id="two",
            category="unsupported",
            query="Unknown?",
            relevant_passage_ids=(),
            required_passage_ids=(),
            expected_citation_pages=(),
            expects_supported_answer=False,
        ),
    ]
    observations = [
        RetrievalEvaluationObservation(
            case_id="one",
            retrieved_passage_ids=("a", "noise"),
            reranked_passage_ids=("noise", "a"),
            citation_pages=(1,),
            answer_faithful=True,
            retrieval_latency_ms=10,
            end_to_end_latency_ms=30,
            evidence_tokens=100,
        ),
        RetrievalEvaluationObservation(
            case_id="two",
            retrieved_passage_ids=(),
            reranked_passage_ids=(),
            citation_pages=(),
            answer_faithful=True,
            retrieval_latency_ms=20,
            end_to_end_latency_ms=50,
            evidence_tokens=0,
        ),
    ]

    metrics = evaluate_retrieval(cases, observations)

    assert metrics.recall_at_1 == pytest.approx(0.5)
    assert metrics.recall_at_5 == pytest.approx(0.75)
    assert metrics.recall_at_10 == pytest.approx(0.75)
    assert metrics.recall_after_reranking == pytest.approx(0.75)
    assert metrics.mean_reciprocal_rank == pytest.approx(0.75)
    assert metrics.ndcg_at_10 == pytest.approx(0.693_4, abs=1e-4)
    assert metrics.citation_page_accuracy == pytest.approx(0.75)
    assert metrics.citation_precision == 1
    assert metrics.answer_faithfulness == 1
    assert metrics.all_required_passages_rate == pytest.approx(0.5)
    assert metrics.filter_correctness is None
    assert metrics.mean_retrieval_latency_ms == 15
    assert metrics.retrieval_latency_p95_ms == pytest.approx(19.5)
    assert metrics.retrieval_latency_p50_ms == pytest.approx(15.0)
    assert metrics.retrieval_latency_p99_ms == pytest.approx(19.9)
    assert metrics.mean_end_to_end_latency_ms == 40
    assert metrics.end_to_end_latency_p95_ms == pytest.approx(49.0)
    assert metrics.end_to_end_latency_p50_ms == pytest.approx(40.0)
    assert metrics.end_to_end_latency_p99_ms == pytest.approx(49.8)
    assert metrics.mean_evidence_tokens == 50


def test_evaluation_comparison_reports_candidate_deltas() -> None:
    field_count = len(RetrievalEvaluationMetrics.__dataclass_fields__)
    baseline = RetrievalEvaluationMetrics(*([1.0] * field_count))
    candidate = RetrievalEvaluationMetrics(*([1.5] * field_count))

    delta = compare_evaluations(baseline, candidate)

    assert set(delta) == set(RetrievalEvaluationMetrics.__dataclass_fields__)
    assert set(delta.values()) == {0.5}


def test_bootstrap_confidence_intervals_are_reproducible_and_bounded() -> None:
    cases = [
        RetrievalEvaluationCase(
            case_id=f"case-{index}",
            category="narrow_fact",
            query=f"Question {index}?",
            relevant_passage_ids=(f"p{index}",),
            required_passage_ids=(f"p{index}",),
            expected_citation_pages=(),
            expects_supported_answer=True,
            relevance_grades={f"p{index}": 3},
        )
        for index in range(6)
    ]
    observations = [
        RetrievalEvaluationObservation(
            case_id=f"case-{index}",
            retrieved_passage_ids=(f"p{index}",) if index % 2 == 0 else ("wrong",),
            reranked_passage_ids=(f"p{index}",) if index % 2 == 0 else ("wrong",),
            citation_pages=(),
            answer_faithful=True,
            retrieval_latency_ms=float(index * 5),
            end_to_end_latency_ms=float(index * 10),
            evidence_tokens=20,
        )
        for index in range(6)
    ]

    first = bootstrap_confidence_intervals(cases, observations, samples=500, seed=7)
    second = bootstrap_confidence_intervals(cases, observations, samples=500, seed=7)

    assert first == second
    assert 0.0 <= first["recall_at_1"]["ci_low"] <= first["recall_at_1"]["ci_high"] <= 1.0
    assert 0.0 <= first["recall_at_5"]["ci_low"] <= first["recall_at_5"]["ci_high"] <= 1.0
    assert first["recall_at_1"]["mean"] == pytest.approx(0.5, abs=0.15)
    assert first["retrieval_latency_p95_ms"]["ci_low"] >= 0
