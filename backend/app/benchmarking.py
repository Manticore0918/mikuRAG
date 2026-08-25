import asyncio
import gc
import math
import tracemalloc
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from statistics import fmean
from time import perf_counter

from app.ingestion.contracts import ExtractedBlock, ExtractedDocument
from app.ingestion.hierarchical_chunking import (
    ConstructedHierarchy,
    HierarchicalChunkingConfig,
    construct_hierarchy,
)
from app.ingestion.normalization import normalize_document
from app.ingestion.tokenization import ConservativeTokenizer, Tokenizer
from app.rag.evidence_assembly import (
    apply_adaptive_diversity,
    assemble_evidence_candidates,
    merge_adjacent_candidates,
    suppress_duplicates,
)
from app.rag.reranking import DeterministicReranker
from app.rag.retrieval import fuse_rankings
from app.rag.retrieval_types import Candidate


@dataclass(frozen=True)
class SyntheticDocumentProfile:
    name: str
    page_count: int
    blocks_per_page: int = 2
    words_per_block: int = 80

    def __post_init__(self) -> None:
        if self.page_count <= 0:
            raise ValueError("page_count must be positive")
        if self.blocks_per_page <= 0:
            raise ValueError("blocks_per_page must be positive")
        if self.words_per_block <= 0:
            raise ValueError("words_per_block must be positive")


@dataclass(frozen=True)
class IngestionBenchmarkResult:
    profile_name: str
    page_count: int
    source_bytes: int
    extraction_ms: float
    normalization_ms: float
    chunk_construction_ms: float
    total_ms: float
    peak_worker_memory_bytes: int
    memory_amplification: float
    parent_count: int
    child_count: int
    embedding_count: int
    embedding_input_tokens: int
    estimated_storage_bytes: int


@dataclass(frozen=True)
class ConcurrentIngestionResult:
    profile_name: str
    job_count: int
    worker_count: int
    duration_ms: float
    documents_per_second: float
    total_parent_count: int
    total_child_count: int


@dataclass(frozen=True)
class KnowledgeBaseCapacityResult:
    profile_name: str
    document_count: int
    total_pages: int
    total_parent_count: int
    total_child_count: int
    total_embedding_count: int
    estimated_storage_bytes: int
    duration_ms: float


@dataclass(frozen=True)
class RetrievalBenchmarkResult:
    candidate_count: int
    document_count: int
    cold_pipeline_ms: float
    warm_mean_pipeline_ms: float
    warm_p95_pipeline_ms: float
    mean_reranker_ms: float
    selected_evidence_count: int
    evidence_tokens: int


def default_chunking_config() -> HierarchicalChunkingConfig:
    return HierarchicalChunkingConfig()


def build_synthetic_document(profile: SyntheticDocumentProfile) -> ExtractedDocument:
    blocks: list[ExtractedBlock] = []
    offset = 0
    for page in range(1, profile.page_count + 1):
        heading = f"Section {page}"
        blocks.append(
            ExtractedBlock(
                text=heading,
                block_type="heading",
                order=len(blocks),
                start_page=page,
                end_page=page,
                heading_level=1,
                heading_path=[heading],
                metadata={
                    "start_offset": offset,
                    "end_offset": offset + len(heading),
                },
            )
        )
        offset += len(heading) + 2
        for block_number in range(profile.blocks_per_page):
            marker = f"p{page}b{block_number}"
            text = " ".join(
                [marker]
                + [
                    f"requirement{word_number % 17}"
                    for word_number in range(profile.words_per_block - 1)
                ]
            )
            text = f"{text}."
            blocks.append(
                ExtractedBlock(
                    text=text,
                    block_type="paragraph",
                    order=len(blocks),
                    start_page=page,
                    end_page=page,
                    heading_path=[heading],
                    metadata={
                        "start_offset": offset,
                        "end_offset": offset + len(text),
                    },
                )
            )
            offset += len(text) + 2
    return ExtractedDocument(blocks=blocks, page_count=profile.page_count)


