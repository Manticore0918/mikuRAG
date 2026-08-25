import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.benchmarking import (
    SyntheticDocumentProfile,
    benchmark_concurrent_ingestion,
    benchmark_ingestion_matrix,
    benchmark_knowledge_base_capacity,
    run_retrieval_benchmark,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run repeatable mikuRAG chunking and retrieval capacity benchmarks."
    )
    parser.add_argument(
        "--profile",
        choices=("smoke", "standard", "capacity"),
        default="smoke",
    )
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    profile = _suite_profile(arguments.profile)
    ingestion = benchmark_ingestion_matrix(
        max_pages=arguments.max_pages,
        page_counts=profile["page_counts"],
        blocks_per_page=profile["blocks_per_page"],
        words_per_block=profile["words_per_block"],
    )
    concurrency_profile = SyntheticDocumentProfile(
        name="concurrent",
        page_count=profile["concurrent_pages"],
        blocks_per_page=profile["blocks_per_page"],
        words_per_block=profile["words_per_block"],
    )
    knowledge_base_profile = SyntheticDocumentProfile(
        name="knowledge_base",
        page_count=profile["knowledge_base_pages"],
        blocks_per_page=profile["blocks_per_page"],
        words_per_block=profile["words_per_block"],
    )
    report = {
        "schema_version": "capacity_benchmark_v1",
        "profile": arguments.profile,
        "ingestion": [asdict(result) for result in ingestion],
        "concurrent_ingestion": asdict(
            benchmark_concurrent_ingestion(
                concurrency_profile,
                job_count=profile["concurrent_jobs"],
                worker_count=profile["workers"],
            )
        ),
        "knowledge_base": asdict(
            benchmark_knowledge_base_capacity(
                knowledge_base_profile,
                document_count=profile["knowledge_base_documents"],
            )
        ),
        "retrieval": asdict(
            run_retrieval_benchmark(
                candidate_count=profile["retrieval_candidates"],
                document_count=profile["retrieval_documents"],
                warm_runs=profile["warm_runs"],
            )
        ),
        "database_query_latency": (
            "Use retrieval_decision observations for semantic_query_duration_ms "
            "and lexical_query_duration_ms on a representative PostgreSQL dataset."
        ),
    }
    serialized = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output is not None:
        arguments.output.write_text(f"{serialized}\n", encoding="utf-8")
    print(serialized)


def _suite_profile(name: str) -> dict[str, int | tuple[int, ...]]:
    if name == "capacity":
        return {
            "page_counts": (10, 50, 200),
            "blocks_per_page": 3,
            "words_per_block": 120,
            "concurrent_pages": 200,
            "concurrent_jobs": 8,
            "workers": 4,
            "knowledge_base_pages": 200,
            "knowledge_base_documents": 50,
            "retrieval_candidates": 100,
            "retrieval_documents": 25,
            "warm_runs": 20,
        }
    if name == "standard":
        return {
            "page_counts": (10, 50, 200),
            "blocks_per_page": 2,
            "words_per_block": 50,
            "concurrent_pages": 50,
            "concurrent_jobs": 4,
            "workers": 2,
            "knowledge_base_pages": 50,
            "knowledge_base_documents": 10,
            "retrieval_candidates": 100,
            "retrieval_documents": 10,
            "warm_runs": 10,
        }
    return {
        "page_counts": (10,),
        "blocks_per_page": 1,
        "words_per_block": 20,
        "concurrent_pages": 10,
        "concurrent_jobs": 2,
        "workers": 2,
        "knowledge_base_pages": 10,
        "knowledge_base_documents": 2,
        "retrieval_candidates": 30,
        "retrieval_documents": 5,
        "warm_runs": 3,
    }


if __name__ == "__main__":
    main()
