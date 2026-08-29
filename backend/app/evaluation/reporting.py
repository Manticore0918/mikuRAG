import json
import math
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.evaluation.contracts import (
    EvaluationArtifactPaths,
    EvaluationCaseRecord,
    EvaluationRunRecord,
)
from app.evaluation.faithfulness import aggregate_faithfulness
from app.rag.evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluationObservation,
    bootstrap_confidence_intervals,
    evaluate_retrieval,
)


def build_aggregate_report(run: EvaluationRunRecord) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run.run_id,
        "status": run.status,
        "evaluation_set_version": run.evaluation_set_version,
        "include_answers": run.include_answers,
        "document_count": len(run.documents),
        "ready_document_count": sum(item.status == "ready" for item in run.documents),
        "case_count": len(run.cases),
        "retrieval_pass_rate": _mean([float(item.retrieval_passed) for item in run.cases]),
        "answer_case_count": sum(item.answer is not None for item in run.cases),
        "answer_failure_count": sum(
            item.answer is not None and item.answer.safe_error is not None for item in run.cases
        ),
        "knowledge_base_cleaned_up": run.knowledge_base_cleaned_up,
        "safe_error": run.safe_error,
        "configuration": dict(run.configuration),
        "ingestion": {
            "ingestion_duration_ms": run.ingestion_duration_ms,
            "total_chunk_count": run.total_chunk_count,
            "embedding_input_count": run.embedding_input_count,
            "embedding_token_count": run.embedding_token_count,
            "storage_estimate_bytes": run.storage_estimate_bytes,
            "chunking_config_hash": run.chunking_config_hash,
            "documents_per_second": _rate(
                len(run.documents), run.ingestion_duration_ms
            ),
            "bytes_per_second": _rate(
                sum(item.size_bytes for item in run.documents),
                run.ingestion_duration_ms,
            ),
        },
        "faithfulness": _faithfulness_report(run.cases, include_answers=run.include_answers),
        "turn_measurements": _turn_measurement_report(run.cases),
    }
    if not run.cases:
        report["metrics"] = None
        report["by_split"] = {}
        report["by_category"] = {}
        report["confidence_intervals"] = None
        return report

    report["metrics"] = asdict(evaluation_metrics(run.cases))
    splits = sorted({item.split for item in run.cases})
    report["by_split"] = {
        split: asdict(evaluation_metrics(tuple(item for item in run.cases if item.split == split)))
        for split in splits
    }
    categories = sorted({item.category for item in run.cases})
    report["by_category"] = {
        category: asdict(
            evaluation_metrics(tuple(item for item in run.cases if item.category == category))
        )
        for category in categories
    }
    report["confidence_intervals"] = _confidence_intervals(run)
    if not run.include_answers:
        report["metrics"]["answer_faithfulness"] = None
        for metrics in report["by_category"].values():
            metrics["answer_faithfulness"] = None
        for metrics in report["by_split"].values():
            metrics["answer_faithfulness"] = None
        if isinstance(report["confidence_intervals"], dict):
            report["confidence_intervals"]["answer_faithfulness"] = {
                "mean": None,
                "ci_low": None,
                "ci_high": None,
            }
    return report


def _faithfulness_report(
    cases: tuple[EvaluationCaseRecord, ...],
    *,
    include_answers: bool,
) -> dict[str, object] | None:
    if not include_answers:
        return None
    available = [item for item in cases if item.faithfulness is not None]
    aggregate = aggregate_faithfulness(
        [dict(item.faithfulness or {}) for item in available]
    )
    if aggregate is None:
        return None
    categories = sorted({item.category for item in available})
    aggregate["by_category"] = {
        category: aggregate_faithfulness(
            [
                dict(item.faithfulness or {})
                for item in available
                if item.category == category
            ]
        )
        for category in categories
    }
    return aggregate


