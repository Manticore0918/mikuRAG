"""Integration tests against real PostgreSQL/pgvector and Redis.

These tests are marked `integration` and are deselected by default
(`addopts`); CI runs them with `-m integration` against service containers.
The BM25 assertions run when the pg_search extension and its index are
available and skip otherwise — the compose smoke exercises the preloaded
pg_search path for real on every pull request.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, text

from app.database import engine, session_factory
from app.models import Chunk, Document, DocumentStatus, KnowledgeBase
from app.rag.retrieval_types import Candidate, RetrievalFilters
from app.rag.retrievers import (
    PgSearchBM25LexicalRetriever,
    PgVectorRetriever,
    PostgresFTSLexicalRetriever,
    is_bm25_available,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL is not reachable; integration tests need a live database")
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def knowledge_base(db_session, settings):
    """A Knowledge Base with one Ready Document and two indexed chunks."""

    kb = KnowledgeBase(
        name=f"integration-{uuid.uuid4().hex[:12]}",
        description="Checkpoint 5 integration fixture",
    )
    db_session.add(kb)
    await db_session.flush()

    document = Document(
        knowledge_base_id=kb.id,
        original_name="integration-fixture.md",
        storage_key=f"integration/{uuid.uuid4().hex}.md",
        sha256=uuid.uuid4().hex,
        media_type="text/markdown",
        size_bytes=256,
        status=DocumentStatus.READY,
        source_kind="markdown",
        chunking_version="legacy",
    )
    db_session.add(document)
    await db_session.flush()

    await _insert_chunk(
        db_session,
        document,
        ordinal=0,
        text_value=(
            "The Melody Harbor deployment window is Tuesday 09:00 to 11:00 Singapore "
            "time. Error code MH-4021 pages the release manager."
        ),
        embedding=_unit_vector(1.0),
        settings=settings,
    )
    await _insert_chunk(
        db_session,
        document,
        ordinal=1,
        text_value=(
            "Rollbacks keep the previous version active while support informs customers."
        ),
        embedding=_unit_vector(2.0),
        settings=settings,
    )
    await db_session.commit()

    yield kb, document

    await db_session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb.id))
    await db_session.commit()


async def _insert_chunk(
    session,
    document: Document,
    *,
    ordinal: int,
    text_value: str,
    embedding: list[float],
    settings,
) -> Chunk:
    chunk = Chunk(
        document_id=document.id,
        chunk_level="child",
        ordinal=ordinal,
        text=text_value,
        locator={"page": 1},
        token_count=len(text_value.split()),
        chunking_version="legacy",
        chunking_config_hash="",
        content_hash=uuid.uuid4().hex,
        search_vector=func.to_tsvector("simple", text_value),
        embedding=embedding,
        embedding_model=settings.embedding_model_id,
    )
    session.add(chunk)
    await session.flush()
    return chunk


def _unit_vector(seed: float) -> list[float]:
    return [0.0] * 765 + [seed, 0.0, 0.0]


async def _foreign_chunk(db_session, settings) -> tuple[KnowledgeBase, Chunk]:
    """A chunk in another Knowledge Base that must never leak into results."""

    kb = KnowledgeBase(name=f"integration-other-{uuid.uuid4().hex[:12]}")
    db_session.add(kb)
    await db_session.flush()

    document = Document(
        knowledge_base_id=kb.id,
        original_name="foreign-fixture.md",
        storage_key=f"integration/{uuid.uuid4().hex}.md",
        sha256=uuid.uuid4().hex,
        media_type="text/markdown",
        size_bytes=64,
        status=DocumentStatus.READY,
        source_kind="markdown",
    )
    db_session.add(document)
    await db_session.flush()

    chunk = Chunk(
        document_id=document.id,
        chunk_level="child",
        ordinal=0,
        text=(
            "The Melody Harbor deployment window is Tuesday 09:00 to 11:00 Singapore "
            "time. Error code MH-4021 pages the release manager."
        ),
        locator={},
        chunking_version="legacy",
        chunking_config_hash="",
        search_vector=func.to_tsvector("simple", "deployment window Melody Harbor"),
        embedding=_unit_vector(1.0),
        embedding_model=settings.embedding_model_id,
    )
    db_session.add(chunk)
    await db_session.commit()
    return kb, chunk


async def test_pgvector_retrieval_is_authorization_scoped(
    db_session, settings, knowledge_base
) -> None:
    kb, _ = knowledge_base
    foreign_kb, _ = await _foreign_chunk(db_session, settings)

    candidates: list[Candidate] = await PgVectorRetriever().retrieve(
        db_session,
        kb.id,
        "deployment window",
        _unit_vector(1.0),
        filters=None,
        chunk_levels=("child",),
        limit=10,
        settings=settings,
    )
    assert [candidate.chunk_id for candidate in candidates], "vector leg returned nothing"
    assert all(candidate.document_name == "integration-fixture.md" for candidate in candidates)
    assert candidates[0].semantic_similarity == pytest.approx(1.0)

    await db_session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == foreign_kb.id))
    await db_session.commit()


async def test_fts_retrieval_matches_and_never_leaks(
    db_session, settings, knowledge_base
) -> None:
    kb, _ = knowledge_base
    foreign_kb, _ = await _foreign_chunk(db_session, settings)

    candidates = await PostgresFTSLexicalRetriever().retrieve(
        db_session,
        kb.id,
        "Melody Harbor deployment window",
        filters=None,
        chunk_levels=("child",),
        limit=10,
        settings=settings,
    )
    assert candidates
    assert all(candidate.document_name == "integration-fixture.md" for candidate in candidates)
    assert candidates[0].lexical_score > 0

    empty = await PostgresFTSLexicalRetriever().retrieve(
        db_session,
        foreign_kb.id,
        "nonexistent-term-zzz",
        filters=None,
        chunk_levels=("child",),
        limit=10,
        settings=settings,
    )
    assert empty == []

    await db_session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == foreign_kb.id))
    await db_session.commit()


async def test_bm25_retrieval_when_pg_search_is_available(
    db_session, settings, knowledge_base
) -> None:
    kb, _ = knowledge_base
    if not await is_bm25_available(db_session):
        pytest.skip(
            "pg_search or the BM25 index is unavailable in this image; "
            "the compose smoke covers the preloaded path"
        )

    candidates = await PgSearchBM25LexicalRetriever().retrieve(
        db_session,
        kb.id,
        "MH-4021",
        filters=None,
        chunk_levels=("child",),
        limit=10,
        settings=settings,
    )
    assert candidates
    assert all("MH-4021" in candidate.text for candidate in candidates)


async def test_reconcile_optional_features_reports_a_status(db_session) -> None:
    from app.database_features import reconcile_optional_database_features

    status = await reconcile_optional_database_features(engine)
    assert status["status"] in {"ready", "unavailable", "error"}
    detail = status.get("detail")
    assert detail is None or isinstance(detail, str)


async def test_metadata_filters_are_pushed_before_candidate_limits(
    db_session, settings, knowledge_base
) -> None:
    kb, _ = knowledge_base
    tags_filter = RetrievalFilters(tags=("integration-nonexistent",))
    candidates = await PgVectorRetriever().retrieve(
        db_session,
        kb.id,
        "deployment window",
        _unit_vector(1.0),
        filters=tags_filter,
        chunk_levels=("child",),
        limit=10,
        settings=settings,
    )
    assert candidates == []
