from pathlib import Path

from app.acceptance import (
    AcceptanceThresholds,
    GateStatus,
    OperationalAcceptanceEvidence,
    evaluate_acceptance,
)
from app.rag.evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluationObservation,
    load_evaluation_observations,
)


def cases() -> list[RetrievalEvaluationCase]:
    return [
        RetrievalEvaluationCase(
            case_id="cross",
            category="cross_page",
            query="Cross-page rule?",
            relevant_passage_ids=("cross-p1-2",),
            required_passage_ids=("cross-p1-2",),
            expected_citation_pages=(1, 2),
            expects_supported_answer=True,
        ),
        RetrievalEvaluationCase(
            case_id="broad",
            category="whole_document_summary",
            query="Summarize everything.",
            relevant_passage_ids=("summary",),
            required_passage_ids=("summary",),
            expected_citation_pages=(3,),
            expects_supported_answer=True,
        ),
        RetrievalEvaluationCase(
            case_id="narrow",
            category="narrow_fact",
            query="Exact limit?",
            relevant_passage_ids=("fact",),
            required_passage_ids=("fact",),
            expected_citation_pages=(4,),
            expects_supported_answer=True,
        ),
    ]


def observations(
    *,
    successful: bool,
    used_summary_path: bool | None,
    latency_ms: float = 100,
    evidence_tokens: int = 500,
) -> list[RetrievalEvaluationObservation]:
    expected = {
        "cross": (("cross-p1-2",), (1, 2)),
        "broad": (("summary",), (3,)),
        "narrow": (("fact",), (4,)),
    }
    return [
        RetrievalEvaluationObservation(
            case_id=case_id,
            retrieved_passage_ids=passages if successful else (),
            reranked_passage_ids=passages if successful else (),
            citation_pages=pages if successful else (),
            answer_faithful=True,
            retrieval_latency_ms=latency_ms,
            end_to_end_latency_ms=latency_ms * 2,
            evidence_tokens=evidence_tokens,
            used_summary_path=(
                used_summary_path if case_id == "broad" else False
            ),
        )
        for case_id, (passages, pages) in expected.items()
    ]


def thresholds() -> AcceptanceThresholds:
    return AcceptanceThresholds(
        minimum_quality_improvement=0.1,
        retrieval_p95_target_ms=1_000,
        evidence_token_budget=6_000,
        worker_memory_limit_bytes=10_000,
        maximum_document_pages=500,
    )


def operational(value: bool | None = True) -> OperationalAcceptanceEvidence:
    return OperationalAcceptanceEvidence(
        source_coverage_verified=value,
        idempotent_reingestion_verified=value,
        rollback_verified=value,
        document_deletion_verified=value,
        document_retry_verified=value,
        citation_compatibility_verified=value,
    )


def benchmark(peak_bytes: int = 5_000) -> dict[str, object]:
    return {
        "schema_version": "capacity_benchmark_v1",
        "ingestion": [
            {
                "page_count": 500,
                "peak_worker_memory_bytes": peak_bytes,
            }
        ],
    }


def test_all_acceptance_gates_pass_with_complete_release_evidence() -> None:
    report = evaluate_acceptance(
        evaluation_set_version="retrieval_v1",
        cases=cases(),
        baseline_observations=observations(
            successful=False,
            used_summary_path=False,
        ),
        candidate_observations=observations(
            successful=True,
            used_summary_path=True,
        ),
        benchmark_report=benchmark(),
        operational_evidence=operational(),
        thresholds=thresholds(),
    )

    assert report.ready_for_default_rollout
    assert len(report.gates) == 10
    assert {gate.status for gate in report.gates} == {GateStatus.PASS}


def test_missing_canary_evidence_is_not_treated_as_failure_or_readiness() -> None:
    report = evaluate_acceptance(
        evaluation_set_version="retrieval_v1",
        cases=cases(),
        baseline_observations=observations(
            successful=False,
            used_summary_path=False,
        ),
        candidate_observations=observations(
            successful=True,
            used_summary_path=None,
        ),
        benchmark_report=benchmark(),
        operational_evidence=operational(None),
        thresholds=thresholds(),
    )
    statuses = {gate.criterion: gate.status for gate in report.gates}

    assert not report.ready_for_default_rollout
    assert statuses["broad_questions_use_summary_path"] == GateStatus.NOT_MEASURED
    assert statuses["no_normalized_source_omission"] == GateStatus.NOT_MEASURED
    assert (
        statuses["idempotent_reingestion_and_rollback"]
        == GateStatus.NOT_MEASURED
    )


def test_capacity_latency_and_evidence_regressions_fail_closed() -> None:
    report = evaluate_acceptance(
        evaluation_set_version="retrieval_v1",
        cases=cases(),
        baseline_observations=observations(
            successful=False,
            used_summary_path=False,
        ),
        candidate_observations=observations(
            successful=True,
            used_summary_path=True,
            latency_ms=2_000,
            evidence_tokens=7_000,
        ),
        benchmark_report=benchmark(peak_bytes=20_000),
        operational_evidence=operational(),
        thresholds=thresholds(),
    )
    statuses = {gate.criterion: gate.status for gate in report.gates}

    assert not report.ready_for_default_rollout
    assert statuses["large_document_worker_memory"] == GateStatus.FAIL
    assert statuses["retrieval_p95_latency"] == GateStatus.FAIL
    assert statuses["average_evidence_tokens"] == GateStatus.FAIL


def test_versioned_observation_and_operational_evidence_loaders(
    tmp_path: Path,
) -> None:
    observation_path = tmp_path / "observations.json"
    observation_path.write_text(
        """
        {
          "evaluation_set_version": "retrieval_v1",
          "observations": [{
            "case_id": "one",
            "retrieved_passage_ids": ["a"],
            "reranked_passage_ids": ["a"],
            "citation_pages": [1],
            "answer_faithful": true,
            "retrieval_latency_ms": 10,
            "end_to_end_latency_ms": 20,
            "evidence_tokens": 30,
            "used_summary_path": true
          }]
        }
        """,
        encoding="utf-8",
    )
    operational_path = (
        Path(__file__).parents[1]
        / "evaluation_sets"
        / "acceptance_operational_v1.example.json"
    )

    version, loaded = load_evaluation_observations(observation_path)
    evidence = OperationalAcceptanceEvidence.load(operational_path)

    assert version == "retrieval_v1"
    assert loaded[0].used_summary_path is True
    assert evidence.rollback_verified is None
