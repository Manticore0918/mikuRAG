"""Chunking-profile comparison and the acceptance gate.

Given one ``EvaluationRunRecord`` per chunking profile against the same corpus,
this module builds a machine-readable comparison plus a Markdown experiment
report: quality per split and category, ingestion/storage statistics, retrieval
latency, evidence tokens, and a per-candidate acceptance verdict against the
existing acceptance report (``app.acceptance``). The builder is pure -- it needs
no database or worker -- so tests can exercise it with synthetic run records.
"""

from dataclasses import asdict

from app.acceptance import (
    AcceptanceReport,
    AcceptanceThresholds,
    OperationalAcceptanceEvidence,
    evaluate_acceptance,
)
from app.evaluation.contracts import EvaluationRunRecord
from app.evaluation.reporting import (
    evaluation_case_definitions,
    evaluation_metrics,
    evaluation_observations,
)

_SCHEMA_VERSION = "chunking_compare_v1"


def build_comparison_report(
    *,
    runs: dict[str, EvaluationRunRecord],
    thresholds: AcceptanceThresholds,
    baseline_profile: str,
    headline_split: str | None = None,
) -> dict[str, object]:
    """Compare profiles on the same corpus.

    ``runs`` maps a profile name to the run record produced against the same
    dataset. ``headline_split`` selects the split used for the headline metrics,
    acceptance verdict, and category winners (the untouched test split by default
    when the dataset has splits). Metrics are always reported per split too.
    """
    if baseline_profile not in runs:
        raise ValueError(f"baseline profile {baseline_profile!r} is not in the runs")
    for profile, run in runs.items():
        _validate_run(profile, run)

    version = _evaluation_set_version(runs)
    splits = sorted({item.split for run in runs.values() for item in run.cases})
    headline = headline_split or ("test" if "test" in splits else None)

    comparison: dict[str, object] = {}
    for profile, run in runs.items():
        selected = _cases_in_split(run.cases, headline)
        comparison[profile] = {
            "run_id": run.run_id,
            "status": run.status,
            "chunking_config_hash": run.chunking_config_hash,
            "case_count": len(selected),
            "metrics": _metrics_dict(selected),
            "by_split": {
                split: _metrics_dict(_cases_in_split(run.cases, split))
                for split in splits
            },
            "by_category": _by_category(selected),
            "ingestion_duration_ms": run.ingestion_duration_ms,
            "total_chunk_count": run.total_chunk_count,
            "embedding_input_count": run.embedding_input_count,
            "storage_estimate_bytes": run.storage_estimate_bytes,
            "is_baseline": profile == baseline_profile,
        }

    acceptance_by_profile = _acceptance_verdicts(
        runs=runs,
        thresholds=thresholds,
        baseline_profile=baseline_profile,
        headline_split=headline,
    )
    for profile, verdict in acceptance_by_profile.items():
        comparison[profile]["acceptance"] = verdict

    return {
        "schema_version": _SCHEMA_VERSION,
        "evaluation_set_version": version,
        "headline_split": headline,
        "baseline_profile": baseline_profile,
        "profiles": list(runs),
        "thresholds": {
            "retrieval_p95_target_ms": thresholds.retrieval_p95_target_ms,
            "evidence_token_budget": thresholds.evidence_token_budget,
            "minimum_quality_improvement": (
                thresholds.minimum_quality_improvement
            ),
        },
        "winners_by_category": _category_winners(
            runs=runs,
            comparison=comparison,
            baseline_profile=baseline_profile,
            headline_split=headline,
        ),
        "comparison": comparison,
    }


