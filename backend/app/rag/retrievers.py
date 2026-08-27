"""Explicit retrieval boundaries: vector and lexical retrievers.

The checkpoint-3 refactor separates candidate production into a `VectorRetriever`
and a `LexicalRetriever` behind Protocol boundaries, pushes user-selectable
`RetrievalFilters` into the SQL before candidate limits are applied, and keeps the
mandatory authorization scope (knowledge base membership, Ready status, and the
embedding model) separate from those filters.
"""

import logging
import uuid
from typing import Any, Protocol

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Chunk, ChunkLevel, Document, DocumentStatus
from app.rag.retrieval_types import Candidate, RetrievalFilters, RetrievalMetrics

logger = logging.getLogger(__name__)

BM25_INDEX_NAME = "chunks_search_bm25"


class Bm25UnavailableError(RuntimeError):
    """Raised when a BM25 query cannot run and the caller must fall back to FTS."""


def filters_sql(filters: RetrievalFilters | None) -> tuple[Any, ...]:
    """Return SQLAlchemy predicates for the user-selectable metadata filters.

    These predicates are pushed into the WHERE clause before any candidate limit,
    so filtered-out Documents never consume candidate slots. The authorization
    scope is applied separately and always precedes these predicates.
    """
    if filters is None or filters.is_empty():
        return ()
    predicates: list[Any] = []
    if filters.document_ids:
        predicates.append(Document.id.in_(filters.document_ids))
    if filters.tags:
        predicates.append(Document.tags.contains(list(filters.tags)))
    if filters.source_kinds:
        predicates.append(Document.source_kind.in_(filters.source_kinds))
    if filters.languages:
        predicates.append(Document.language.in_(filters.languages))
    if filters.ingested_after is not None:
        predicates.append(Document.created_at >= filters.ingested_after)
    if filters.ingested_before is not None:
        predicates.append(Document.created_at <= filters.ingested_before)
    return tuple(predicates)


def authorized_scope(
    knowledge_base_id: uuid.UUID,
    settings: Settings,
) -> tuple[Any, ...]:
    """The mandatory, non-user-selectable retrieval scope.

    Knowledge base membership is the authorization boundary and is applied
    before ranking, never as post-retrieval cleanup.
    """
    return (
        Document.knowledge_base_id == knowledge_base_id,
        Document.status == DocumentStatus.READY,
        Chunk.embedding_model == settings.embedding_model_id,
    )


class VectorRetriever(Protocol):
    async def retrieve(
        self,
        session: AsyncSession,
        knowledge_base_id: uuid.UUID,
        query_text: str,
        query_vector: list[float],
        *,
        filters: RetrievalFilters | None,
        chunk_levels: tuple[ChunkLevel, ...],
        limit: int,
        settings: Settings,
        metrics: RetrievalMetrics | None = None,
    ) -> list[Candidate]: ...


class LexicalRetriever(Protocol):
    async def retrieve(
        self,
        session: AsyncSession,
        knowledge_base_id: uuid.UUID,
        query_text: str,
        *,
        filters: RetrievalFilters | None,
        chunk_levels: tuple[ChunkLevel, ...],
        limit: int,
        settings: Settings,
        metrics: RetrievalMetrics | None = None,
    ) -> list[Candidate]: ...


class PgVectorRetriever:
    """The existing pgvector cosine-distance leg, preserved as a baseline."""

    async def retrieve(
        self,
        session: AsyncSession,
        knowledge_base_id: uuid.UUID,
        query_text: str,
        query_vector: list[float],
        *,
        filters: RetrievalFilters | None,
        chunk_levels: tuple[ChunkLevel, ...],
        limit: int,
        settings: Settings,
        metrics: RetrievalMetrics | None = None,
    ) -> list[Candidate]:
        distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
        result = await session.execute(
            select(Chunk, Document, distance)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                *authorized_scope(knowledge_base_id, settings),
                Chunk.chunk_level.in_(chunk_levels),
                Chunk.embedding.is_not(None),
                *filters_sql(filters),
            )
            .order_by(distance)
            .limit(limit)
        )
        return [
            _candidate_from_row(
                chunk,
                document,
                semantic_similarity=1.0 - float(distance_value),
            )
            for chunk, document, distance_value in result.all()
        ]


