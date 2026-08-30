"""Contract tests for the dependency-free Compose report validator."""

import importlib.util
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pytest


def _load_validator() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "evaluation_report_validation.py"
    spec = importlib.util.spec_from_file_location("evaluation_report_validation", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load report validator from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = _load_validator()


def _metrics() -> dict[str, float | None]:
    return {
        "recall_at_1": 0.5,
        "recall_at_5": 1.0,
        "recall_at_10": 1.0,
        "recall_after_reranking": 1.0,
        "mean_reciprocal_rank": 0.75,
        "ndcg_at_10": 0.8,
        "citation_page_accuracy": 1.0,
        "citation_precision": 0.75,
        "answer_faithfulness": None,
        "all_required_passages_rate": 1.0,
        "filter_correctness": None,
        "mean_retrieval_latency_ms": 10.0,
        "retrieval_latency_p95_ms": 12.0,
        "mean_end_to_end_latency_ms": 20.0,
        "end_to_end_latency_p95_ms": 24.0,
        "mean_evidence_tokens": 120.0,
    }


def _report() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "smoke-run",
        "status": "completed",
        "evaluation_set_version": "executable_v1",
        "include_answers": False,
        "document_count": 4,
        "ready_document_count": 4,
        "case_count": 2,
        "answer_case_count": 0,
        "answer_failure_count": 0,
        "knowledge_base_cleaned_up": True,
        "safe_error": None,
        "configuration": {
            "chunking_version": "legacy",
            "embedding_model_id": "stub-embedding",
            "retrieval_mode": "hybrid_rrf",
            "reranker_provider": "deterministic",
            "query_planning": True,
        },
        "ingestion": {
            "ingestion_duration_ms": 100.0,
            "total_chunk_count": 8,
            "embedding_input_count": 8,
            "embedding_token_count": 80,
            "storage_estimate_bytes": 1024,
            "chunking_config_hash": "a" * 64,
        },
        "metrics": _metrics(),
        "by_category": {"narrow_fact": _metrics()},
        "by_split": {"test": _metrics()},
    }


def test_validate_evaluation_report_accepts_complete_v1_smoke_report() -> None:
    VALIDATOR.validate_evaluation_report(_report(), expected_case_count=2)


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda report: report.update(status="failed"), "status"),
        (lambda report: report.update(case_count="2"), "case_count"),
        (lambda report: report.update(ready_document_count=3), "ready_document_count"),
        (lambda report: report.update(knowledge_base_cleaned_up=False), "knowledge_base"),
        (lambda report: report["metrics"].update(recall_at_10=1.1), "recall_at_10"),
        (lambda report: report.update(by_category={}), "by_category"),
        (
            lambda report: report["configuration"].update(embedding_model_id=""),
            "embedding_model_id",
        ),
        (
            lambda report: report["ingestion"].update(embedding_input_count=-1),
            "embedding_input_count",
        ),
    ],
)
def test_validate_evaluation_report_rejects_invalid_contract(mutate, path: str) -> None:
    report = deepcopy(_report())
    mutate(report)

    with pytest.raises(VALIDATOR.EvaluationReportValidationError, match=path):
        VALIDATOR.validate_evaluation_report(report, expected_case_count=2)


def test_validate_evaluation_report_enforces_smoke_subset_size() -> None:
    report = _report()
    report["case_count"] = 3

    with pytest.raises(VALIDATOR.EvaluationReportValidationError, match="must equal 2"):
        VALIDATOR.validate_evaluation_report(report, expected_case_count=2)
