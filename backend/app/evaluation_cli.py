"""Executable evaluation command line interface.

``run`` ingests a versioned corpus through the real worker and executes the live
retriever for one configuration. ``compare`` runs every chunking profile against
the same corpus and produces a machine-readable plus human-readable comparison
with an acceptance verdict for each candidate profile.

Examples
--------
Run one profile::

    python -m app.evaluation_cli run --dataset evaluation/corpus/gold_v1/manifest.json

Compare all profiles on the untouched test split::

    python -m app.evaluation_cli compare \\
        --dataset evaluation/corpus/gold_v1/manifest.json --split test

Both commands accept ``--max-cases`` to run a small smoke subset for CI.
"""

import argparse
import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from app.evaluation.contracts import EvaluationRunOptions
from app.evaluation.datasets import load_executable_dataset

BACKEND_ROOT = Path(__file__).resolve().parents[1]
GOLD_DATASET = (
    BACKEND_ROOT / "evaluation" / "corpus" / "gold_v1" / "manifest.json"
)
EXECUTABLE_DATASET = (
    BACKEND_ROOT / "evaluation" / "corpus" / "executable_v1" / "manifest.json"
)
DEFAULT_OUTPUT = BACKEND_ROOT / "evaluation" / "results"
DEFAULT_PROFILES = ("legacy_char_v1", "token_recursive_v1", "hierarchical_v1")
_SUPPORTED_SPLITS = ("all", "train", "dev", "test")


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
        help="Profile treated as the baseline for the acceptance gate "
        "(default: the first profile)",
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
    compare.set_defaults(handler=_handle_compare)
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
    compare_dir = (
        args.output_dir.resolve() / "compare" / version / split_label
    )
    compare_dir.mkdir(parents=True, exist_ok=True)
    compare_json = compare_dir / "compare.json"
    compare_md = compare_dir / "compare.md"
    compare_json.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    compare_md.write_text(
        render_compare_markdown(report), encoding="utf-8", newline="\n"
    )
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
                        _as_mapping(_as_mapping(report["comparison"])[str(profile)])[
                            "run_id"
                        ]
                    )
                    for profile in args.profiles
                },
                "acceptance": {
                    str(profile): (
                        _as_mapping(
                            _as_mapping(report["comparison"])[str(profile)]
                        )
                        .get("acceptance")
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
