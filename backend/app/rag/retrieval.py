import logging
import uuid
from collections import Counter
from dataclasses import replace
from time import perf_counter

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.ingestion.tokenization import Tokenizer, create_tokenizer
from app.models import Chunk, ChunkLevel, Document, DocumentStatus, KnowledgeBase
from app.observability import emit_observation
from app.rag.cache import DerivedDataCache, cache_key, get_derived_cache
from app.rag.citations import public_locator
from app.rag.evidence_assembly import (
    apply_adaptive_diversity,
    assemble_evidence_candidates,
    expansion_direction,
    merge_adjacent_candidates,
    suppress_duplicates,
)
from app.rag.fusion import RrfFusionStrategy
from app.rag.reranking import DeterministicReranker, Reranker, build_default_reranker
from app.rag.retrieval_types import (
    Candidate,
    Evidence,
    QueryPlan,
    RetrievalFilters,
    RetrievalMetrics,
    RetrievalMode,
)
from app.rag.retrievers import (
    Bm25UnavailableError,
    LexicalRetriever,
    PgSearchBM25LexicalRetriever,
    PgVectorRetriever,
    PostgresFTSLexicalRetriever,
    _candidate_from_row,
    filters_sql,
    is_bm25_available,
)

logger = logging.getLogger(__name__)


def fuse_rankings(
    semantic: list[Candidate],
    lexical: list[Candidate],
    *,
    rrf_k: int,
    limit: int,
    max_per_document: int | None,
) -> list[Candidate]:
    """Compatibility wrapper over the weighted RRF strategy (weights default to 1)."""
    return RrfFusionStrategy().fuse(
        semantic,
        lexical,
        rrf_k=rrf_k,
        semantic_weight=1.0,
        lexical_weight=1.0,
        limit=limit,
        max_per_document=max_per_document,
    )


def is_sufficient(candidates: list[Candidate], settings: Settings) -> bool:
    return any(
        (
            candidate.semantic_similarity is not None
            and candidate.semantic_similarity
            >= settings.retrieval_min_semantic_similarity
        )
        or (
            candidate.lexical_score is not None
            and candidate.lexical_score >= settings.retrieval_min_lexical_score
        )
        for candidate in candidates
    )