def _turn_measurement_report(
    cases: tuple[EvaluationCaseRecord, ...],
) -> dict[str, object] | None:
    measurements = [item.measurement for item in cases if item.measurement is not None]
    if not measurements:
        return None
    stage_names = sorted(
        {
            name
            for measurement in measurements
            for name in _mapping(measurement.get("latency_ms"))
        }
    )
    latency: dict[str, dict[str, float]] = {}
    for name in stage_names:
        values = sorted(
            float(stage[name])
            for measurement in measurements
            for stage in [_mapping(measurement.get("latency_ms"))]
            if name in stage
        )
        latency[name] = {
            "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95),
            "p99": _percentile(values, 0.99),
        }
    token_totals: Counter[str] = Counter()
    cache_counts: dict[str, Counter[str]] = {}
    estimated_spend = 0.0
    unpriced_tokens = 0
    for measurement in measurements:
        token_totals.update(
            {key: int(value) for key, value in _mapping(measurement.get("tokens")).items()}
        )
        for cache_name, status in _mapping(measurement.get("cache")).items():
            cache_counts.setdefault(cache_name, Counter())[str(status)] += 1
        cost = _mapping(measurement.get("cost"))
        estimated_spend += float(cost.get("estimated_api_spend") or 0.0)
        unpriced_tokens += int(cost.get("unpriced_token_count") or 0)
    return {
        "schema_version": 1,
        "turn_count": len(measurements),
        "latency_ms": latency,
        "token_totals": dict(token_totals),
        "cache": {name: dict(counts) for name, counts in cache_counts.items()},
        "cost": {
            "currency": "USD",
            "estimated_api_spend": round(estimated_spend, 8),
            "estimate_complete": unpriced_tokens == 0,
            "unpriced_token_count": unpriced_tokens,
        },
    }


def write_evaluation_artifacts(
    output_root: Path,
    run: EvaluationRunRecord,
    aggregate: dict[str, Any],
) -> EvaluationArtifactPaths:
    directory = output_root.resolve() / run.evaluation_set_version / run.run_id
    directory.mkdir(parents=True, exist_ok=False)
    raw_path = directory / "raw-run.json"
    report_path = directory / "report.json"
    markdown_path = directory / "report.md"
    _write_text_atomic(raw_path, _json_text(asdict(run)))
    _write_text_atomic(report_path, _json_text(aggregate))
    _write_text_atomic(markdown_path, _markdown_report(run, aggregate))
    return EvaluationArtifactPaths(
        directory=directory,
        raw_json=raw_path,
        report_json=report_path,
        report_markdown=markdown_path,
    )


def evaluation_case_definitions(
    cases: tuple[EvaluationCaseRecord, ...],
) -> list[RetrievalEvaluationCase]:
    return [
        RetrievalEvaluationCase(
            case_id=item.case_id,
            category=item.category,
            query=item.query,
            relevant_passage_ids=item.relevant_passage_ids,
            required_passage_ids=item.required_passage_ids,
            expected_citation_pages=item.expected_citation_pages,
            expects_supported_answer=item.expects_supported_answer,
            split=item.split,
            relevance_grades=dict(item.relevance_grades),
            filters=item.filters,
        )
        for item in cases
    ]


def evaluation_observations(
    cases: tuple[EvaluationCaseRecord, ...],
) -> list[RetrievalEvaluationObservation]:
    return [
        RetrievalEvaluationObservation(
            case_id=item.case_id,
            retrieved_passage_ids=item.retrieved_passage_ids,
            reranked_passage_ids=item.reranked_passage_ids,
            citation_pages=item.citation_pages,
            answer_faithful=item.answer_faithful,
            retrieval_latency_ms=item.retrieval_latency_ms,
            end_to_end_latency_ms=item.end_to_end_latency_ms,
            evidence_tokens=item.evidence_tokens,
            used_summary_path=item.used_summary_path,
            filter_correct=item.filter_correct,
        )
        for item in cases
    ]


def evaluation_metrics(
    cases: tuple[EvaluationCaseRecord, ...],
):
    return evaluate_retrieval(
        evaluation_case_definitions(cases),
        evaluation_observations(cases),
    )


