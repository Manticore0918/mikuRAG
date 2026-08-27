import json
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from statistics import fmean

from app.config import Settings
from app.rag.evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluationMetrics,
    RetrievalEvaluationObservation,
    evaluate_retrieval,
)


class GateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_MEASURED = "not_measured"


@dataclass(frozen=True)
class AcceptanceThresholds:
    minimum_quality_improvement: float
    retrieval_p95_target_ms: float
    evidence_token_budget: int
    worker_memory_limit_bytes: int
    maximum_document_pages: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "AcceptanceThresholds":
        return cls(
            minimum_quality_improvement=settings.acceptance_min_quality_improvement,
            retrieval_p95_target_ms=settings.acceptance_retrieval_p95_target_ms,
            evidence_token_budget=settings.retrieval_evidence_token_budget,
            worker_memory_limit_bytes=settings.worker_memory_limit_bytes,
            maximum_document_pages=settings.max_document_pages,
        )


@dataclass(frozen=True)
class OperationalAcceptanceEvidence:
    source_coverage_verified: bool | None = None
    idempotent_reingestion_verified: bool | None = None
    rollback_verified: bool | None = None
    document_deletion_verified: bool | None = None
    document_retry_verified: bool | None = None
    citation_compatibility_verified: bool | None = None

    @classmethod
    def load(cls, path: Path) -> "OperationalAcceptanceEvidence":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "acceptance_operational_v1":
            raise ValueError("Unsupported operational acceptance evidence schema")
        evidence = payload.get("evidence")
        if not isinstance(evidence, dict):
            raise ValueError("Operational acceptance evidence requires an evidence object")
        values = {}
        for field_name in cls.__dataclass_fields__:
            value = evidence.get(field_name)
            if value is not None and type(value) is not bool:
                raise ValueError(f"{field_name} must be true, false, or null")
            values[field_name] = value
        return cls(**values)


@dataclass(frozen=True)
class AcceptanceGate:
    criterion: str
    status: GateStatus
    actual: object
    threshold: object
    evidence: str


@dataclass(frozen=True)
class AcceptanceReport:
    schema_version: str
    evaluation_set_version: str
    ready_for_default_rollout: bool
    baseline_metrics: dict[str, float]
    candidate_metrics: dict[str, float]
    gates: list[AcceptanceGate]


def evaluate_acceptance(
    *,
    evaluation_set_version: str,
    cases: list[RetrievalEvaluationCase],
    baseline_observations: list[RetrievalEvaluationObservation],
    candidate_observations: list[RetrievalEvaluationObservation],
    benchmark_report: dict[str, object],
    operational_evidence: OperationalAcceptanceEvidence,
    thresholds: AcceptanceThresholds,
) -> AcceptanceReport:
    baseline_metrics = evaluate_retrieval(cases, baseline_observations)
    candidate_metrics = evaluate_retrieval(cases, candidate_observations)
    candidate_by_id = {
        observation.case_id: observation for observation in candidate_observations
    }

    gates = [
        _cross_page_gate(cases, candidate_by_id),
        _citation_ranges_gate(cases, candidate_by_id),
        _boolean_gate(
            "no_normalized_source_omission",
            operational_evidence.source_coverage_verified,
            "Property and hierarchy validation suite",
        ),
        _worker_memory_gate(benchmark_report, thresholds),
        _quality_gate(baseline_metrics, candidate_metrics, thresholds),
        _summary_routing_gate(cases, candidate_by_id),
        _latency_gate(candidate_observations, thresholds),
        _evidence_budget_gate(candidate_observations, thresholds),
        _combined_boolean_gate(
            "idempotent_reingestion_and_rollback",
            {
                "idempotent_reingestion": (
                    operational_evidence.idempotent_reingestion_verified
                ),
                "rollback": operational_evidence.rollback_verified,
            },
            "Re-index integration and rollback canary",
        ),
        _combined_boolean_gate(
            "existing_document_and_citation_flows",
            {
                "document_deletion": operational_evidence.document_deletion_verified,
                "document_retry": operational_evidence.document_retry_verified,
                "citation_compatibility": (
                    operational_evidence.citation_compatibility_verified
                ),
            },
            "Compatibility regression suite",
        ),
    ]
    return AcceptanceReport(
        schema_version="chunking_acceptance_v1",
        evaluation_set_version=evaluation_set_version,
        ready_for_default_rollout=all(
            gate.status == GateStatus.PASS for gate in gates
        ),
        baseline_metrics=_metrics_dict(baseline_metrics),
        candidate_metrics=_metrics_dict(candidate_metrics),
        gates=gates,
    )


