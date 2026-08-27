"""Executable evaluation command line interface.

``run`` ingests a versioned corpus through the real worker and executes the live
retriever for one configuration. ``compare`` runs every chunking profile against
the same corpus and produces a machine-readable plus human-readable comparison
with an acceptance verdict for each candidate profile. ``ablation`` runs every
retrieval experiment mode against the same corpus and publishes a
Recall@10/MRR@10/NDCG@10/p95/evidence-token table for the frozen split.

Examples
--------
Run one profile::

    python -m app.evaluation_cli run --dataset evaluation/corpus/gold_v1/manifest.json

Run the BM25 mode with the cross-encoder reranker on the test split::

    python -m app.evaluation_cli ablation \\
        --dataset evaluation/corpus/gold_v1/manifest.json \\
        --modes bm25 hybrid_rrf_reranked --reranker cross_encoder --split test

Compare all profiles on the untouched test split::

    python -m app.evaluation_cli compare \\
        --dataset evaluation/corpus/gold_v1/manifest.json --split test

All commands accept ``--max-cases`` to run a small smoke subset for CI.
"""

import argparse
import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.evaluation.contracts import EvaluationRunOptions
from app.evaluation.datasets import load_executable_dataset

if TYPE_CHECKING:
    from app.evaluation.runner import EvaluationExecutionResult

BACKEND_ROOT = Path(__file__).resolve().parents[1]
GOLD_DATASET = BACKEND_ROOT / "evaluation" / "corpus" / "gold_v1" / "manifest.json"
EXECUTABLE_DATASET = BACKEND_ROOT / "evaluation" / "corpus" / "executable_v1" / "manifest.json"
DEFAULT_OUTPUT = BACKEND_ROOT / "evaluation" / "results"
DEFAULT_PROFILES = ("legacy_char_v1", "token_recursive_v1", "hierarchical_v1")
_SUPPORTED_SPLITS = ("all", "train", "dev", "test")
_RETRIEVAL_MODES = (
    "vector",
    "fts_baseline",
    "bm25",
    "hybrid_rrf",
    "hybrid_rrf_reranked",
)
_RERANKER_PROVIDERS = ("deterministic", "cross_encoder")