def benchmark_ingestion_profile(
    profile: SyntheticDocumentProfile,
    *,
    config: HierarchicalChunkingConfig | None = None,
    tokenizer: Tokenizer | None = None,
    embedding_dimensions: int = 768,
) -> IngestionBenchmarkResult:
    active_config = config or default_chunking_config()
    active_tokenizer = tokenizer or ConservativeTokenizer()
    gc.collect()
    tracemalloc.start()
    benchmark_started = perf_counter()
    try:
        phase_started = perf_counter()
        extracted = build_synthetic_document(profile)
        extraction_ms = _elapsed_ms(phase_started)
        source_bytes = sum(len(block.text.encode("utf-8")) for block in extracted.blocks)

        phase_started = perf_counter()
        normalized = normalize_document(extracted)
        normalization_ms = _elapsed_ms(phase_started)

        phase_started = perf_counter()
        hierarchy = construct_hierarchy(
            normalized,
            config=active_config,
            tokenizer=active_tokenizer,
        )
        chunk_construction_ms = _elapsed_ms(phase_started)
        _, peak_memory = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    return _ingestion_result(
        profile,
        hierarchy,
        source_bytes=source_bytes,
        extraction_ms=extraction_ms,
        normalization_ms=normalization_ms,
        chunk_construction_ms=chunk_construction_ms,
        total_ms=_elapsed_ms(benchmark_started),
        peak_memory=peak_memory,
        tokenizer=active_tokenizer,
        embedding_dimensions=embedding_dimensions,
    )


def benchmark_ingestion_matrix(
    *,
    max_pages: int,
    page_counts: Sequence[int] = (10, 50, 200),
    blocks_per_page: int = 2,
    words_per_block: int = 80,
    config: HierarchicalChunkingConfig | None = None,
) -> list[IngestionBenchmarkResult]:
    requested_pages = sorted({*page_counts, max_pages})
    if requested_pages[-1] > max_pages:
        raise ValueError("A benchmark page count exceeds max_pages")
    return [
        benchmark_ingestion_profile(
            SyntheticDocumentProfile(
                name=f"{page_count}_page",
                page_count=page_count,
                blocks_per_page=blocks_per_page,
                words_per_block=words_per_block,
            ),
            config=config,
        )
        for page_count in requested_pages
    ]


def benchmark_concurrent_ingestion(
    profile: SyntheticDocumentProfile,
    *,
    job_count: int,
    worker_count: int,
    config: HierarchicalChunkingConfig | None = None,
) -> ConcurrentIngestionResult:
    if job_count <= 0 or worker_count <= 0:
        raise ValueError("job_count and worker_count must be positive")
    active_config = config or default_chunking_config()
    started = perf_counter()
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        outputs = list(
            executor.map(
                lambda _: _construct_synthetic_hierarchy(profile, active_config),
                range(job_count),
            )
        )
    duration_ms = _elapsed_ms(started)
    return ConcurrentIngestionResult(
        profile_name=profile.name,
        job_count=job_count,
        worker_count=worker_count,
        duration_ms=duration_ms,
        documents_per_second=round(job_count / max(duration_ms / 1_000, 0.000_001), 3),
        total_parent_count=sum(len(output.parents) for output in outputs),
        total_child_count=sum(len(output.children) for output in outputs),
    )


