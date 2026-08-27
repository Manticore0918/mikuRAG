"""Tests for checkpoint-3 retrieval modes, weighted RRF, and cross-KB scoping.

These tests exercise `retrieve_evidence` through a recording fake session so no
live database is needed. Every select that the retrievers issue is inspected:
its bind parameters must carry the mandatory authorization scope and any
user-selectable filters, because that SQL scope (not post-retrieval cleanup) is
what guarantees a user of one Knowledge Base never sees another's Documents.
"""

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import Select, TextClause

from app.config import Settings
from app.models import Chunk, ChunkLevel, Document, DocumentStatus
from app.rag.fusion import RrfFusionStrategy
from app.rag.retrieval import retrieve_evidence
from app.rag.retrieval_types import (
    Candidate,
    RetrievalFilters,
    RetrievalMetrics,
    RetrievalMode,
)


def settings(**overrides) -> Settings:
    return Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        embedding_model_id="embed-v1",
        **overrides,
    )


def candidate(
    document_id: uuid.UUID,
    *,
    semantic: float | None = None,
    lexical: float | None = None,
    chunk_id: uuid.UUID | None = None,
) -> Candidate:
    identity = chunk_id or uuid.uuid4()
    return Candidate(
        chunk_id=identity,
        document_id=document_id,
        document_name="policy.md",
        locator={"section": "Access"},
        text="Authorized evidence",
        semantic_similarity=semantic,
        lexical_score=lexical,
    )


def chunk(*, document_id: uuid.UUID, text: str = "evidence") -> Chunk:
    return Chunk(
        id=uuid.uuid4(),
        document_id=document_id,
        chunk_level=ChunkLevel.CHILD,
        ordinal=0,
        text=text,
        locator={"page": 1},
        heading_path=["Access"],
        token_count=len(text.split()),
        chunking_version="legacy",
        content_type="mixed",
        embedding_model="embed-v1",
    )


def document(*, document_id: uuid.UUID, name: str = "policy.md") -> Document:
    return Document(
        id=document_id,
        original_name=name,
        status=DocumentStatus.READY,
    )


class FakeResult:
    def __init__(self, rows: list, one_value: Any | None = None) -> None:
        self._rows = rows
        self._one = one_value

    def all(self):
        return self._rows

    def one(self):
        if self._one is None:
            raise AssertionError("no one() value was configured for this fake")
        return self._one


class FakeSession:
    """A session that records every statement and returns configured rows.

    `is_bm25_available` issues a `TextClause`; that returns the configured
    availability tuple. Retrieval selects return the configured rows, so a test
    can prove ordering/fallback behavior end to end without a database.
    """

    def __init__(
        self,
        *,
        rows: tuple = (),
        bm25_available: tuple[bool, bool] = (False, False),
    ) -> None:
        self.rows = rows
        self.bm25_available = bm25_available
        self.executed: list[Any] = []

    async def execute(self, statement, *args, **kwargs):
        self.executed.append(statement)
        return FakeResult(
            list(self.rows),
            one_value=(
                self.bm25_available if isinstance(statement, TextClause) else None
            ),
        )

    @asynccontextmanager
    async def begin_nested(self):
        yield


def selects(session: FakeSession) -> list[Select]:
    return [item for item in session.executed if isinstance(item, Select)]


def compiled_values(statement: Select) -> list[Any]:
    """Flatten a compiled statement's bind values for membership assertions."""
    flattened: list[Any] = []
    for value in statement.compile().params.values():
        if isinstance(value, (list, tuple)):
            flattened.extend(value)
        else:
            flattened.append(value)
    return flattened


def test_rrf_weights_shift_ordering_between_legs() -> None:
    semantic_doc = uuid.uuid4()
    lexical_doc = uuid.uuid4()
    semantic_only = candidate(semantic_doc, semantic=0.9)
    lexical_only = candidate(lexical_doc, lexical=0.9)

    semantic_favored = RrfFusionStrategy().fuse(
        [semantic_only],
        [lexical_only],
        rrf_k=60,
        semantic_weight=2.0,
        lexical_weight=1.0,
        limit=2,
        max_per_document=2,
    )
    lexical_favored = RrfFusionStrategy().fuse(
        [semantic_only],
        [lexical_only],
        rrf_k=60,
        semantic_weight=1.0,
        lexical_weight=2.0,
        limit=2,
        max_per_document=2,
    )

    assert semantic_favored[0].chunk_id == semantic_only.chunk_id
    assert lexical_favored[0].chunk_id == lexical_only.chunk_id