class PostgresFTSLexicalRetriever:
    """The existing PostgreSQL full-text-search leg (the portable fallback)."""

    async def retrieve(
        self,
        session: AsyncSession,
        knowledge_base_id: uuid.UUID,
        query_text: str,
        *,
        filters: RetrievalFilters | None,
        chunk_levels: tuple[ChunkLevel, ...],
        limit: int,
        settings: Settings,
        metrics: RetrievalMetrics | None = None,
    ) -> list[Candidate]:
        search_query = func.websearch_to_tsquery(literal_column("'simple'"), query_text)
        lexical_rank = func.ts_rank_cd(Chunk.search_vector, search_query).label(
            "lexical_rank"
        )
        result = await session.execute(
            select(Chunk, Document, lexical_rank)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                *authorized_scope(knowledge_base_id, settings),
                Chunk.chunk_level.in_(chunk_levels),
                Chunk.search_vector.is_not(None),
                Chunk.search_vector.op("@@")(search_query),
                *filters_sql(filters),
            )
            .order_by(lexical_rank.desc())
            .limit(limit)
        )
        return [
            _candidate_from_row(chunk, document, lexical_score=float(rank_value))
            for chunk, document, rank_value in result.all()
        ]


class PgSearchBM25LexicalRetriever:
    """True BM25 lexical retrieval via the pg_search extension.

    Requires the `pg_search` extension and the `chunks_search_bm25` index. The
    caller decides whether BM25 is available through `is_bm25_available` and
    falls back to FTS when it is not.
    """

    def __init__(self, *, index_name: str = BM25_INDEX_NAME) -> None:
        self.index_name = index_name

    async def retrieve(
        self,
        session: AsyncSession,
        knowledge_base_id: uuid.UUID,
        query_text: str,
        *,
        filters: RetrievalFilters | None,
        chunk_levels: tuple[ChunkLevel, ...],
        limit: int,
        settings: Settings,
        metrics: RetrievalMetrics | None = None,
    ) -> list[Candidate]:
        # pg_search 0.24.1 accepts a bound text query on the @@@ operator and
        # exposes BM25 scoring from the `pdb` schema. Keep this version-pinned
        # dialect aligned with ADR-0005 and the compatibility spike.
        score = func.pdb.score(Chunk.id).label("bm25_score")
        try:
            # A failed extension query aborts its transaction in PostgreSQL.
            # Isolate it in a savepoint so the caller can safely execute the FTS
            # fallback on the same Session.
            async with session.begin_nested():
                result = await session.execute(
                    select(Chunk, Document, score)
                    .join(Document, Document.id == Chunk.document_id)
                    .where(
                        *authorized_scope(knowledge_base_id, settings),
                        Chunk.chunk_level.in_(chunk_levels),
                        Chunk.text.op("@@@")(query_text),
                        *filters_sql(filters),
                    )
                    .order_by(score.desc())
                    .limit(limit)
                )
                rows = result.all()
        except Exception as error:  # pragma: no cover - depends on the pg_search dialect
            logger.warning(
                "pg_search BM25 query failed (%s: %s); falling back to FTS",
                type(error).__name__,
                error,
            )
            raise Bm25UnavailableError("pg_search BM25 query failed") from error
        return [
            _candidate_from_row(
                chunk,
                document,
                lexical_score=float(score_value),
            )
            for chunk, document, score_value in rows
        ]


async def is_bm25_available(session: AsyncSession) -> bool:
    """Return whether pg_search and the BM25 index are both usable.

    Readiness is checked on every pipeline build so a missing extension or index
    degrades to the FTS baseline instead of failing retrieval.
    """
    row = await session.execute(
        text(
            "SELECT "
            "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_search') AS ext, "
            "to_regclass(:index_name) IS NOT NULL AS idx"
        ),
        {"index_name": BM25_INDEX_NAME},
    )
    ext, idx = row.one()
    return bool(ext and idx)


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
