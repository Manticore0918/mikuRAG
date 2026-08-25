import logging
import uuid
from collections import Counter
from dataclasses import replace
from time import perf_counter

from sqlalchemy import and_, func, literal_column, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.ingestion.tokenization import Tokenizer, create_tokenizer
from app.models import Chunk, ChunkLevel, Document, DocumentStatus
from app.observability import emit_observation
from app.rag.citations import public_locator
from app.rag.evidence_assembly import (
    apply_adaptive_diversity,
    assemble_evidence_candidates,
    expansion_direction,
    merge_adjacent_candidates,
    suppress_duplicates,
)
from app.rag.reranking import DeterministicReranker, Reranker
from app.rag.retrieval_types import Candidate, Evidence, RetrievalMetrics

logger = logging.getLogger(__name__)


def fuse_rankings(
    semantic: list[Candidate],
    lexical: list[Candidate],
    *,
    rrf_k: int,
    limit: int,
    max_per_document: int | None,
) -> list[Candidate]:
    combined: dict[uuid.UUID, Candidate] = {}
    for rank, candidate in enumerate(semantic, start=1):
        current = combined.setdefault(candidate.chunk_id, replace(candidate))
        current.semantic_similarity = candidate.semantic_similarity
        current.fused_score += 1 / (rrf_k + rank)
    for rank, candidate in enumerate(lexical, start=1):
        current = combined.setdefault(candidate.chunk_id, replace(candidate))
        current.lexical_score = candidate.lexical_score
        current.fused_score += 1 / (rrf_k + rank)

    ranked = sorted(
        combined.values(),
        key=lambda item: (
            item.fused_score,
            item.semantic_similarity if item.semantic_similarity is not None else -2.0,
            item.lexical_score if item.lexical_score is not None else -1.0,
        ),
        reverse=True,
    )
    if max_per_document is None:
        return ranked[:limit]

    selected: list[Candidate] = []
    document_counts: Counter[uuid.UUID] = Counter()
    for candidate in ranked:
        if document_counts[candidate.document_id] >= max_per_document:
            continue
        selected.append(candidate)
        document_counts[candidate.document_id] += 1
        if len(selected) == limit:
            break
    return selected


def is_sufficient(candidates: list[Candidate], settings: Settings) -> bool:
    return any(
        (
            candidate.semantic_similarity is not None
            and candidate.semantic_similarity >= settings.retrieval_min_semantic_similarity
        )
        or (
            candidate.lexical_score is not None
            and candidate.lexical_score >= settings.retrieval_min_lexical_score
        )
        for candidate in candidates
    )


