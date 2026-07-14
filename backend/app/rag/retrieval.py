import uuid
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Chunk, Document, DocumentStatus


@dataclass
class Candidate:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    locator: dict[str, Any]
    text: str
    semantic_similarity: float | None = None
    lexical_score: float | None = None
    fused_score: float = 0.0


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    locator: dict[str, Any]
    text: str
    retrieval_rank: int
    retrieval_score: float
    semantic_similarity: float | None
    lexical_score: float | None


def fuse_rankings(
    semantic: list[Candidate],
    lexical: list[Candidate],
    *,
    rrf_k: int,
    limit: int,
    max_per_document: int,
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
    selected: list[Candidate] = []
    document_counts: dict[uuid.UUID, int] = {}
    for candidate in ranked:
        count = document_counts.get(candidate.document_id, 0)
        if count >= max_per_document:
            continue
        selected.append(candidate)
        document_counts[candidate.document_id] = count + 1
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
) -> tuple[list[Evidence], bool]:
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    common_filters = (
        Document.knowledge_base_id == knowledge_base_id,
        Document.status == DocumentStatus.READY,
        Chunk.embedding_model == settings.embedding_model_id,
    )
    semantic_result = await session.execute(
        select(Chunk, Document, distance)
        .join(Document, Document.id == Chunk.document_id)
        .where(*common_filters, Chunk.embedding.is_not(None))
        .order_by(distance)
        .limit(settings.retrieval_semantic_candidates)
    )
    semantic = [
        Candidate(
            chunk_id=chunk.id,
            document_id=document.id,
            document_name=document.original_name,
            locator=chunk.locator,
            text=chunk.text,
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
        .limit(settings.retrieval_lexical_candidates)
    )
    lexical = [
        Candidate(
            chunk_id=chunk.id,
            document_id=document.id,
            document_name=document.original_name,
            locator=chunk.locator,
            text=chunk.text,
            lexical_score=float(rank_value),
        )
        for chunk, document, rank_value in lexical_result.all()
    ]

    selected = fuse_rankings(
        semantic,
        lexical,
        rrf_k=settings.retrieval_rrf_k,
        limit=settings.retrieval_evidence_limit,
        max_per_document=settings.retrieval_max_chunks_per_document,
    )
    evidence = [
        Evidence(
            evidence_id=f"E{rank}",
            chunk_id=candidate.chunk_id,
            document_id=candidate.document_id,
            document_name=candidate.document_name,
            locator=candidate.locator,
            text=candidate.text,
            retrieval_rank=rank,
            retrieval_score=candidate.fused_score,
            semantic_similarity=candidate.semantic_similarity,
            lexical_score=candidate.lexical_score,
        )
        for rank, candidate in enumerate(selected, start=1)
    ]
    return evidence, is_sufficient(selected, settings)