def _coerce_mode(mode: RetrievalMode | str) -> RetrievalMode:
    if isinstance(mode, RetrievalMode):
        return mode
    return RetrievalMode(mode)


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
    mode: RetrievalMode | str | None = None,
    filters: RetrievalFilters | None = None,
    query_plan: QueryPlan | None = None,
    cache: DerivedDataCache | None = None,
    index_generation: int | None = None,
) -> tuple[list[Evidence], bool]:
    """Retrieve ranked Evidence for a Knowledge Base under the active retrieval mode.

    `mode` selects which legs run (`vector`, `fts_baseline`, `bm25`,
    `hybrid_rrf`, `hybrid_rrf_reranked`); when omitted it falls back to
    `settings.retrieval_mode`. `filters` are pushed into the retrieval SQL before
    candidate limits. The authorization scope (knowledge base membership, Ready
    status, embedding model) is always applied separately and first.
    """
    retrieval_started = perf_counter()
    active_metrics = metrics or RetrievalMetrics()
    active_mode = _coerce_mode(mode or settings.retrieval_mode)
    active_filters = filters
    if (active_filters is None or active_filters.is_empty()) and query_plan is not None:
        active_filters = query_plan.inferred_filters
    active_filters = active_filters or RetrievalFilters()
    active_metrics.retrieval_mode = active_mode.value
    active_metrics.filters_applied = not active_filters.is_empty()
    if query_plan is not None:
        active_metrics.rewrite_status = query_plan.status.value

    active_cache = cache or get_derived_cache(settings)
    retrieval_cache_key: str | None = None
    if (
        active_cache is not None
        and settings.retrieval_cache_enabled
        and not settings.hierarchical_retrieval_enabled
    ):
        generation = index_generation
        if generation is None:
            generation = await session.scalar(
                select(KnowledgeBase.index_generation).where(
                    KnowledgeBase.id == knowledge_base_id
                )
            )
        if generation is not None:
            retrieval_cache_key = cache_key(
                "retrieval-result",
                knowledge_base_id=knowledge_base_id,
                index_generation=int(generation),
                query_text=query_text,
                filters=active_filters,
                settings=settings,
                mode=active_mode,
            )
            cached, cache_status = await active_cache.get_json(retrieval_cache_key)
            active_metrics.retrieval_cache_status = cache_status
            if cache_status == "hit":
                cached_result = await _load_cached_candidates(
                    session,
                    knowledge_base_id,
                    active_filters,
                    cached,
                )
                if cached_result is not None:
                    selected, sufficient = cached_result
                    active_tokenizer = tokenizer or create_tokenizer(settings.chunk_tokenizer)
                    active_metrics.fused_candidate_count = len(selected)
                    active_metrics.reranked_candidate_count = len(selected)
                    active_metrics.evidence_token_count = _evidence_token_count(
                        selected, active_tokenizer
                    )
                    active_metrics.retrieval_duration_ms = (
                        perf_counter() - retrieval_started
                    ) * 1_000
                    _emit_retrieval_observation(
                        knowledge_base_id,
                        active_mode.value,
                        selected,
                        sufficient,
                        active_metrics,
                    )
                    return _to_evidence(selected), sufficient
                active_metrics.retrieval_cache_status = "invalid"

    candidate_started = perf_counter()
    semantic, lexical, lexical_kind = await _generate_candidates(
        session,
        knowledge_base_id,
        query_text,
        query_vector,
        settings,
        mode=active_mode,
        filters=active_filters,
        metrics=active_metrics,
    )
    active_metrics.candidate_generation_ms = (
        perf_counter() - candidate_started
    ) * 1_000
    active_metrics.semantic_candidate_count = len(semantic)
    active_metrics.lexical_candidate_count = len(lexical)
    active_metrics.lexical_kind = lexical_kind

    reranked_mode = active_mode == RetrievalMode.HYBRID_RRF_RERANKED
    hierarchical = settings.hierarchical_retrieval_enabled
    fusion_started = perf_counter()
    fused = RrfFusionStrategy().fuse(
        semantic,
        lexical,
        rrf_k=settings.retrieval_rrf_k,
        semantic_weight=settings.retrieval_rrf_semantic_weight,
        lexical_weight=settings.retrieval_rrf_lexical_weight,
        limit=(
            settings.retrieval_rerank_candidates
            if reranked_mode or hierarchical
            else settings.retrieval_evidence_limit
        ),
        max_per_document=(
            None if reranked_mode or hierarchical else settings.retrieval_max_chunks_per_document
        ),
    )
    active_metrics.fusion_ms = (perf_counter() - fusion_started) * 1_000
    active_metrics.fused_candidate_count = len(fused)

    if reranked_mode:
        active_reranker = reranker or build_default_reranker(settings)
        rerank_started = perf_counter()
        try:
            reranked = await active_reranker.rerank(query_text, fused)
            selected = reranked[: settings.retrieval_evidence_limit]
        except Exception as error:
            logger.warning("Reranker failed (%s); using fused order", error)
            selected = fused[: settings.retrieval_evidence_limit]
            active_metrics.reranker_provider = "fallback_fused_order"
        else:
            active_metrics.reranker_provider = getattr(
                active_reranker, "provider_name", "unknown"
            )
            active_metrics.reranker_model = getattr(
                active_reranker, "model_name", None
            )
            active_metrics.reranker_version = getattr(
                active_reranker, "version", None
            )
        active_metrics.reranking_ms = (perf_counter() - rerank_started) * 1_000
        active_metrics.reranker_latency_ms = active_metrics.reranking_ms
        active_metrics.reranked_candidate_count = len(selected)
    else:
        selected = fused
        active_metrics.reranked_candidate_count = len(fused)

    if not hierarchical:
        active_tokenizer = tokenizer or create_tokenizer(settings.chunk_tokenizer)
        active_metrics.evidence_token_count = _evidence_token_count(
            selected, active_tokenizer
        )
        active_metrics.drop_counts = _legacy_drop_counts(semantic, lexical, selected)
        sufficient = is_sufficient(selected, settings)
        await _write_retrieval_cache(
            active_cache,
            retrieval_cache_key,
            selected,
            sufficient,
            active_metrics,
        )
        active_metrics.retrieval_duration_ms = (
            perf_counter() - retrieval_started
        ) * 1_000
        _emit_retrieval_observation(
            knowledge_base_id,
            active_mode.value,
            selected,
            sufficient,
            active_metrics,
        )
        return _to_evidence(selected), sufficient

    expansion_started = perf_counter()
    active_tokenizer = tokenizer or create_tokenizer(settings.chunk_tokenizer)
    if not reranked_mode:
        # The hierarchical pipeline always begins with a deterministic rerank
        # for ranking stability when no configured reranker ran.
        rerank_started = perf_counter()
        selected = await DeterministicReranker().rerank(query_text, fused)
        active_metrics.reranking_ms = (perf_counter() - rerank_started) * 1_000
        active_metrics.reranker_latency_ms = active_metrics.reranking_ms
        active_metrics.reranker_provider = "deterministic"
    deduplicated = suppress_duplicates(selected)
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
        "rerank_duplication": len(selected) - len(deduplicated),
        "diversity_or_seed_limit": len(deduplicated) - len(seeds),
    }
    active_metrics.evidence_token_count = _evidence_token_count(
        selected, active_tokenizer
    )
    sufficient = is_sufficient(selected, settings)
    active_metrics.expansion_ms = (perf_counter() - expansion_started) * 1_000
    active_metrics.retrieval_duration_ms = (
        perf_counter() - retrieval_started
    ) * 1_000
    _emit_retrieval_observation(
        knowledge_base_id,
        active_mode.value,
        selected,
        sufficient,
        active_metrics,
    )
    return _to_evidence(selected), sufficient