def _cross_page_gate(
    cases: list[RetrievalEvaluationCase],
    observations: dict[str, RetrievalEvaluationObservation],
) -> AcceptanceGate:
    cross_page_cases = [case for case in cases if case.category == "cross_page"]
    if not cross_page_cases:
        return _not_measured(
            "cross_page_context_complete",
            "Evaluation set has no cross-page cases",
        )
    coverage = {
        case.case_id: set(case.required_passage_ids)
        <= set(observations[case.case_id].reranked_passage_ids)
        for case in cross_page_cases
    }
    return _gate(
        "cross_page_context_complete",
        all(coverage.values()),
        actual=coverage,
        threshold="all cross-page cases include every required passage",
        evidence="Candidate retrieval observations",
    )


def _citation_ranges_gate(
    cases: list[RetrievalEvaluationCase],
    observations: dict[str, RetrievalEvaluationObservation],
) -> AcceptanceGate:
    citable_cases = [case for case in cases if case.expected_citation_pages]
    if not citable_cases:
        return _not_measured(
            "citation_page_ranges_correct",
            "Evaluation set has no expected citation pages",
        )
    accuracy = {
        case.case_id: set(case.expected_citation_pages)
        == set(observations[case.case_id].citation_pages)
        for case in citable_cases
    }
    return _gate(
        "citation_page_ranges_correct",
        all(accuracy.values()),
        actual=accuracy,
        threshold="exact expected citation-page set for every citable case",
        evidence="Candidate citation observations",
    )


def _worker_memory_gate(
    benchmark_report: dict[str, object],
    thresholds: AcceptanceThresholds,
) -> AcceptanceGate:
    ingestion = benchmark_report.get("ingestion")
    if not isinstance(ingestion, list) or not ingestion:
        return _not_measured(
            "large_document_worker_memory",
            "Benchmark report has no ingestion measurements",
        )
    valid_rows = [
        row
        for row in ingestion
        if isinstance(row, dict)
        and type(row.get("page_count")) is int
        and type(row.get("peak_worker_memory_bytes")) is int
    ]
    if not valid_rows:
        return _not_measured(
            "large_document_worker_memory",
            "Benchmark report has no valid memory measurements",
        )
    largest = max(valid_rows, key=lambda row: int(row["page_count"]))
    page_count = int(largest["page_count"])
    peak_bytes = int(largest["peak_worker_memory_bytes"])
    passed = (
        page_count >= thresholds.maximum_document_pages
        and peak_bytes <= thresholds.worker_memory_limit_bytes
    )
    return _gate(
        "large_document_worker_memory",
        passed,
        actual={
            "page_count": page_count,
            "peak_worker_memory_bytes": peak_bytes,
        },
        threshold={
            "minimum_page_count": thresholds.maximum_document_pages,
            "maximum_peak_worker_memory_bytes": thresholds.worker_memory_limit_bytes,
        },
        evidence="capacity_benchmark_v1 ingestion matrix",
    )


def _quality_gate(
    baseline: RetrievalEvaluationMetrics,
    candidate: RetrievalEvaluationMetrics,
    thresholds: AcceptanceThresholds,
) -> AcceptanceGate:
    names = (
        "recall_at_10",
        "recall_after_reranking",
        "mean_reciprocal_rank",
        "all_required_passages_rate",
    )
    baseline_values = [getattr(baseline, name) for name in names]
    candidate_values = [getattr(candidate, name) for name in names]
    baseline_composite = fmean(baseline_values)
    candidate_composite = fmean(candidate_values)
    improvement = candidate_composite - baseline_composite
    no_component_regression = all(
        candidate_value >= baseline_value
        for baseline_value, candidate_value in zip(
            baseline_values,
            candidate_values,
            strict=True,
        )
    )
    return _gate(
        "retrieval_quality_materially_improves",
        improvement >= thresholds.minimum_quality_improvement
        and no_component_regression,
        actual={
            "baseline_composite": round(baseline_composite, 6),
            "candidate_composite": round(candidate_composite, 6),
            "improvement": round(improvement, 6),
            "no_component_regression": no_component_regression,
        },
        threshold={
            "minimum_improvement": thresholds.minimum_quality_improvement,
            "component_regressions_allowed": False,
        },
        evidence="Legacy and hierarchical retrieval evaluations",
    )