def benchmark_knowledge_base_capacity(
    profile: SyntheticDocumentProfile,
    *,
    document_count: int,
    config: HierarchicalChunkingConfig | None = None,
    embedding_dimensions: int = 768,
) -> KnowledgeBaseCapacityResult:
    if document_count <= 0:
        raise ValueError("document_count must be positive")
    active_config = config or default_chunking_config()
    tokenizer = ConservativeTokenizer()
    total_parents = 0
    total_children = 0
    storage_bytes = 0
    started = perf_counter()
    for _ in range(document_count):
        hierarchy = _construct_synthetic_hierarchy(profile, active_config)
        total_parents += len(hierarchy.parents)
        total_children += len(hierarchy.children)
        storage_bytes += _estimated_storage_bytes(
            hierarchy,
            embedding_dimensions=embedding_dimensions,
        )
        # Count now so the benchmark includes the embedding-input traversal performed
        # by ingestion without allocating provider-sized vectors in worker memory.
        sum(
            tokenizer.count(child.embedding_text or child.text)
            for child in hierarchy.children
        )
    return KnowledgeBaseCapacityResult(
        profile_name=profile.name,
        document_count=document_count,
        total_pages=profile.page_count * document_count,
        total_parent_count=total_parents,
        total_child_count=total_children,
        total_embedding_count=total_children,
        estimated_storage_bytes=storage_bytes,
        duration_ms=_elapsed_ms(started),
    )


async def benchmark_retrieval_pipeline(
    *,
    candidate_count: int,
    document_count: int,
    warm_runs: int = 5,
    evidence_limit: int = 8,
    evidence_token_budget: int = 6_000,
) -> RetrievalBenchmarkResult:
    if candidate_count <= 0 or document_count <= 0 or warm_runs <= 0:
        raise ValueError("candidate_count, document_count, and warm_runs must be positive")
    tokenizer = ConservativeTokenizer()
    semantic, lexical = _synthetic_candidates(candidate_count, document_count)

    cold_started = perf_counter()
    selected, cold_reranker_ms = await _retrieval_pass(
        semantic,
        lexical,
        tokenizer=tokenizer,
        evidence_limit=evidence_limit,
        evidence_token_budget=evidence_token_budget,
    )
    cold_ms = _elapsed_ms(cold_started)

    warm_durations: list[float] = []
    reranker_durations = [cold_reranker_ms]
    for _ in range(warm_runs):
        warm_started = perf_counter()
        selected, reranker_ms = await _retrieval_pass(
            semantic,
            lexical,
            tokenizer=tokenizer,
            evidence_limit=evidence_limit,
            evidence_token_budget=evidence_token_budget,
        )
        warm_durations.append(_elapsed_ms(warm_started))
        reranker_durations.append(reranker_ms)
    return RetrievalBenchmarkResult(
        candidate_count=candidate_count,
        document_count=document_count,
        cold_pipeline_ms=cold_ms,
        warm_mean_pipeline_ms=round(fmean(warm_durations), 3),
        warm_p95_pipeline_ms=round(_percentile(warm_durations, 0.95), 3),
        mean_reranker_ms=round(fmean(reranker_durations), 3),
        selected_evidence_count=len(selected),
        evidence_tokens=sum(
            candidate.token_count or tokenizer.count(candidate.text)
            for candidate in selected
        ),
    )


def _construct_synthetic_hierarchy(
    profile: SyntheticDocumentProfile,
    config: HierarchicalChunkingConfig,
) -> ConstructedHierarchy:
    document = normalize_document(build_synthetic_document(profile))
    return construct_hierarchy(
        document,
        config=config,
        tokenizer=ConservativeTokenizer(),
    )


def _ingestion_result(
    profile: SyntheticDocumentProfile,
    hierarchy: ConstructedHierarchy,
    *,
    source_bytes: int,
    extraction_ms: float,
    normalization_ms: float,
    chunk_construction_ms: float,
    total_ms: float,
    peak_memory: int,
    tokenizer: Tokenizer,
    embedding_dimensions: int,
) -> IngestionBenchmarkResult:
    embedding_tokens = sum(
        tokenizer.count(child.embedding_text or child.text)
        for child in hierarchy.children
    )
    return IngestionBenchmarkResult(
        profile_name=profile.name,
        page_count=profile.page_count,
        source_bytes=source_bytes,
        extraction_ms=extraction_ms,
        normalization_ms=normalization_ms,
        chunk_construction_ms=chunk_construction_ms,
        total_ms=total_ms,
        peak_worker_memory_bytes=peak_memory,
        memory_amplification=round(peak_memory / max(source_bytes, 1), 3),
        parent_count=len(hierarchy.parents),
        child_count=len(hierarchy.children),
        embedding_count=len(hierarchy.children),
        embedding_input_tokens=embedding_tokens,
        estimated_storage_bytes=_estimated_storage_bytes(
            hierarchy,
            embedding_dimensions=embedding_dimensions,
        ),
    )