async def _write_retrieval_cache(
    cache: DerivedDataCache | None,
    key: str | None,
    selected: list[Candidate],
    sufficient: bool,
    metrics: RetrievalMetrics,
) -> None:
    if cache is None or key is None:
        return
    status = await cache.set_json(
        key,
        {
            "sufficient": sufficient,
            "candidates": [
                {
                    "chunk_id": str(candidate.chunk_id),
                    "semantic_similarity": candidate.semantic_similarity,
                    "lexical_score": candidate.lexical_score,
                    "retrieval_score": candidate.effective_score,
                }
                for candidate in selected
            ],
        },
    )
    if status in {"error", "oversize"}:
        metrics.retrieval_cache_status = status
    elif metrics.retrieval_cache_status in {"disabled", "invalid"}:
        metrics.retrieval_cache_status = "miss"


async def _load_cached_candidates(
    session: AsyncSession,
    knowledge_base_id: uuid.UUID,
    filters: RetrievalFilters,
    payload: object,
) -> tuple[list[Candidate], bool] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("sufficient"), bool):
        return None
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list) or len(raw_candidates) > 100:
        return None
    records: list[tuple[uuid.UUID, dict[str, object]]] = []
    try:
        for item in raw_candidates:
            if not isinstance(item, dict):
                return None
            records.append((uuid.UUID(str(item["chunk_id"])), item))
    except (KeyError, TypeError, ValueError):
        return None
    chunk_ids = [chunk_id for chunk_id, _ in records]
    if len(set(chunk_ids)) != len(chunk_ids):
        return None
    result = await session.execute(
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == DocumentStatus.READY,
            Chunk.id.in_(chunk_ids),
            *filters_sql(filters),
        )
    )
    rows = {chunk.id: (chunk, document) for chunk, document in result.all()}
    if set(rows) != set(chunk_ids):
        return None
    selected: list[Candidate] = []
    try:
        for chunk_id, item in records:
            chunk, document = rows[chunk_id]
            score = float(item["retrieval_score"])
            semantic = item.get("semantic_similarity")
            lexical = item.get("lexical_score")
            selected.append(
                replace(
                    _candidate_from_row(chunk, document),
                    semantic_similarity=(float(semantic) if semantic is not None else None),
                    lexical_score=(float(lexical) if lexical is not None else None),
                    fused_score=score,
                )
            )
    except (KeyError, TypeError, ValueError):
        return None
    return selected, bool(payload["sufficient"])