async def retrieve_evidence(
    session: AsyncSession,
    knowledge_base_id: uuid.UUID,
    query_text: str,
    query_vector: list[float],
    settings: Settings,
    *,
    reranker: Reranker | None = None,
    tokenizer: Tokenizer | None = None,
    metrics: RetrievalMetrics | None = None,
) -> tuple[list[Evidence], bool]:
    retrieval_started = perf_counter()
    active_metrics = metrics or RetrievalMetrics()
    candidate_started = perf_counter()
    semantic, lexical = await _generate_candidates(
        session,
        knowledge_base_id,
        query_text,
        query_vector,
        settings,
        metrics=active_metrics,
    )
    active_metrics.candidate_generation_ms = (
        perf_counter() - candidate_started
    ) * 1_000
    active_metrics.semantic_candidate_count = len(semantic)
    active_metrics.lexical_candidate_count = len(lexical)
    if not settings.hierarchical_retrieval_enabled:
        selected = fuse_rankings(
            semantic,
            lexical,
            rrf_k=settings.retrieval_rrf_k,
            limit=settings.retrieval_evidence_limit,
            max_per_document=settings.retrieval_max_chunks_per_document,
        )
        active_tokenizer = tokenizer or create_tokenizer(settings.chunk_tokenizer)
        active_metrics.fused_candidate_count = len(selected)
        active_metrics.reranked_candidate_count = len(selected)
        active_metrics.evidence_token_count = _evidence_token_count(
            selected, active_tokenizer
        )
        active_metrics.drop_counts = {
            "legacy_cap_or_limit": max(
                0,
                len({candidate.chunk_id for candidate in [*semantic, *lexical]})
                - len(selected),
            )
        }
        sufficient = is_sufficient(selected, settings)
        active_metrics.retrieval_duration_ms = (
            perf_counter() - retrieval_started
        ) * 1_000
        _emit_retrieval_observation(
            knowledge_base_id,
            "legacy",
            selected,
            sufficient,
            active_metrics,
        )
        return _to_evidence(selected), sufficient

    active_tokenizer = tokenizer or create_tokenizer(settings.chunk_tokenizer)
    fused = fuse_rankings(
        semantic,
        lexical,
        rrf_k=settings.retrieval_rrf_k,
        limit=settings.retrieval_rerank_candidates,
        max_per_document=None,
    )
    active_metrics.fused_candidate_count = len(fused)
    active_reranker = reranker or DeterministicReranker()
    rerank_started = perf_counter()
    reranked = await active_reranker.rerank(query_text, fused)
    active_metrics.reranking_ms = (perf_counter() - rerank_started) * 1_000
    active_metrics.reranked_candidate_count = len(reranked)
    deduplicated = suppress_duplicates(reranked)
    seeds = apply_adaptive_diversity(
        deduplicated,
        limit=settings.retrieval_evidence_limit,
        document_penalty=settings.retrieval_document_diversity_penalty,
    )
    expanded = await _expand_context(
        session,
        knowledge_base_id,
        seeds,
        settings,
        active_tokenizer,
        metrics=active_metrics,
    )
    merged = merge_adjacent_candidates(
        expanded,
        tokenizer=active_tokenizer,
        max_tokens=settings.retrieval_max_merged_passage_tokens,
    )
    selected, drops = assemble_evidence_candidates(
        merged,
        tokenizer=active_tokenizer,
        max_items=settings.retrieval_evidence_limit,
        token_budget=settings.retrieval_evidence_token_budget,
    )
    active_metrics.drop_counts = {
        **dict(drops),
        "rerank_duplication": len(reranked) - len(deduplicated),
        "diversity_or_seed_limit": len(deduplicated) - len(seeds),
    }
    active_metrics.evidence_token_count = _evidence_token_count(
        selected, active_tokenizer
    )
    sufficient = is_sufficient(selected, settings)
    active_metrics.retrieval_duration_ms = (
        perf_counter() - retrieval_started
    ) * 1_000
    _emit_retrieval_observation(
        knowledge_base_id,
        "hierarchical",
        selected,
        sufficient,
        active_metrics,
    )
    return _to_evidence(selected), sufficient


async def _generate_candidates(
    session: AsyncSession,
    knowledge_base_id: uuid.UUID,
    query_text: str,
    query_vector: list[float],
    settings: Settings,
    *,
    metrics: RetrievalMetrics | None = None,
) -> tuple[list[Candidate], list[Candidate]]:
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    common_filters = (
        Document.knowledge_base_id == knowledge_base_id,
        Document.status == DocumentStatus.READY,
        Chunk.chunk_level == ChunkLevel.CHILD,
        Chunk.embedding_model == settings.embedding_model_id,
    )
    semantic_started = perf_counter()
    semantic_result = await session.execute(
        select(Chunk, Document, distance)
        .join(Document, Document.id == Chunk.document_id)
        .where(*common_filters, Chunk.embedding.is_not(None))
        .order_by(distance)
        .limit(settings.retrieval_semantic_candidates)
    )
    if metrics is not None:
        metrics.semantic_query_ms = (perf_counter() - semantic_started) * 1_000
    semantic = [
        _candidate_from_row(
            chunk,
            document,
            semantic_similarity=1.0 - float(distance_value),
        )
        for chunk, document, distance_value in semantic_result.all()
    ]

    search_query = func.websearch_to_tsquery(literal_column("'simple'"), query_text)
    lexical_rank = func.ts_rank_cd(Chunk.search_vector, search_query).label("lexical_rank")
    lexical_started = perf_counter()
    lexical_result = await session.execute(
        select(Chunk, Document, lexical_rank)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            *common_filters,
            Chunk.search_vector.is_not(None),
            Chunk.search_vector.op("@@")(search_query),
        )
        .order_by(lexical_rank.desc())
        .limit(settings.retrieval_lexical_candidates)
    )
    if metrics is not None:
        metrics.lexical_query_ms = (perf_counter() - lexical_started) * 1_000
    lexical = [
        _candidate_from_row(
            chunk,
            document,
            lexical_score=float(rank_value),
        )
        for chunk, document, rank_value in lexical_result.all()
    ]
    return semantic, lexical