def test_rrf_overlap_beats_a_single_leg_at_equal_weights() -> None:
    shared_doc = uuid.uuid4()
    single_doc = uuid.uuid4()
    shared = candidate(shared_doc, semantic=0.8)
    shared_lexical = candidate(
        shared_doc, lexical=0.6, chunk_id=shared.chunk_id
    )
    single = candidate(single_doc, semantic=0.95)

    ranked = RrfFusionStrategy().fuse(
        [single, shared],
        [shared_lexical],
        rrf_k=60,
        semantic_weight=1.0,
        lexical_weight=1.0,
        limit=2,
        max_per_document=2,
    )

    assert ranked[0].chunk_id == shared.chunk_id
    assert ranked[0].semantic_similarity == 0.8
    assert ranked[0].lexical_score == 0.6


@pytest.mark.asyncio
async def test_vector_mode_runs_only_the_vector_leg() -> None:
    session = FakeSession()
    metrics = RetrievalMetrics()
    await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(),
        mode=RetrievalMode.VECTOR,
        metrics=metrics,
    )

    executed = selects(session)
    assert len(executed) == 1
    assert "websearch_to_tsquery" not in str(executed[0].compile())
    assert metrics.retrieval_mode == "vector"
    assert metrics.lexical_kind is None


@pytest.mark.asyncio
async def test_fts_baseline_mode_runs_only_the_fts_leg() -> None:
    session = FakeSession()
    metrics = RetrievalMetrics()
    await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(),
        mode=RetrievalMode.FTS_BASELINE,
        metrics=metrics,
    )

    executed = selects(session)
    assert len(executed) == 1
    assert "websearch_to_tsquery" in str(executed[0].compile())
    assert metrics.lexical_kind == "fts"


@pytest.mark.asyncio
async def test_bm25_mode_uses_pg_search_when_available() -> None:
    session = FakeSession(bm25_available=(True, True))
    metrics = RetrievalMetrics()
    await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(),
        mode=RetrievalMode.BM25,
        metrics=metrics,
    )

    executed = selects(session)
    assert len(executed) == 1
    assert "@@@" in str(executed[0].compile())
    assert metrics.lexical_kind == "bm25"
    assert metrics.bm25_index_available is True


@pytest.mark.asyncio
async def test_bm25_mode_falls_back_to_fts_when_unavailable() -> None:
    session = FakeSession(bm25_available=(False, False))
    metrics = RetrievalMetrics()
    await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(),
        mode=RetrievalMode.BM25,
        metrics=metrics,
    )

    executed = selects(session)
    assert len(executed) == 1
    assert "websearch_to_tsquery" in str(executed[0].compile())
    assert metrics.lexical_kind == "fts_fallback"
    assert metrics.bm25_index_available is False


@pytest.mark.asyncio
async def test_bm25_execution_failure_retries_with_fts(monkeypatch) -> None:
    from app.rag import retrieval as retrieval_module
    from app.rag.retrievers import Bm25UnavailableError

    class FailingBm25:
        async def retrieve(self, *args, **kwargs):
            raise Bm25UnavailableError("dialect failed")

    monkeypatch.setattr(
        retrieval_module,
        "PgSearchBM25LexicalRetriever",
        lambda: FailingBm25(),
    )
    session = FakeSession(bm25_available=(True, True))
    metrics = RetrievalMetrics()

    await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(),
        mode=RetrievalMode.BM25,
        metrics=metrics,
    )

    executed = selects(session)
    assert len(executed) == 1
    assert "websearch_to_tsquery" in str(executed[0].compile())
    assert metrics.lexical_kind == "fts_fallback"


@pytest.mark.asyncio
async def test_hybrid_mode_runs_both_legs_with_fts_baseline() -> None:
    session = FakeSession()
    metrics = RetrievalMetrics()
    await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(),
        mode=RetrievalMode.HYBRID_RRF,
        metrics=metrics,
    )

    executed = selects(session)
    assert len(executed) == 2
    assert metrics.lexical_kind == "fts"
    assert metrics.bm25_index_available is None


@pytest.mark.asyncio
async def test_hybrid_mode_uses_bm25_when_enabled_and_available() -> None:
    session = FakeSession(bm25_available=(True, True))
    metrics = RetrievalMetrics()
    await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(bm25_hybrid_enabled=True),
        mode=RetrievalMode.HYBRID_RRF,
        metrics=metrics,
    )

    executed = selects(session)
    assert len(executed) == 2
    assert "@@@" in str(executed[1].compile())
    assert metrics.lexical_kind == "bm25"
    assert metrics.bm25_index_available is True


