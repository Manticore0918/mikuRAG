import argparse
import asyncio
import json
from pathlib import Path

from app.evaluation.contracts import EvaluationRunOptions

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = (
    BACKEND_ROOT / "evaluation" / "corpus" / "executable_v1" / "manifest.json"
)
DEFAULT_OUTPUT = BACKEND_ROOT / "evaluation" / "results"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest a versioned corpus through the real worker and execute the live "
            "mikuRAG retriever"
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--answers",
        action="store_true",
        help="Also run grounded generation and validation for every evaluation case",
    )
    parser.add_argument(
        "--keep-knowledge-base",
        action="store_true",
        help="Keep the isolated evaluation Knowledge Base for manual inspection",
    )
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--run-id")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    from app.evaluation.runner import EvaluationExecutionError, execute_evaluation

    try:
        result = await execute_evaluation(
            EvaluationRunOptions(
                dataset_path=args.dataset,
                output_dir=args.output_dir,
                include_answers=args.answers,
                keep_knowledge_base=args.keep_knowledge_base,
                ingestion_timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
                run_id=args.run_id,
            )
        )
    except EvaluationExecutionError as error:
        result = error.result
        print(
            json.dumps(
                {
                    "status": result.run.status,
                    "run_id": result.run.run_id,
                    "safe_error": result.run.safe_error,
                    "artifact_directory": str(result.artifacts.directory),
                    "raw_run": str(result.artifacts.raw_json),
                    "aggregate_report": str(result.artifacts.report_json),
                    "markdown_report": str(result.artifacts.report_markdown),
                    "knowledge_base_cleaned_up": (
                        result.run.knowledge_base_cleaned_up
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": result.run.status,
                "run_id": result.run.run_id,
                "evaluation_set_version": result.run.evaluation_set_version,
                "artifact_directory": str(result.artifacts.directory),
                "raw_run": str(result.artifacts.raw_json),
                "aggregate_report": str(result.artifacts.report_json),
                "markdown_report": str(result.artifacts.report_markdown),
                "knowledge_base_cleaned_up": result.run.knowledge_base_cleaned_up,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


async def _run_and_close(args: argparse.Namespace) -> int:
    from app.database import close_database

    try:
        return await _run(args)
    finally:
        await close_database()


def main() -> None:
    args = parse_args()
    exit_code = asyncio.run(_run_and_close(args))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
