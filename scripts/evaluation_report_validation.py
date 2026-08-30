"""Dependency-free validation for evaluation ``report.json`` artifacts.

The Compose smoke driver deliberately installs only httpx on the host.  Keep
this validator in ``scripts`` and stdlib-only so CI can verify the report
contract without installing the backend package or a second schema library.
"""

from collections.abc import Mapping
from math import isfinite
from typing import Any


class EvaluationReportValidationError(ValueError):
    """Raised when an evaluation report does not satisfy schema version 1."""


_QUALITY_METRICS = (
    "recall_at_1",
    "recall_at_5",
    "recall_at_10",
    "recall_after_reranking",
    "mean_reciprocal_rank",
    "ndcg_at_10",
    "citation_page_accuracy",
    "citation_precision",
    "all_required_passages_rate",
)
_NONNEGATIVE_METRICS = (
    "mean_retrieval_latency_ms",
    "retrieval_latency_p95_ms",
    "mean_end_to_end_latency_ms",
    "end_to_end_latency_p95_ms",
    "mean_evidence_tokens",
)


def validate_evaluation_report(
    report: object,
    *,
    expected_case_count: int | None = None,
) -> None:
    """Validate the stable, operationally important report-v1 contract.

    This checks types, successful-run invariants, metric ranges, configuration,
    ingestion accounting, and the category/split metric views.  It intentionally
    does not reject additive fields so newer report producers remain compatible
    with an older smoke driver.
    """

    root = _mapping(report, "report")
    if root.get("schema_version") != 1:
        _fail("schema_version", "must equal 1")
    _nonempty_string(root.get("run_id"), "run_id")
    _nonempty_string(root.get("evaluation_set_version"), "evaluation_set_version")
    if root.get("status") != "completed":
        _fail("status", "must equal 'completed'")
    if root.get("safe_error") is not None:
        _fail("safe_error", "must be null for a completed run")
    _boolean(root.get("include_answers"), "include_answers")
    if root.get("knowledge_base_cleaned_up") is not True:
        _fail("knowledge_base_cleaned_up", "must be true")

    case_count = _integer(root.get("case_count"), "case_count", minimum=1)
    if expected_case_count is not None and case_count != expected_case_count:
        _fail("case_count", f"must equal {expected_case_count}, got {case_count}")
    answer_case_count = _integer(
        root.get("answer_case_count"), "answer_case_count", minimum=0
    )
    answer_failure_count = _integer(
        root.get("answer_failure_count"), "answer_failure_count", minimum=0
    )
    if answer_case_count > case_count:
        _fail("answer_case_count", "cannot exceed case_count")
    if answer_failure_count > answer_case_count:
        _fail("answer_failure_count", "cannot exceed answer_case_count")

    document_count = _integer(root.get("document_count"), "document_count", minimum=1)
    ready_document_count = _integer(
        root.get("ready_document_count"), "ready_document_count", minimum=0
    )
    if ready_document_count != document_count:
        _fail(
            "ready_document_count",
            "must equal document_count for a completed smoke evaluation",
        )

    configuration = _mapping(root.get("configuration"), "configuration")
    for key in (
        "chunking_version",
        "embedding_model_id",
        "retrieval_mode",
        "reranker_provider",
    ):
        _nonempty_string(configuration.get(key), f"configuration.{key}")
    _boolean(configuration.get("query_planning"), "configuration.query_planning")

    ingestion = _mapping(root.get("ingestion"), "ingestion")
    _number(
        ingestion.get("ingestion_duration_ms"),
        "ingestion.ingestion_duration_ms",
        minimum=0,
    )
    for key in (
        "total_chunk_count",
        "embedding_input_count",
        "embedding_token_count",
        "storage_estimate_bytes",
    ):
        _integer(ingestion.get(key), f"ingestion.{key}", minimum=0)
    _nonempty_string(
        ingestion.get("chunking_config_hash"), "ingestion.chunking_config_hash"
    )

    _metrics(root.get("metrics"), "metrics")
    _metric_views(root.get("by_category"), "by_category")
    _metric_views(root.get("by_split"), "by_split")


def _metric_views(value: object, path: str) -> None:
    views = _mapping(value, path)
    if not views:
        _fail(path, "must contain at least one metric view")
    for name, metrics in views.items():
        _nonempty_string(name, f"{path} key")
        _metrics(metrics, f"{path}.{name}")


def _metrics(value: object, path: str) -> None:
    metrics = _mapping(value, path)
    for key in _QUALITY_METRICS:
        _number(metrics.get(key), f"{path}.{key}", minimum=0, maximum=1)
    for key in _NONNEGATIVE_METRICS:
        _number(metrics.get(key), f"{path}.{key}", minimum=0)

    for key in ("answer_faithfulness", "filter_correctness"):
        metric = metrics.get(key)
        if metric is not None:
            _number(metric, f"{path}.{key}", minimum=0, maximum=1)


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    return value


def _nonempty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-empty string")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(path, "must be a boolean")
    return value


def _integer(value: object, path: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "must be an integer")
    if value < minimum:
        _fail(path, f"must be >= {minimum}")
    return value


def _number(
    value: object,
    path: str,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _fail(path, "must be a finite number")
    number = float(value)
    if not isfinite(number):
        _fail(path, "must be a finite number")
    if number < minimum:
        _fail(path, f"must be >= {minimum}")
    if maximum is not None and number > maximum:
        _fail(path, f"must be <= {maximum}")
    return number


def _fail(path: str, message: str) -> None:
    raise EvaluationReportValidationError(f"Invalid evaluation report at {path}: {message}")
