import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.ingestion.tokenization import Tokenizer
from app.models import Chunk, ChunkLevel, Document, DocumentStatus
from app.observability import emit_observation
from app.rag.evidence_assembly import (
    apply_adaptive_diversity,
    assemble_evidence_candidates,
    suppress_duplicates,
)
from app.rag.reranking import DeterministicReranker
from app.rag.retrieval import _candidate_from_row, fuse_rankings
from app.rag.retrieval_types import Candidate

logger = logging.getLogger(__name__)

_WHOLE_DOCUMENT = re.compile(
    r"\b(?:entire|whole) (?:document|documents|knowledge base)\b"
    r"|\b(?:summari[sz]e|summary|overview)(?: of)? "
    r"(?:the |this |all )?(?:document|documents|knowledge base)\b"
    r"|\ball documents?\b|\beverything\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SummaryContext:
    summary_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    chunk_level: str
    heading_path: list[str]
    start_page: int | None
    end_page: int | None
    text: str
    source_parent_id: str | None
    source_content_hash: str | None
    summary_model: str | None
    prompt_version: str | None
    score: float


async def retrieve_summary_context(
    session: AsyncSession,
    knowledge_base_id: uuid.UUID,
    query_text: str,
    query_vector: list[float],
    settings: Settings,
    tokenizer: Tokenizer,
) -> list[SummaryContext]:
    candidate_started = perf_counter()
    semantic, lexical = await _summary_candidates(
        session,
        knowledge_base_id,
        query_text,
        query_vector,
        settings,
    )
    candidate_duration_ms = (perf_counter() - candidate_started) * 1_000
    fused = fuse_rankings(
        semantic,
        lexical,
        rrf_k=settings.retrieval_rrf_k,
        limit=min(100, settings.summary_retrieval_limit * 3),
        max_per_document=None,
    )
    if _WHOLE_DOCUMENT.search(query_text):
        document_summaries = [
            candidate
            for candidate in fused
            if candidate.chunk_level == ChunkLevel.DOCUMENT_SUMMARY
        ]
        if document_summaries:
            fused = document_summaries
    rerank_started = perf_counter()
    reranked = await DeterministicReranker().rerank(query_text, fused)
    reranking_duration_ms = (perf_counter() - rerank_started) * 1_000
    deduplicated = suppress_duplicates(reranked)
    diverse = apply_adaptive_diversity(
        deduplicated,
        limit=settings.summary_retrieval_limit,
        document_penalty=settings.retrieval_document_diversity_penalty,
    )
    selected, drops = assemble_evidence_candidates(
        diverse,
        tokenizer=tokenizer,
        max_items=settings.summary_retrieval_limit,
        token_budget=settings.summary_context_token_budget,
    )
    emit_observation(
        logger,
        "summary_retrieval_decision",
        knowledge_base_id=str(knowledge_base_id),
        semantic_candidate_count=len(semantic),
        lexical_candidate_count=len(lexical),
        fused_candidate_count=len(fused),
        selected_summary_count=len(selected),
        selected_document_count=len(
            {candidate.document_id for candidate in selected}
        ),
        selected_summary_levels=dict(
            Counter(str(candidate.chunk_level) for candidate in selected)
        ),
        candidate_generation_duration_ms=round(candidate_duration_ms, 2),
        reranking_duration_ms=round(reranking_duration_ms, 2),
        summary_context_token_count=sum(
            candidate.token_count or tokenizer.count(candidate.text)
            for candidate in selected
        ),
        drop_counts=dict(drops),
    )
    return [_to_summary_context(candidate) for candidate in selected]


async def _summary_candidates(
    session: AsyncSession,
    knowledge_base_id: uuid.UUID,
    query_text: str,
    query_vector: list[float],
    settings: Settings,
) -> tuple[list[Candidate], list[Candidate]]:
    levels = [ChunkLevel.SECTION_SUMMARY, ChunkLevel.DOCUMENT_SUMMARY]
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    common_filters = (
        Document.knowledge_base_id == knowledge_base_id,
        Document.status == DocumentStatus.READY,
        Chunk.chunk_level.in_(levels),
        Chunk.embedding_model == settings.embedding_model_id,
    )
    semantic_result = await session.execute(
        select(Chunk, Document, distance)
        .join(Document, Document.id == Chunk.document_id)
        .where(*common_filters, Chunk.embedding.is_not(None))
        .order_by(distance)
        .limit(min(100, settings.summary_retrieval_limit * 4))
    )
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
    lexical_result = await session.execute(
        select(Chunk, Document, lexical_rank)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            *common_filters,
            Chunk.search_vector.is_not(None),
            Chunk.search_vector.op("@@")(search_query),
        )
        .order_by(lexical_rank.desc())
        .limit(min(100, settings.summary_retrieval_limit * 4))
    )
    lexical = [
        _candidate_from_row(chunk, document, lexical_score=float(rank_value))
        for chunk, document, rank_value in lexical_result.all()
    ]
    return semantic, lexical


def _to_summary_context(candidate: Candidate) -> SummaryContext:
    locator: dict[str, Any] = candidate.locator
    return SummaryContext(
        summary_id=candidate.chunk_id,
        document_id=candidate.document_id,
        document_name=candidate.document_name,
        chunk_level=str(candidate.chunk_level),
        heading_path=candidate.heading_path,
        start_page=candidate.start_page,
        end_page=candidate.end_page,
        text=candidate.text,
        source_parent_id=_optional_string(locator.get("source_parent_id")),
        source_content_hash=_optional_string(locator.get("source_content_hash")),
        summary_model=_optional_string(locator.get("summary_model")),
        prompt_version=_optional_string(locator.get("summary_prompt_version")),
        score=candidate.effective_score,
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None