def _markdown_report(run: EvaluationRunRecord, aggregate: dict[str, Any]) -> str:
    lines = [
        f"# Evaluation run `{run.run_id}`",
        "",
        f"- Status: **{run.status}**",
        f"- Evaluation set: `{run.evaluation_set_version}`",
        f"- Documents: {aggregate['ready_document_count']}/{aggregate['document_count']} Ready",
        f"- Cases: {aggregate['case_count']}",
        f"- Grounded answer path: {'enabled' if run.include_answers else 'disabled'}",
        f"- Grounded answer failures: {aggregate['answer_failure_count']}",
        f"- Isolated Knowledge Base cleaned up: {run.knowledge_base_cleaned_up}",
        "",
    ]
    if run.safe_error:
        lines.extend(["## Failure", "", run.safe_error, ""])
    configuration = run.configuration
    lines.extend(
        [
            "## Configuration",
            "",
            f"- Chunking version: `{configuration.get('chunking_version')}`",
            f"- Chunking config hash: `{configuration.get('chunking_config_hash')}`",
            f"- Embedding model: `{configuration.get('embedding_model_id')}`",
            f"- Retrieval mode: `{configuration.get('retrieval_mode')}`",
            f"- Reranker provider: `{configuration.get('reranker_provider')}`",
            f"- Query planning: {'on' if configuration.get('query_planning') else 'off'}",
            f"- BM25 hybrid: {'on' if configuration.get('bm25_hybrid_enabled') else 'off'}",
            f"- RRF: k={configuration.get('retrieval_rrf_k')} "
            f"semantic={configuration.get('retrieval_rrf_semantic_weight')} "
            f"lexical={configuration.get('retrieval_rrf_lexical_weight')}",
            f"- Retrieval semantic candidates: "
            f"{configuration.get('retrieval_semantic_candidates')}",
            f"- Retrieval lexical candidates: {configuration.get('retrieval_lexical_candidates')}",
            f"- Evidence token budget: {configuration.get('retrieval_evidence_token_budget')}",
            "",
        ]
    )
    ingestion = aggregate.get("ingestion")
    if isinstance(ingestion, dict):
        duration = ingestion.get("ingestion_duration_ms")
        lines.extend(
            [
                "## Ingestion and storage",
                "",
                f"- Ingestion duration: {_fmt_value(duration)} ms",
                f"- Chunks: {ingestion.get('total_chunk_count')}",
                f"- Embedding inputs: {ingestion.get('embedding_input_count')}",
                f"- Embedding tokens: {ingestion.get('embedding_token_count')}",
                f"- Ingestion throughput: "
                f"{_fmt_value(ingestion.get('documents_per_second'))} Documents/s, "
                f"{_fmt_value(ingestion.get('bytes_per_second'))} bytes/s",
                f"- Storage estimate: {ingestion.get('storage_estimate_bytes')} bytes",
                f"- Chunking config hash: `{ingestion.get('chunking_config_hash')}`",
                "",
            ]
        )
    metrics = aggregate.get("metrics")
    if metrics:
        lines.extend(
            [
                "## Aggregate metrics",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
            ]
        )
        for name, value in metrics.items():
            rendered = "not run" if value is None else f"{value:.4f}"
            lines.append(f"| `{name}` | {rendered} |")
        lines.append("")
    faithfulness = aggregate.get("faithfulness")
    if isinstance(faithfulness, dict):
        lines.extend(
            [
                "## Answer faithfulness",
                "",
                f"- Evaluator: `{faithfulness.get('evaluator')}` "
                f"v`{faithfulness.get('evaluator_version')}`",
                f"- Human audit rate: {_fmt_metric(faithfulness.get('human_audit_rate'))}",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
            ]
        )
        for name in (
            "citation_precision",
            "citation_recall",
            "claim_citation_coverage",
            "unsupported_citation_rate",
            "refusal_correctness",
            "answer_completeness",
            "claim_support",
        ):
            lines.append(f"| `{name}` | {_fmt_metric(faithfulness.get(name))} |")
        lines.append("")
    turn_measurements = aggregate.get("turn_measurements")
    if isinstance(turn_measurements, dict):
        lines.extend(
            [
                "## Latency, tokens, cache, and cost",
                "",
                "| Stage | p50 (ms) | p95 (ms) | p99 (ms) |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        latency = _mapping(turn_measurements.get("latency_ms"))
        for name in sorted(latency):
            percentiles = _mapping(latency[name])
            lines.append(
                f"| `{name}` | {_fmt_metric(percentiles.get('p50'))} | "
                f"{_fmt_metric(percentiles.get('p95'))} | "
                f"{_fmt_metric(percentiles.get('p99'))} |"
            )
        cost = _mapping(turn_measurements.get("cost"))
        lines.extend(
            [
                "",
                f"- Estimated API spend: {cost.get('estimated_api_spend')} "
                f"{cost.get('currency', 'USD')}",
                f"- Unpriced tokens: {cost.get('unpriced_token_count')}",
                "- Cache outcomes: `"
                f"{json.dumps(turn_measurements.get('cache', {}), sort_keys=True)}`",
                "",
            ]
        )
    by_split = aggregate.get("by_split")
    if isinstance(by_split, dict) and by_split:
        lines.extend(
            [
                "## Metrics by split",
                "",
                "| Split | Cases | Recall@10 | MRR@10 | NDCG@10 | "
                "p95 retrieval (ms) | Mean evidence tokens |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for split in sorted(by_split):
            split_metrics = by_split[split]
            case_count = sum(item.split == split for item in run.cases)
            if not isinstance(split_metrics, dict):
                lines.append(f"| {split} | {case_count} | — | — | — | — | — |")
                continue
            lines.append(
                f"| {split} | {case_count} | "
                f"{_fmt_metric(split_metrics.get('recall_at_10'))} | "
                f"{_fmt_metric(split_metrics.get('mean_reciprocal_rank'))} | "
                f"{_fmt_metric(split_metrics.get('ndcg_at_10'))} | "
                f"{_fmt_metric(split_metrics.get('retrieval_latency_p95_ms'))} | "
                f"{_fmt_metric(split_metrics.get('mean_evidence_tokens'))} |"
            )
        lines.append("")
    intervals = aggregate.get("confidence_intervals")
    if isinstance(intervals, dict) and intervals:
        lines.extend(
            [
                "## Bootstrap confidence intervals",
                "",
                "| Metric | Mean | 95% CI low | 95% CI high |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for name in sorted(intervals):
            interval = intervals[name]
            if not isinstance(interval, dict):
                continue
            lines.append(
                f"| `{name}` | {_fmt_metric(interval.get('mean'))} | "
                f"{_fmt_metric(interval.get('ci_low'))} | "
                f"{_fmt_metric(interval.get('ci_high'))} |"
            )
        lines.append("")
    if run.cases:
        lines.extend(
            [
                "## Cases",
                "",
                "| Case | Category | Required passages | Retrieved passages | "
                "Pass | Latency (ms) |",
                "| --- | --- | --- | --- | --- | ---: |",
            ]
        )
        for item in run.cases:
            required = ", ".join(item.required_passage_ids) or "none"
            retrieved = ", ".join(item.reranked_passage_ids) or "none"
            lines.append(
                f"| `{item.case_id}` | {item.category} | {required} | {retrieved} | "
                f"{'yes' if item.retrieval_passed else 'no'} | "
                f"{item.retrieval_latency_ms:.2f} |"
            )
        lines.append("")
    return "\n".join(lines)


def _json_text(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _confidence_intervals(
    run: EvaluationRunRecord,
) -> dict[str, dict[str, float]] | None:
    samples = int(run.configuration.get("bootstrap_samples") or 0)
    if samples <= 0 or not run.cases:
        return None
    raw_seed = run.configuration.get("bootstrap_seed")
    seed = int(raw_seed) if isinstance(raw_seed, int) else None
    return bootstrap_confidence_intervals(
        evaluation_case_definitions(run.cases),
        evaluation_observations(run.cases),
        samples=samples,
        seed=seed,
    )


def _fmt_metric(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _fmt_value(value: object) -> str:
    return "—" if value is None else str(value)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rate(value: int, duration_ms: float | None) -> float | None:
    if duration_ms is None or duration_ms <= 0:
        return None
    return round(value / (duration_ms / 1_000), 4)


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] * (upper - position) + values[upper] * (position - lower)