def _summary_routing_gate(
    cases: list[RetrievalEvaluationCase],
    observations: dict[str, RetrievalEvaluationObservation],
) -> AcceptanceGate:
    broad_cases = [
        case for case in cases if case.category == "whole_document_summary"
    ]
    if not broad_cases:
        return _not_measured(
            "broad_questions_use_summary_path",
            "Evaluation set has no whole-document summary cases",
        )
    routes = {
        case.case_id: observations[case.case_id].used_summary_path
        for case in broad_cases
    }
    if any(value is None for value in routes.values()):
        return _not_measured(
            "broad_questions_use_summary_path",
            "Summary-route observations are missing",
            actual=routes,
        )
    return _gate(
        "broad_questions_use_summary_path",
        all(bool(value) for value in routes.values()),
        actual=routes,
        threshold="all whole-document questions use hierarchical summaries",
        evidence="Candidate query-routing observations",
    )


def _latency_gate(
    observations: list[RetrievalEvaluationObservation],
    thresholds: AcceptanceThresholds,
) -> AcceptanceGate:
    if not observations:
        return _not_measured(
            "retrieval_p95_latency",
            "Candidate observations are empty",
        )
    p95 = _percentile(
        [observation.retrieval_latency_ms for observation in observations],
        0.95,
    )
    return _gate(
        "retrieval_p95_latency",
        p95 <= thresholds.retrieval_p95_target_ms,
        actual=round(p95, 3),
        threshold=thresholds.retrieval_p95_target_ms,
        evidence="Candidate retrieval latency observations",
    )


def _evidence_budget_gate(
    observations: list[RetrievalEvaluationObservation],
    thresholds: AcceptanceThresholds,
) -> AcceptanceGate:
    if not observations:
        return _not_measured(
            "average_evidence_tokens",
            "Candidate observations are empty",
        )
    average = fmean(
        observation.evidence_tokens for observation in observations
    )
    return _gate(
        "average_evidence_tokens",
        average <= thresholds.evidence_token_budget,
        actual=round(average, 3),
        threshold=thresholds.evidence_token_budget,
        evidence="Candidate evidence assembly observations",
    )


def _boolean_gate(
    criterion: str,
    value: bool | None,
    evidence: str,
) -> AcceptanceGate:
    if value is None:
        return _not_measured(criterion, "Operational evidence is missing")
    return _gate(
        criterion,
        value,
        actual=value,
        threshold=True,
        evidence=evidence,
    )


def _combined_boolean_gate(
    criterion: str,
    values: dict[str, bool | None],
    evidence: str,
) -> AcceptanceGate:
    if any(value is None for value in values.values()):
        return _not_measured(
            criterion,
            "One or more operational checks are missing",
            actual=values,
        )
    return _gate(
        criterion,
        all(bool(value) for value in values.values()),
        actual=values,
        threshold={name: True for name in values},
        evidence=evidence,
    )


def _gate(
    criterion: str,
    passed: bool,
    *,
    actual: object,
    threshold: object,
    evidence: str,
) -> AcceptanceGate:
    return AcceptanceGate(
        criterion=criterion,
        status=GateStatus.PASS if passed else GateStatus.FAIL,
        actual=actual,
        threshold=threshold,
        evidence=evidence,
    )


def _not_measured(
    criterion: str,
    evidence: str,
    *,
    actual: object = None,
) -> AcceptanceGate:
    return AcceptanceGate(
        criterion=criterion,
        status=GateStatus.NOT_MEASURED,
        actual=actual,
        threshold=None,
        evidence=evidence,
    )


def _metrics_dict(metrics: RetrievalEvaluationMetrics) -> dict[str, float | None]:
    return {
        key: float(value) if value is not None else None
        for key, value in asdict(metrics).items()
    }


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]
