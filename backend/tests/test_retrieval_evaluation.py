from pathlib import Path

import pytest

from app.rag.evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluationMetrics,
    RetrievalEvaluationObservation,
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

    assert metrics.recall_at_10 == pytest.approx(0.75)
    assert metrics.recall_after_reranking == pytest.approx(0.75)
    assert metrics.mean_reciprocal_rank == pytest.approx(0.75)
    assert metrics.citation_page_accuracy == pytest.approx(0.75)
    assert metrics.answer_faithfulness == 1
    assert metrics.all_required_passages_rate == pytest.approx(0.5)
    assert metrics.mean_retrieval_latency_ms == 15
    assert metrics.mean_end_to_end_latency_ms == 40
    assert metrics.mean_evidence_tokens == 50


def test_evaluation_comparison_reports_candidate_deltas() -> None:
    baseline = RetrievalEvaluationMetrics(*([1.0] * 9))
    candidate = RetrievalEvaluationMetrics(*([1.5] * 9))

    delta = compare_evaluations(baseline, candidate)

    assert set(delta) == set(RetrievalEvaluationMetrics.__dataclass_fields__)
    assert set(delta.values()) == {0.5}
