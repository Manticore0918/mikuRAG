import pytest

from app.benchmarking import (
    SyntheticDocumentProfile,
    benchmark_concurrent_ingestion,
    benchmark_ingestion_matrix,
    benchmark_knowledge_base_capacity,
    benchmark_retrieval_pipeline,
)


def test_ingestion_matrix_covers_required_document_sizes_and_scales_linearly() -> None:
    results = benchmark_ingestion_matrix(
        max_pages=250,
        page_counts=(10, 50, 200),
        blocks_per_page=1,
        words_per_block=40,
    )

    assert [result.page_count for result in results] == [10, 50, 200, 250]
    assert all(result.parent_count > 0 for result in results)
    assert all(result.child_count == result.embedding_count for result in results)
    assert all(result.estimated_storage_bytes > result.source_bytes for result in results)
    assert all(result.memory_amplification < 40 for result in results)
    assert results[-1].memory_amplification < 15
    assert results[-1].peak_worker_memory_bytes < (
        results[1].peak_worker_memory_bytes * 8
    )


def test_concurrent_and_many_document_capacity_profiles_complete() -> None:
    profile = SyntheticDocumentProfile(
        name="capacity_test",
        page_count=10,
        blocks_per_page=1,
        words_per_block=20,
    )

    concurrent = benchmark_concurrent_ingestion(
        profile,
        job_count=4,
        worker_count=2,
    )
    knowledge_base = benchmark_knowledge_base_capacity(
        profile,
        document_count=5,
    )

    assert concurrent.job_count == 4
    assert concurrent.total_child_count > 0
    assert concurrent.documents_per_second > 0
    assert knowledge_base.total_pages == 50
    assert knowledge_base.total_embedding_count == knowledge_base.total_child_count
    assert knowledge_base.estimated_storage_bytes > 0


@pytest.mark.asyncio
async def test_cold_and_warm_retrieval_benchmark_tracks_prompt_growth() -> None:
    result = await benchmark_retrieval_pipeline(
        candidate_count=100,
        document_count=10,
        warm_runs=3,
    )

    assert result.candidate_count == 100
    assert result.cold_pipeline_ms > 0
    assert result.warm_mean_pipeline_ms > 0
    assert result.warm_p95_pipeline_ms >= result.warm_mean_pipeline_ms
    assert result.mean_reranker_ms > 0
    assert 0 < result.selected_evidence_count <= 8
    assert 0 < result.evidence_tokens <= 6_000