def _add_retrieval_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--retrieval-mode",
        choices=_RETRIEVAL_MODES,
        default=None,
        help="Retrieval experiment mode (default: the configured mode)",
    )
    parser.add_argument(
        "--reranker",
        choices=_RERANKER_PROVIDERS,
        default=None,
        help="Reranker provider for reranked modes (default: the configured provider)",
    )
    parser.add_argument(
        "--bm25-hybrid-enabled",
        action="store_true",
        default=None,
        help="Use the pg_search BM25 leg in hybrid modes (default: the configured flag)",
    )
    parser.add_argument(
        "--no-query-planning",
        action="store_false",
        dest="query_planning",
        default=True,
        help="Disable the typed query plan for each evaluation case",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mikurag-eval",
        description=(
            "Ingest a versioned corpus through the real worker and execute the "
            "live mikuRAG retriever, optionally comparing chunking profiles"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser(
        "run", help="execute one evaluation run with a single chunking profile"
    )
    run.add_argument("--dataset", type=Path, default=EXECUTABLE_DATASET)
    run.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    run.add_argument(
        "--answers",
        action="store_true",
        help="Also run grounded generation and validation for every evaluation case",
    )
    run.add_argument(
        "--keep-knowledge-base",
        action="store_true",
        help="Keep the isolated evaluation Knowledge Base for manual inspection",
    )
    run.add_argument("--timeout-seconds", type=int, default=300)
    run.add_argument("--poll-seconds", type=float, default=2.0)
    run.add_argument("--run-id")
    run.add_argument(
        "--chunking-version",
        help="Chunker profile to ingest with (default: the configured version)",
    )
    run.add_argument("--bootstrap-samples", type=int, default=2000)
    run.add_argument("--bootstrap-seed", type=int, default=0)
    run.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Run only the first N cases (smoke subset for CI)",
    )
    _add_retrieval_flags(run)
    run.set_defaults(handler=_handle_run)

    compare = subparsers.add_parser(
        "compare",
        help="run every chunking profile against the same corpus and compare",
    )
    compare.add_argument("--dataset", type=Path, default=GOLD_DATASET)
    compare.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    compare.add_argument(
        "--profiles",
        nargs="+",
        default=list(DEFAULT_PROFILES),
        choices=["legacy_char_v1", "token_recursive_v1", "hierarchical_v1"],
        help="Profiles to compare (default: all three)",
    )
    compare.add_argument(
        "--baseline",
        default=None,
        help="Profile treated as the baseline for the acceptance gate (default: the first profile)",
    )
    compare.add_argument(
        "--split",
        choices=_SUPPORTED_SPLITS,
        default="test",
        help="Headline split for metrics and the acceptance gate (default: test)",
    )
    compare.add_argument(
        "--answers",
        action="store_true",
        help="Also run grounded generation for every evaluation case",
    )
    compare.add_argument("--timeout-seconds", type=int, default=300)
    compare.add_argument("--poll-seconds", type=float, default=2.0)
    compare.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Run only the first N cases per profile (smoke subset for CI)",
    )
    compare.add_argument("--bootstrap-samples", type=int, default=2000)
    compare.add_argument("--bootstrap-seed", type=int, default=0)
    _add_retrieval_flags(compare)
    compare.set_defaults(handler=_handle_compare)

    ablation = subparsers.add_parser(
        "ablation",
        help=(
            "run retrieval-mode ablations on the same corpus and publish a "
            "Recall@10/MRR@10/NDCG@10/p95/evidence-token table"
        ),
    )
    ablation.add_argument("--dataset", type=Path, default=GOLD_DATASET)
    ablation.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    ablation.add_argument(
        "--modes",
        nargs="+",
        default=list(_RETRIEVAL_MODES),
        choices=_RETRIEVAL_MODES,
        help="Modes to ablate (default: all five experiment modes)",
    )
    ablation.add_argument(
        "--reranker",
        choices=_RERANKER_PROVIDERS,
        default=None,
        help="Reranker provider for reranked modes (default: the configured provider)",
    )
    ablation.add_argument(
        "--bm25-hybrid-enabled",
        action="store_true",
        default=None,
        help="Use the pg_search BM25 leg in hybrid modes (default: the configured flag)",
    )
    ablation.add_argument(
        "--split",
        choices=_SUPPORTED_SPLITS,
        default="test",
        help="Headline split for the ablation table (default: test)",
    )
    ablation.add_argument(
        "--query-planning",
        choices=("both", "on", "off"),
        default="both",
        help="Ablate rewritten and original queries (default: both)",
    )
    ablation.add_argument("--timeout-seconds", type=int, default=300)
    ablation.add_argument("--poll-seconds", type=float, default=2.0)
    ablation.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Run only the first N cases per mode (smoke subset for CI)",
    )
    ablation.add_argument("--bootstrap-samples", type=int, default=2000)
    ablation.add_argument("--bootstrap-seed", type=int, default=0)
    ablation.set_defaults(handler=_handle_ablation)
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


async def _handle_run(args: argparse.Namespace) -> int:
    from app.evaluation.runner import EvaluationExecutionError, execute_evaluation

    dataset = load_executable_dataset(args.dataset)
    if args.max_cases:
        dataset = replace(dataset, cases=dataset.cases[: args.max_cases])
    options = EvaluationRunOptions(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        include_answers=args.answers,
        keep_knowledge_base=args.keep_knowledge_base,
        ingestion_timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
        run_id=args.run_id,
        target_chunking_version=args.chunking_version,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
        retrieval_mode=args.retrieval_mode,
        reranker_provider=args.reranker,
        bm25_hybrid_enabled=args.bm25_hybrid_enabled,
        query_planning=args.query_planning,
    )
    try:
        result = await execute_evaluation(options, dataset=dataset)
    except EvaluationExecutionError as error:
        result = error.result
        print(_run_summary(result), flush=True)
        return 1
    print(_run_summary(result), flush=True)
    return 0