async def _expand_context(
    session: AsyncSession,
    knowledge_base_id: uuid.UUID,
    seeds: list[Candidate],
    settings: Settings,
    tokenizer: Tokenizer,
    *,
    metrics: RetrievalMetrics | None = None,
) -> list[Candidate]:
    parent_counts = Counter(
        candidate.parent_chunk_id
        for candidate in seeds
        if candidate.parent_chunk_id is not None
    )
    requested_parent_ids = {
        parent_id for parent_id, count in parent_counts.items() if count >= 2
    }
    neighbor_requests: dict[tuple[uuid.UUID, int], Candidate] = {}
    for seed in seeds:
        if seed.parent_chunk_id is None or seed.parent_chunk_id in requested_parent_ids:
            continue
        direction = expansion_direction(seed)
        for distance in range(1, settings.retrieval_neighbor_expansion_count + 1):
            if direction:
                neighbor_requests[
                    (seed.parent_chunk_id, seed.ordinal + direction * distance)
                ] = seed

    conditions = []
    if requested_parent_ids:
        conditions.append(
            and_(
                Chunk.id.in_(requested_parent_ids),
                Chunk.chunk_level == ChunkLevel.PARENT,
            )
        )
    if neighbor_requests:
        conditions.append(
            and_(
                tuple_(Chunk.parent_chunk_id, Chunk.ordinal).in_(list(neighbor_requests)),
                Chunk.chunk_level == ChunkLevel.CHILD,
            )
        )
    if not conditions:
        return seeds

    result = await session.execute(
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == DocumentStatus.READY,
            or_(*conditions),
        )
    )
    rows = result.all()
    parent_rows = {
        chunk.id: (chunk, document)
        for chunk, document in rows
        if chunk.chunk_level == ChunkLevel.PARENT
    }
    promoted_parent_ids = {
        parent_id
        for parent_id, (chunk, _) in parent_rows.items()
        if (chunk.token_count or tokenizer.count(chunk.text))
        <= settings.retrieval_max_merged_passage_tokens
    }

    expanded: list[Candidate] = []
    for seed in seeds:
        if seed.parent_chunk_id not in promoted_parent_ids:
            expanded.append(seed)
    for parent_id in promoted_parent_ids:
        chunk, document = parent_rows[parent_id]
        matching = [seed for seed in seeds if seed.parent_chunk_id == parent_id]
        parent = _candidate_from_row(chunk, document)
        expanded.append(
            replace(
                parent,
                semantic_similarity=_maximum_optional(
                    *(candidate.semantic_similarity for candidate in matching)
                ),
                lexical_score=_maximum_optional(
                    *(candidate.lexical_score for candidate in matching)
                ),
                fused_score=max(candidate.fused_score for candidate in matching),
                rerank_score=max(candidate.effective_score for candidate in matching),
                source_chunk_ids=tuple(candidate.chunk_id for candidate in matching),
            )
        )
        if metrics is not None:
            metrics.parent_promotion_count += 1

    for chunk, document in rows:
        if chunk.chunk_level != ChunkLevel.CHILD or chunk.parent_chunk_id is None:
            continue
        seed = neighbor_requests.get((chunk.parent_chunk_id, chunk.ordinal))
        if seed is None or seed.parent_chunk_id in promoted_parent_ids:
            continue
        expanded.append(
            replace(
                _candidate_from_row(chunk, document),
                semantic_similarity=seed.semantic_similarity,
                lexical_score=seed.lexical_score,
                fused_score=seed.fused_score * 0.9,
                rerank_score=seed.effective_score * 0.9,
            )
        )
        if metrics is not None:
            metrics.neighbor_expansion_count += 1
    return expanded