@pytest.mark.asyncio
async def test_reranked_mode_records_the_reranker_and_runs_it() -> None:
    class RecordingReranker:
        provider_name = "test_reranker"
        model_name = "test-model"
        version = "v1"

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def rerank(self, query: str, candidates: list[Candidate]):
            self.calls.append(query)
            return candidates

    session = FakeSession()
    reranker = RecordingReranker()
    metrics = RetrievalMetrics()
    await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(),
        mode=RetrievalMode.HYBRID_RRF_RERANKED,
        reranker=reranker,
        metrics=metrics,
    )

    assert reranker.calls == ["query"]
    assert metrics.retrieval_mode == "hybrid_rrf_reranked"
    assert metrics.reranker_provider == "test_reranker"
    assert metrics.reranker_model == "test-model"
    assert metrics.reranker_version == "v1"


@pytest.mark.asyncio
async def test_rerank_failure_falls_back_to_fused_order() -> None:
    class FailingReranker:
        provider_name = "failing"

        async def rerank(self, query: str, candidates: list[Candidate]):
            raise RuntimeError("model unavailable")

    document_id = uuid.uuid4()
    row = (chunk(document_id=document_id), document(document_id=document_id), 0.2)
    session = FakeSession(rows=[row])
    metrics = RetrievalMetrics()
    evidence, _ = await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(),
        mode=RetrievalMode.HYBRID_RRF_RERANKED,
        reranker=FailingReranker(),
        metrics=metrics,
    )

    assert metrics.reranker_provider == "fallback_fused_order"
    assert len(evidence) == 1
    assert metrics.reranked_candidate_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        RetrievalMode.VECTOR,
        RetrievalMode.FTS_BASELINE,
        RetrievalMode.BM25,
        RetrievalMode.HYBRID_RRF,
        RetrievalMode.HYBRID_RRF_RERANKED,
    ],
)
async def test_every_mode_scopes_retrieval_to_the_knowledge_base(
    mode: RetrievalMode,
) -> None:
    knowledge_base_id = uuid.uuid4()
    other_kb_id = uuid.uuid4()
    if mode == RetrievalMode.BM25:
        session = FakeSession(bm25_available=(True, True))
    else:
        session = FakeSession()

    await retrieve_evidence(
        session,
        knowledge_base_id,
        "query",
        [0.1] * 8,
        settings(),
        mode=mode,
    )

    executed = selects(session)
    assert len(executed) >= 1
    for statement in executed:
        values = compiled_values(statement)
        assert knowledge_base_id in values, f"{mode}: KB scope is missing"
        assert DocumentStatus.READY in values, f"{mode}: status scope is missing"
        assert "embed-v1" in values, f"{mode}: embedding scope is missing"
        assert other_kb_id not in values, f"{mode}: leaked another KB"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        RetrievalMode.VECTOR,
        RetrievalMode.FTS_BASELINE,
        RetrievalMode.HYBRID_RRF,
    ],
)
async def test_filters_are_pushed_into_every_leg_before_ranking(
    mode: RetrievalMode,
) -> None:
    knowledge_base_id = uuid.uuid4()
    document_id = uuid.uuid4()
    session = FakeSession()
    await retrieve_evidence(
        session,
        knowledge_base_id,
        "query",
        [0.1] * 8,
        settings(),
        mode=mode,
        filters=RetrievalFilters(
            document_ids=(document_id,),
            tags=("hr",),
            source_kinds=("pdf",),
            languages=("en",),
        ),
    )

    executed = selects(session)
    expected_legs = (
        1 if mode in (RetrievalMode.VECTOR, RetrievalMode.FTS_BASELINE) else 2
    )
    assert len(executed) == expected_legs
    for statement in executed:
        values = compiled_values(statement)
        assert document_id in values, f"{mode}: document filter is missing"
        assert "hr" in values, f"{mode}: tag filter is missing"
        assert "pdf" in values, f"{mode}: source filter is missing"
        assert "en" in values, f"{mode}: language filter is missing"


@pytest.mark.asyncio
async def test_empty_filters_leave_no_filter_predicates() -> None:
    document_id = uuid.uuid4()
    session = FakeSession()
    await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(),
        mode=RetrievalMode.HYBRID_RRF,
        filters=RetrievalFilters(),
    )

    for statement in selects(session):
        assert document_id not in compiled_values(statement)


@pytest.mark.asyncio
async def test_mode_accepts_string_names() -> None:
    session = FakeSession()
    metrics = RetrievalMetrics()
    await retrieve_evidence(
        session,
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings(),
        mode="fts_baseline",
        metrics=metrics,
    )

    assert metrics.retrieval_mode == "fts_baseline"