def _estimated_storage_bytes(
    hierarchy: ConstructedHierarchy,
    *,
    embedding_dimensions: int,
) -> int:
    text_bytes = sum(
        len(chunk.text.encode("utf-8"))
        for chunk in [*hierarchy.parents, *hierarchy.children]
    )
    embedding_bytes = len(hierarchy.children) * embedding_dimensions * 4
    metadata_overhead = (len(hierarchy.parents) + len(hierarchy.children)) * 512
    return text_bytes + embedding_bytes + metadata_overhead


def _synthetic_candidates(
    candidate_count: int,
    document_count: int,
) -> tuple[list[Candidate], list[Candidate]]:
    namespace = uuid.UUID("c3bd854d-0964-4f57-8cdf-ff32db6837bc")
    document_ids = [
        uuid.uuid5(namespace, f"document:{index}") for index in range(document_count)
    ]
    parent_ids = [
        uuid.uuid5(namespace, f"parent:{index}") for index in range(document_count)
    ]
    semantic: list[Candidate] = []
    for index in range(candidate_count):
        document_index = index % document_count
        text = (
            f"Approval control requirement {index} includes validation evidence and "
            "operational safeguards."
        )
        semantic.append(
            Candidate(
                chunk_id=uuid.uuid5(namespace, f"chunk:{index}"),
                document_id=document_ids[document_index],
                document_name=f"document-{document_index}.pdf",
                locator={"page": index + 1},
                text=text,
                parent_chunk_id=parent_ids[document_index],
                ordinal=index // document_count,
                start_page=index + 1,
                end_page=index + 1,
                start_offset=index * 100,
                end_offset=index * 100 + len(text),
                heading_path=["Controls", f"Requirement {index}"],
                token_count=18,
                content_hash=f"{index:064x}",
                chunking_version="hierarchical_v1",
                semantic_similarity=max(0.0, 0.95 - index / max(candidate_count, 1)),
            )
        )
    lexical = [
        Candidate(
            **{
                **candidate.__dict__,
                "semantic_similarity": None,
                "lexical_score": 1 / (index + 1),
            }
        )
        for index, candidate in enumerate(reversed(semantic))
    ]
    return semantic, lexical


async def _retrieval_pass(
    semantic: list[Candidate],
    lexical: list[Candidate],
    *,
    tokenizer: Tokenizer,
    evidence_limit: int,
    evidence_token_budget: int,
) -> tuple[list[Candidate], float]:
    fused = fuse_rankings(
        semantic,
        lexical,
        rrf_k=60,
        limit=min(100, len(semantic)),
        max_per_document=None,
    )
    reranker_started = perf_counter()
    reranked = await DeterministicReranker().rerank(
        "approval control requirements",
        fused,
    )
    reranker_ms = _elapsed_ms(reranker_started)
    diverse = apply_adaptive_diversity(
        suppress_duplicates(reranked),
        limit=evidence_limit * 2,
        document_penalty=0.2,
    )
    merged = merge_adjacent_candidates(
        diverse,
        tokenizer=tokenizer,
        max_tokens=1_500,
    )
    selected, _ = assemble_evidence_candidates(
        merged,
        tokenizer=tokenizer,
        max_items=evidence_limit,
        token_budget=evidence_token_budget,
    )
    return selected, reranker_ms


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1_000, 3)


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def run_retrieval_benchmark(**kwargs) -> RetrievalBenchmarkResult:
    return asyncio.run(benchmark_retrieval_pipeline(**kwargs))