def _candidate_from_row(
    chunk: Chunk,
    document: Document,
    *,
    semantic_similarity: float | None = None,
    lexical_score: float | None = None,
) -> Candidate:
    heading_path = (
        list(chunk.heading_path)
        if isinstance(chunk.heading_path, list)
        and all(isinstance(item, str) for item in chunk.heading_path)
        else []
    )
    return Candidate(
        chunk_id=chunk.id,
        document_id=document.id,
        document_name=document.original_name,
        locator=chunk.locator,
        text=chunk.text,
        parent_chunk_id=chunk.parent_chunk_id,
        ordinal=chunk.ordinal,
        chunk_level=chunk.chunk_level,
        start_page=chunk.start_page,
        end_page=chunk.end_page,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        heading_path=heading_path,
        content_type=chunk.content_type,
        token_count=chunk.token_count,
        content_hash=chunk.content_hash,
        chunking_version=chunk.chunking_version,
        semantic_similarity=semantic_similarity,
        lexical_score=lexical_score,
        source_chunk_ids=(chunk.id,),
    )


def _to_evidence(candidates: list[Candidate]) -> list[Evidence]:
    return [
        Evidence(
            evidence_id=f"E{rank}",
            chunk_id=candidate.chunk_id,
            document_id=candidate.document_id,
            document_name=candidate.document_name,
            locator=public_locator(
                candidate.locator,
                start_page=candidate.start_page,
                end_page=candidate.end_page,
                heading_path=candidate.heading_path,
            ),
            text=candidate.text,
            retrieval_rank=rank,
            retrieval_score=candidate.effective_score,
            semantic_similarity=candidate.semantic_similarity,
            lexical_score=candidate.lexical_score,
        )
        for rank, candidate in enumerate(candidates, start=1)
    ]


def _evidence_token_count(candidates: list[Candidate], tokenizer: Tokenizer) -> int:
    return sum(
        candidate.token_count
        if candidate.token_count is not None
        else tokenizer.count(candidate.text)
        for candidate in candidates
    )


def _emit_retrieval_observation(
    knowledge_base_id: uuid.UUID,
    mode: str,
    selected: list[Candidate],
    sufficient: bool,
    metrics: RetrievalMetrics,
) -> None:
    emit_observation(
        logger,
        "retrieval_decision",
        knowledge_base_id=str(knowledge_base_id),
        retrieval_mode=mode,
        sufficient=sufficient,
        semantic_candidate_count=metrics.semantic_candidate_count,
        lexical_candidate_count=metrics.lexical_candidate_count,
        fused_candidate_count=metrics.fused_candidate_count,
        reranked_candidate_count=metrics.reranked_candidate_count,
        selected_evidence_count=len(selected),
        selected_document_count=len({candidate.document_id for candidate in selected}),
        selected_chunking_versions=dict(
            Counter(candidate.chunking_version for candidate in selected)
        ),
        retrieval_duration_ms=round(metrics.retrieval_duration_ms, 2),
        candidate_generation_duration_ms=round(metrics.candidate_generation_ms, 2),
        semantic_query_duration_ms=round(metrics.semantic_query_ms, 2),
        lexical_query_duration_ms=round(metrics.lexical_query_ms, 2),
        reranking_duration_ms=round(metrics.reranking_ms, 2),
        neighbor_expansion_count=metrics.neighbor_expansion_count,
        parent_promotion_count=metrics.parent_promotion_count,
        evidence_token_count=metrics.evidence_token_count,
        drop_counts=metrics.drop_counts,
    )


def _maximum_optional(*values):
    present = [value for value in values if value is not None]
    return max(present) if present else None
