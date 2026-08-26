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
        "retrieval_pass_rate": _mean(
            [float(item.retrieval_passed) for item in run.cases]
        ),
        "answer_case_count": sum(item.answer is not None for item in run.cases),
        "answer_failure_count": sum(
            item.answer is not None and item.answer.safe_error is not None
            for item in run.cases
        ),
        "knowledge_base_cleaned_up": run.knowledge_base_cleaned_up,
        "safe_error": run.safe_error,
        "configuration": dict(run.configuration),
    }
    if not run.cases:
        report["metrics"] = None
        report["by_category"] = {}
        return report

    report["metrics"] = asdict(_evaluate_cases(run.cases))
    categories = sorted({item.category for item in run.cases})
    report["by_category"] = {
        category: asdict(
            _evaluate_cases(tuple(item for item in run.cases if item.category == category))
        )
        for category in categories
    }
    if not run.include_answers:
        report["metrics"]["answer_faithfulness"] = None
        for metrics in report["by_category"].values():
            metrics["answer_faithfulness"] = None
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


def _evaluate_cases(cases: tuple[EvaluationCaseRecord, ...]):
    definitions = [
        RetrievalEvaluationCase(
            case_id=item.case_id,
            category=item.category,
            query=item.query,
            relevant_passage_ids=item.relevant_passage_ids,
            required_passage_ids=item.required_passage_ids,
            expected_citation_pages=item.expected_citation_pages,
            expects_supported_answer=item.expects_supported_answer,
        )
        for item in cases
    ]
    observations = [
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
        )
        for item in cases
    ]
    return evaluate_retrieval(definitions, observations)


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


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