async def _handle_compare(args: argparse.Namespace) -> int:
    from app.acceptance import AcceptanceThresholds
    from app.config import get_settings
    from app.evaluation.compare import (
        build_comparison_report,
        render_compare_markdown,
    )
    from app.evaluation.runner import EvaluationExecutionError, execute_evaluation

    dataset = load_executable_dataset(args.dataset)
    if args.max_cases:
        dataset = replace(dataset, cases=dataset.cases[: args.max_cases])
    baseline_profile = args.baseline or args.profiles[0]

    runs: dict[str, object] = {}
    for profile in args.profiles:
        run_id = _profile_run_id(profile)
        try:
            result = await execute_evaluation(
                EvaluationRunOptions(
                    dataset_path=args.dataset,
                    output_dir=args.output_dir,
                    include_answers=args.answers,
                    ingestion_timeout_seconds=args.timeout_seconds,
                    poll_seconds=args.poll_seconds,
                    run_id=run_id,
                    target_chunking_version=profile,
                    bootstrap_samples=args.bootstrap_samples,
                    bootstrap_seed=args.bootstrap_seed,
                    retrieval_mode=args.retrieval_mode,
                    reranker_provider=args.reranker,
                    bm25_hybrid_enabled=args.bm25_hybrid_enabled,
                    query_planning=args.query_planning,
                ),
                dataset=dataset,
            )
        except EvaluationExecutionError as error:
            print(
                json.dumps(
                    {
                        "profile": profile,
                        "status": error.result.run.status,
                        "run_id": error.result.run.run_id,
                        "safe_error": error.result.run.safe_error,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                flush=True,
            )
            return 1
        runs[profile] = result.run

    report = build_comparison_report(
        runs=runs,
        thresholds=AcceptanceThresholds.from_settings(get_settings()),
        baseline_profile=baseline_profile,
        headline_split=None if args.split == "all" else args.split,
    )
    version = str(report["evaluation_set_version"])
    split_label = str(report["headline_split"] or "all")
    compare_dir = args.output_dir.resolve() / "compare" / version / split_label
    compare_dir.mkdir(parents=True, exist_ok=True)
    compare_json = compare_dir / "compare.json"
    compare_md = compare_dir / "compare.md"
    compare_json.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    compare_md.write_text(render_compare_markdown(report), encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "schema_version": report["schema_version"],
                "evaluation_set_version": version,
                "headline_split": split_label,
                "baseline_profile": baseline_profile,
                "profiles": args.profiles,
                "compare_report": str(compare_json),
                "markdown_report": str(compare_md),
                "profile_runs": {
                    str(profile): str(
                        _as_mapping(_as_mapping(report["comparison"])[str(profile)])["run_id"]
                    )
                    for profile in args.profiles
                },
                "acceptance": {
                    str(profile): (
                        _as_mapping(_as_mapping(report["comparison"])[str(profile)]).get(
                            "acceptance"
                        )
                        or {}
                    ).get("ready_for_default_rollout")
                    for profile in args.profiles
                    if profile != baseline_profile
                },
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


async def _handle_ablation(args: argparse.Namespace) -> int:
    from app.evaluation.runner import EvaluationExecutionError, execute_evaluation

    dataset = load_executable_dataset(args.dataset)
    if args.max_cases:
        dataset = replace(dataset, cases=dataset.cases[: args.max_cases])
    results: dict[str, EvaluationExecutionResult] = {}
    planning_variants = (
        (True, False)
        if args.query_planning == "both"
        else (args.query_planning == "on",)
    )
    for mode in args.modes:
        for query_planning in planning_variants:
            label = f"{mode}:{'rewritten' if query_planning else 'original'}"
            run_id = _ablation_run_id(label)
            try:
                results[label] = await execute_evaluation(
                    EvaluationRunOptions(
                        dataset_path=args.dataset,
                        output_dir=args.output_dir,
                        include_answers=False,
                        ingestion_timeout_seconds=args.timeout_seconds,
                        poll_seconds=args.poll_seconds,
                        run_id=run_id,
                        target_chunking_version=None,
                        bootstrap_samples=args.bootstrap_samples,
                        bootstrap_seed=args.bootstrap_seed,
                        retrieval_mode=mode,
                        reranker_provider=args.reranker,
                        bm25_hybrid_enabled=args.bm25_hybrid_enabled,
                        query_planning=query_planning,
                    ),
                    dataset=dataset,
                )
            except EvaluationExecutionError as error:
                print(
                    json.dumps(
                        {
                            "mode": mode,
                            "query_planning": query_planning,
                            "status": error.result.run.status,
                            "run_id": error.result.run.run_id,
                            "safe_error": error.result.run.safe_error,
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return 1

    table = _ablation_table(results, args.split)
    version = str(table["evaluation_set_version"])
    split_label = str(table["headline_split"])
    ablation_dir = args.output_dir.resolve() / "ablation" / version / split_label
    ablation_dir.mkdir(parents=True, exist_ok=True)
    ablation_json = ablation_dir / "ablation.json"
    ablation_md = ablation_dir / "ablation.md"
    ablation_json.write_text(
        json.dumps(table, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    ablation_md.write_text(_render_ablation_markdown(table), encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "schema_version": table["schema_version"],
                "evaluation_set_version": version,
                "headline_split": split_label,
                "reranker_provider": args.reranker,
                "bm25_hybrid_enabled": args.bm25_hybrid_enabled,
                "modes": args.modes,
                "query_planning": args.query_planning,
                "ablation_json": str(ablation_json),
                "ablation_markdown": str(ablation_md),
                "configs": [
                    {
                        "mode": row["mode"],
                        "query_planning": row["query_planning"],
                        "recall_at_10": row["recall_at_10"],
                        "mean_reciprocal_rank": row["mean_reciprocal_rank"],
                        "ndcg_at_10": row["ndcg_at_10"],
                        "retrieval_latency_p95_ms": row["retrieval_latency_p95_ms"],
                        "mean_evidence_tokens": row["mean_evidence_tokens"],
                        "effective_lexical_kinds": row["effective_lexical_kinds"],
                        "effective_reranker_providers": row[
                            "effective_reranker_providers"
                        ],
                        "valid_for_headline": row["valid_for_headline"],
                        "run_id": row["run_id"],
                    }
                    for row in table["configs"]
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


def _ablation_table(
    results: dict[str, "EvaluationExecutionResult"],
    split: str,
) -> dict[str, object]:
    """Assemble the per-config metric table from each mode's aggregate report."""
    rows: list[dict[str, object]] = []
    evaluation_set_version: str | None = None
    for label, result in results.items():
        aggregate = _as_mapping(result.aggregate)
        metrics = aggregate.get("metrics")
        if split != "all" and isinstance(aggregate.get("by_split"), dict):
            metrics = _as_mapping(aggregate.get("by_split")).get(split) or metrics
        run = result.run
        mode = str(run.configuration.get("retrieval_mode") or label.split(":", 1)[0])
        query_planning = bool(run.configuration.get("query_planning", True))
        cases = [
            case for case in run.cases if split == "all" or case.split == split
        ]
        lexical_kinds = sorted(
            {
                str(value)
                for case in cases
                if (value := case.retrieval_metrics.get("lexical_kind")) is not None
            }
        )
        reranker_providers = sorted(
            {
                str(value)
                for case in cases
                if (value := case.retrieval_metrics.get("reranker_provider")) is not None
            }
        )
        bm25_requested = mode == "bm25" or (
            mode in {"hybrid_rrf", "hybrid_rrf_reranked"}
            and bool(run.configuration.get("bm25_hybrid_enabled"))
        )
        bm25_fallback = bm25_requested and lexical_kinds != ["bm25"]
        reranker_fallback = mode == "hybrid_rrf_reranked" and (
            "fallback_fused_order" in reranker_providers
        )
        learned_reranker_ran = mode != "hybrid_rrf_reranked" or (
            reranker_providers == ["cross_encoder"]
        )
        rows.append(
            {
                "mode": mode,
                "query_planning": query_planning,
                "run_id": run.run_id,
                "status": run.status,
                "recall_at_10": _metric(metrics, "recall_at_10"),
                "mean_reciprocal_rank": _metric(metrics, "mean_reciprocal_rank"),
                "ndcg_at_10": _metric(metrics, "ndcg_at_10"),
                "retrieval_latency_p95_ms": _metric(metrics, "retrieval_latency_p95_ms"),
                "mean_evidence_tokens": _metric(metrics, "mean_evidence_tokens"),
                "effective_lexical_kinds": lexical_kinds,
                "effective_reranker_providers": reranker_providers,
                "fallback_used": bm25_fallback or reranker_fallback,
                "valid_for_headline": (
                    run.status == "completed"
                    and not bm25_fallback
                    and not reranker_fallback
                    and learned_reranker_ran
                ),
            }
        )
        if evaluation_set_version is None:
            evaluation_set_version = run.evaluation_set_version
    return {
        "schema_version": 1,
        "evaluation_set_version": evaluation_set_version,
        "headline_split": "all" if split == "all" else split,
        "configs": rows,
    }


def _metric(metrics: object, name: str) -> float | None:
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(name)
    return float(value) if isinstance(value, (int, float)) else None


def _render_ablation_markdown(table: dict[str, object]) -> str:
    lines = [
        f"# Retrieval-mode ablation (`{table['evaluation_set_version']}`)",
        "",
        f"- Headline split: `{table['headline_split']}`",
        f"- Configs: {len(table['configs'])}",
        "",
        "| Requested mode | Query | Effective lexical leg | Effective reranker | "
        "Headline valid | Recall@10 | MRR@10 | NDCG@10 | p95 retrieval (ms) | "
        "Mean evidence tokens |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in table["configs"]:
        lines.append(
            f"| `{row['mode']}` | {'rewritten' if row['query_planning'] else 'original'} | "
            f"{_fmt_list(row['effective_lexical_kinds'])} | "
            f"{_fmt_list(row['effective_reranker_providers'])} | "
            f"{'yes' if row['valid_for_headline'] else 'no'} | "
            f"{_fmt_score(row['recall_at_10'])} | "
            f"{_fmt_score(row['mean_reciprocal_rank'])} | "
            f"{_fmt_score(row['ndcg_at_10'])} | "
            f"{_fmt_latency(row['retrieval_latency_p95_ms'])} | "
            f"{_fmt_tokens(row['mean_evidence_tokens'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _fmt_score(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def _fmt_latency(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _fmt_tokens(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"


def _fmt_list(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "-"
    return ", ".join(f"`{value}`" for value in values)


def _ablation_run_id(mode: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_mode = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in mode
    )
    return f"{safe_mode}-{timestamp}-{uuid.uuid4().hex[:6]}"


def _run_summary(result) -> dict[str, object]:
    return {
        "status": result.run.status,
        "run_id": result.run.run_id,
        "evaluation_set_version": result.run.evaluation_set_version,
        "chunking_config_hash": result.run.chunking_config_hash,
        "ingestion_duration_ms": result.run.ingestion_duration_ms,
        "total_chunk_count": result.run.total_chunk_count,
        "embedding_input_count": result.run.embedding_input_count,
        "storage_estimate_bytes": result.run.storage_estimate_bytes,
        "artifact_directory": str(result.artifacts.directory),
        "raw_run": str(result.artifacts.raw_json),
        "aggregate_report": str(result.artifacts.report_json),
        "markdown_report": str(result.artifacts.report_markdown),
        "knowledge_base_cleaned_up": result.run.knowledge_base_cleaned_up,
        "safe_error": result.run.safe_error,
    }


def _profile_run_id(profile: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{profile}-{timestamp}-{uuid.uuid4().hex[:6]}"


def _as_mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


async def _run_and_close(args: argparse.Namespace) -> int:
    from app.database import close_database

    try:
        return await args.handler(args)
    finally:
        await close_database()


def main() -> None:
    args = parse_args()
    exit_code = asyncio.run(_run_and_close(args))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