async def _generate_candidates(
    session: AsyncSession,
    knowledge_base_id: uuid.UUID,
    query_text: str,
    query_vector: list[float],
    settings: Settings,
    *,
    mode: RetrievalMode,
    filters: RetrievalFilters,
    metrics: RetrievalMetrics | None = None,
) -> tuple[list[Candidate], list[Candidate], str | None]:
    """Run the legs selected by the active mode and return (semantic, lexical, lexical_kind)."""
    semantic: list[Candidate] = []
    lexical: list[Candidate] = []
    lexical_kind: str | None = None
    if mode in (
        RetrievalMode.VECTOR,
        RetrievalMode.HYBRID_RRF,
        RetrievalMode.HYBRID_RRF_RERANKED,
    ):
        semantic_started = perf_counter()
        semantic = await PgVectorRetriever().retrieve(
            session,
            knowledge_base_id,
            query_text,
            query_vector,
            filters=filters,
            chunk_levels=(ChunkLevel.CHILD,),
            limit=settings.retrieval_semantic_candidates,
            settings=settings,
        )
        if metrics is not None:
            metrics.semantic_query_ms = (perf_counter() - semantic_started) * 1_000
    if mode in (
        RetrievalMode.FTS_BASELINE,
        RetrievalMode.BM25,
        RetrievalMode.HYBRID_RRF,
        RetrievalMode.HYBRID_RRF_RERANKED,
    ):
        lexical_retriever, lexical_kind = await _resolve_lexical(
            session, mode, settings, metrics
        )
        if lexical_retriever is not None:
            lexical_started = perf_counter()
            try:
                lexical = await lexical_retriever.retrieve(
                    session,
                    knowledge_base_id,
                    query_text,
                    filters=filters,
                    chunk_levels=(ChunkLevel.CHILD,),
                    limit=settings.retrieval_lexical_candidates,
                    settings=settings,
                )
            except Bm25UnavailableError:
                if not settings.bm25_fallback_to_fts:
                    raise
                logger.warning("BM25 execution failed; retrying with PostgreSQL FTS")
                lexical = await PostgresFTSLexicalRetriever().retrieve(
                    session,
                    knowledge_base_id,
                    query_text,
                    filters=filters,
                    chunk_levels=(ChunkLevel.CHILD,),
                    limit=settings.retrieval_lexical_candidates,
                    settings=settings,
                )
                lexical_kind = "fts_fallback"
            if metrics is not None:
                metrics.lexical_query_ms = (perf_counter() - lexical_started) * 1_000
    return semantic, lexical, lexical_kind


async def _resolve_lexical(
    session: AsyncSession,
    mode: RetrievalMode,
    settings: Settings,
    metrics: RetrievalMetrics | None,
) -> tuple[LexicalRetriever | None, str | None]:
    """Resolve the lexical retriever for a mode, falling back to FTS when needed.

    Explicit `bm25` mode uses BM25 when available and otherwise falls back to FTS
    (unless `bm25_fallback_to_fts` is disabled). Hybrid modes keep today's FTS
    baseline until `bm25_hybrid_enabled` is turned on after the evaluation gate
    passes, per the delivery rule that new retrieval behavior is off by default.
    """
    if mode == RetrievalMode.FTS_BASELINE:
        return PostgresFTSLexicalRetriever(), "fts"
    if mode == RetrievalMode.BM25:
        if await is_bm25_available(session):
            if metrics is not None:
                metrics.bm25_index_available = True
            return PgSearchBM25LexicalRetriever(), "bm25"
        if metrics is not None:
            metrics.bm25_index_available = False
        if not settings.bm25_fallback_to_fts:
            logger.warning(
                "pg_search BM25 is unavailable and fallback is disabled for bm25 mode"
            )
            return None, "bm25_unavailable"
        logger.warning(
            "pg_search BM25 unavailable; using PostgreSQL FTS for bm25 mode"
        )
        return PostgresFTSLexicalRetriever(), "fts_fallback"
    if settings.bm25_hybrid_enabled and await is_bm25_available(session):
        if metrics is not None:
            metrics.bm25_index_available = True
        return PgSearchBM25LexicalRetriever(), "bm25"
    return PostgresFTSLexicalRetriever(), "fts"


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


def _legacy_drop_counts(
    semantic: list[Candidate],
    lexical: list[Candidate],
    selected: list[Candidate],
) -> dict[str, int]:
    return {
        "legacy_cap_or_limit": max(
            0,
            len({candidate.chunk_id for candidate in [*semantic, *lexical]})
            - len(selected),
        )
    }


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
        rewrite_status=metrics.rewrite_status,
        rewrite_latency_ms=round(metrics.rewrite_latency_ms, 2),
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
        fusion_duration_ms=round(metrics.fusion_ms, 2),
        expansion_duration_ms=round(metrics.expansion_ms, 2),
        query_embedding_cache_status=metrics.query_embedding_cache_status,
        retrieval_cache_status=metrics.retrieval_cache_status,
        neighbor_expansion_count=metrics.neighbor_expansion_count,
        parent_promotion_count=metrics.parent_promotion_count,
        evidence_token_count=metrics.evidence_token_count,
        drop_counts=metrics.drop_counts,
    )


def _maximum_optional(*values):
    present = [value for value in values if value is not None]
    return max(present) if present else None