def render_compare_markdown(report: dict[str, object]) -> str:
    """Render the committed Markdown experiment report for ``report``."""
    comparison = _as_mapping(report["comparison"])
    baseline = str(report["baseline_profile"])
    split = report.get("headline_split")
    split_label = f" ({split} split)" if split else ""
    lines = [
        f"# Chunking comparison `{report['evaluation_set_version']}`",
        "",
        f"- Schema: `{report['schema_version']}`",
        f"- Evaluation set: `{report['evaluation_set_version']}`",
        f"- Baseline profile: `{baseline}`",
        f"- Headline metrics{split_label}",
        "",
        "## Quality, latency, and tokens",
        "",
        "| Profile | Recall@10 | MRR@10 | NDCG@10 | Filter | "
        "p95 retrieval (ms) | Mean evidence tokens |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for profile in report["profiles"]:
        row = _as_mapping(comparison[str(profile)])
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            lines.append(
                f"| `{profile}` | — | — | — | — | — | — |"
            )
            continue
        lines.append(
            f"| `{profile}` | {_fmt(metrics.get('recall_at_10'))} | "
            f"{_fmt(metrics.get('mean_reciprocal_rank'))} | "
            f"{_fmt(metrics.get('ndcg_at_10'))} | "
            f"{_fmt(metrics.get('filter_correctness'))} | "
            f"{_fmt(metrics.get('retrieval_latency_p95_ms'))} | "
            f"{_fmt(metrics.get('mean_evidence_tokens'))} |"
        )
    lines.extend(["", "## Ingestion and storage", "", "| Profile | Chunks | "
        "Embedding inputs | Storage (bytes) | Ingestion (ms) | Config hash |",
        "| --- | ---: | ---: | ---: | ---: | --- |"])
    for profile in report["profiles"]:
        row = _as_mapping(comparison[str(profile)])
        lines.append(
            f"| `{profile}` | {row['total_chunk_count']} | "
            f"{row['embedding_input_count']} | {row['storage_estimate_bytes']} | "
            f"{_fmt(row.get('ingestion_duration_ms'))} | "
            f"{str(row.get('chunking_config_hash'))[:16]}… |"
        )
    lines.extend(["", "## Winners by question category", "", "| Category | "
        "Winning profile | NDCG@10 |", "| --- | --- | ---: |"])
    winners = _as_mapping(report["winners_by_category"])
    for category in sorted(winners):
        winner = _as_mapping(winners[str(category)])
        lines.append(
            f"| {category} | `{winner['profile']}` | {_fmt(winner['ndcg_at_10'])} |"
        )
    lines.extend(["", "## Acceptance gate vs baseline", "", "| Candidate | "
        "Ready for default rollout | Deciding gates |", "| --- | --- | --- |"])
    for profile in report["profiles"]:
        if profile == baseline:
            continue
        row = _as_mapping(comparison[str(profile)])
        acceptance = row.get("acceptance")
        if not isinstance(acceptance, dict):
            lines.append(f"| `{profile}` | — | not evaluated |")
            continue
        gates = acceptance.get("gates")
        if not isinstance(gates, list) or not gates:
            reason = acceptance.get("reason", "no measured gates")
            lines.append(
                f"| `{profile}` | "
                f"{'**yes**' if acceptance.get('ready_for_default_rollout') else 'no'} "
                f"| {reason} |"
            )
            continue
        failing = [
            str(gate["criterion"]) for gate in gates if gate.get("status") == "fail"
        ]
        passing = [
            str(gate["criterion"]) for gate in gates if gate.get("status") == "pass"
        ]
        deciding = ", ".join(failing) or f"all measured gates pass ({len(passing)})"
        lines.append(
            f"| `{profile}` | "
            f"{'**yes**' if acceptance.get('ready_for_default_rollout') else 'no'} "
            f"| {deciding} |"
        )
    lines.append("")
    return "\n".join(lines)


def _acceptance_verdicts(
    *,
    runs: dict[str, EvaluationRunRecord],
    thresholds: AcceptanceThresholds,
    baseline_profile: str,
    headline_split: str | None,
) -> dict[str, dict[str, object] | None]:
    baseline_cases = _cases_in_split(runs[baseline_profile].cases, headline_split)
    if not baseline_cases:
        return {profile: None for profile in runs}
    baseline_definitions = evaluation_case_definitions(baseline_cases)
    baseline_ids = [item.case_id for item in baseline_cases]
    verdicts: dict[str, dict[str, object] | None] = {}
    for profile, run in runs.items():
        if profile == baseline_profile:
            verdicts[profile] = None
            continue
        candidate_cases = _cases_in_split(run.cases, headline_split)
        if [item.case_id for item in candidate_cases] != baseline_ids:
            verdicts[profile] = {
                "ready_for_default_rollout": False,
                "reason": (
                    "candidate and baseline ran different cases for the headline split"
                ),
            }
            continue
        acceptance = evaluate_acceptance(
            evaluation_set_version=run.evaluation_set_version,
            cases=baseline_definitions,
            baseline_observations=evaluation_observations(baseline_cases),
            candidate_observations=evaluation_observations(candidate_cases),
            benchmark_report={},
            operational_evidence=OperationalAcceptanceEvidence(),
            thresholds=thresholds,
        )
        verdicts[profile] = _acceptance_verdict(acceptance)
    return verdicts


def _acceptance_verdict(
    acceptance: AcceptanceReport,
) -> dict[str, object]:
    """Summarize an acceptance report for the compare.

    The compare has retrieval evidence only, so operational and capacity gates
    report ``not_measured``. A candidate is ``ready_for_default_rollout`` when no
    measured gate fails; unmeasured gates neither pass nor block.
    """
    gates = [
        {
            "criterion": gate.criterion,
            "status": str(gate.status.value),
            "actual": _json_safe(gate.actual),
            "threshold": _json_safe(gate.threshold),
            "evidence": gate.evidence,
        }
        for gate in acceptance.gates
    ]
    failing = [gate for gate in gates if gate["status"] == "fail"]
    return {
        "ready_for_default_rollout": not failing,
        "failing_gates": [gate["criterion"] for gate in failing],
        "gates": gates,
    }


def _by_category(
    cases: tuple,
) -> dict[str, dict[str, float | None] | None]:
    categories = sorted({item.category for item in cases})
    return {
        category: _metrics_dict(
            tuple(item for item in cases if item.category == category)
        )
        for category in categories
    }


def _category_winners(
    *,
    runs: dict[str, EvaluationRunRecord],
    comparison: dict[str, object],
    baseline_profile: str,
    headline_split: str | None,
) -> dict[str, dict[str, object]]:
    categories = sorted(
        {
            item.category
            for run in runs.values()
            for item in _cases_in_split(run.cases, headline_split)
        }
    )
    winners: dict[str, dict[str, object]] = {}
    for category in categories:
        best_profile: str | None = None
        best_ndcg: float | None = None
        for profile in runs:
            metrics = _as_mapping(comparison[str(profile)]).get("by_category")
            if not isinstance(metrics, dict):
                continue
            category_metrics = metrics.get(category)
            if not isinstance(category_metrics, dict):
                continue
            ndcg = category_metrics.get("ndcg_at_10")
            if not isinstance(ndcg, float):
                continue
            if best_ndcg is None or ndcg > best_ndcg or (
                ndcg == best_ndcg and profile == baseline_profile
            ):
                best_ndcg = ndcg
                best_profile = profile
        if best_profile is not None:
            winners[category] = {
                "profile": best_profile,
                "ndcg_at_10": best_ndcg,
            }
    return winners


def _metrics_dict(cases: tuple):
    if not cases:
        return None
    return {key: value for key, value in asdict(evaluation_metrics(cases)).items()}


def _cases_in_split(cases: tuple, split: str | None) -> tuple:
    if split is None:
        return cases
    return tuple(item for item in cases if item.split == split)


def _validate_run(profile: str, run: EvaluationRunRecord) -> None:
    if run.status != "completed":
        raise ValueError(
            f"cannot compare profile {profile!r}: run {run.run_id} "
            f"finished with status {run.status!r}"
        )
    if not run.cases:
        raise ValueError(
            f"cannot compare profile {profile!r}: run {run.run_id} has no cases"
        )


def _evaluation_set_version(runs: dict[str, EvaluationRunRecord]) -> str:
    versions = {run.evaluation_set_version for run in runs.values()}
    if len(versions) != 1:
        raise ValueError(
            "comparison runs must target the same evaluation set, got "
            f"{sorted(versions)}"
        )
    return versions.pop()


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _as_mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
