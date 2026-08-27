import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.evaluation.contracts import (
    EvaluationArtifactPaths,
    EvaluationCaseRecord,
    EvaluationRunRecord,
)
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
            "storage_estimate_bytes": run.storage_estimate_bytes,
            "chunking_config_hash": run.chunking_config_hash,
        },
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
