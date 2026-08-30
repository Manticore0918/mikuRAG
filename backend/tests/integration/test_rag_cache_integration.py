"""Checkpoint-4 cache integration scenarios against PostgreSQL and Redis."""

import hashlib
import json
import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, text

from app.database import engine, session_factory
from app.models import (
    Chunk,
    Citation,
    Conversation,
    Document,
    DocumentStatus,
    KnowledgeBase,
    Message,
    MessageRole,
    MessageStatus,
    User,
)
from app.rag import service
from app.rag.cache import DerivedDataCache, cache_key
from app.rag.generation import GenerationResult
from app.rag.retrieval import Evidence, retrieve_evidence
from app.rag.retrieval_types import RetrievalFilters, RetrievalMetrics, RetrievalMode

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Corpus:
    primary_kb: KnowledgeBase
    primary_document: Document
    primary_chunk: Chunk
    other_kb: KnowledgeBase
    other_document: Document
    other_chunk: Chunk


@dataclass(frozen=True)
class TurnRecords:
    user: User
    conversation: Conversation
    user_message: Message
    assistant_message: Message


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
async def redis_client(settings):
    client = Redis.from_url(settings.redis_url)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("Redis is not reachable; integration tests need a live Redis")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def derived_cache(redis_client, settings):
    return DerivedDataCache(
        redis_client,
        ttl_seconds=settings.rag_cache_ttl_seconds,
        max_entry_bytes=settings.rag_cache_max_entry_bytes,
    )


@pytest_asyncio.fixture
async def corpus(db_session, settings):
    primary_kb = KnowledgeBase(name=f"cache-primary-{uuid.uuid4().hex[:12]}")
    other_kb = KnowledgeBase(name=f"cache-other-{uuid.uuid4().hex[:12]}")
    db_session.add_all([primary_kb, other_kb])
    await db_session.flush()

    primary_document = _document(primary_kb.id, "primary-handbook.md")
    other_document = _document(other_kb.id, "other-handbook.md")
    db_session.add_all([primary_document, other_document])
    await db_session.flush()

    primary_chunk = _chunk(
        primary_document.id,
        text_value=(
            "The Melody Harbor deployment window is Tuesday from 09:00 to 11:00 "
            "Singapore time."
        ),
        embedding=_vector(0),
        embedding_model=settings.embedding_model_id,
    )
    other_chunk = _chunk(
        other_document.id,
        text_value=(
            "The other Knowledge Base has a private Wednesday maintenance window."
        ),
        embedding=_vector(0),
        embedding_model=settings.embedding_model_id,
    )
    db_session.add_all([primary_chunk, other_chunk])
    await db_session.commit()

    yield Corpus(
        primary_kb=primary_kb,
        primary_document=primary_document,
        primary_chunk=primary_chunk,
        other_kb=other_kb,
        other_document=other_document,
        other_chunk=other_chunk,
    )

    await db_session.rollback()
    await db_session.execute(
        delete(KnowledgeBase).where(
            KnowledgeBase.id.in_([primary_kb.id, other_kb.id])
        )
    )
    await db_session.commit()


@pytest_asyncio.fixture
async def turn_records(db_session, corpus):
    user = User(
        username=f"cache-turn-{uuid.uuid4().hex[:12]}",
        password_hash="not-used-by-this-integration-test",
        is_administrator=False,
    )
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(
        owner_id=user.id,
        knowledge_base_id=corpus.primary_kb.id,
        title="Cache fail-open integration",
    )
    db_session.add(conversation)
    await db_session.flush()
    user_message = Message(
        conversation_id=conversation.id,
        sequence=1,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETE,
        content="When is the Melody Harbor deployment window?",
        model_metadata={},
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        sequence=2,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.STREAMING,
        content="",
        model_metadata={},
    )
    db_session.add_all([user_message, assistant_message])
    await db_session.commit()

    yield TurnRecords(user, conversation, user_message, assistant_message)

    await db_session.rollback()
    await db_session.execute(delete(User).where(User.id == user.id))
    await db_session.commit()


def _document(knowledge_base_id: uuid.UUID, name: str) -> Document:
    return Document(
        knowledge_base_id=knowledge_base_id,
        original_name=name,
        storage_key=f"integration/{uuid.uuid4().hex}.md",
        sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        media_type="text/markdown",
        size_bytes=256,
        status=DocumentStatus.READY,
        source_kind="markdown",
        chunking_version="legacy",
    )


def _chunk(
    document_id: uuid.UUID,
    *,
    text_value: str,
    embedding: list[float],
    embedding_model: str,
) -> Chunk:
    return Chunk(
        document_id=document_id,
        chunk_level="child",
        ordinal=0,
        text=text_value,
        locator={"page": 1},
        token_count=len(text_value.split()),
        chunking_version="legacy",
        chunking_config_hash="",
        content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        search_vector=func.to_tsvector("simple", text_value),
        embedding=embedding,
        embedding_model=embedding_model,
    )


def _vector(position: int) -> list[float]:
    vector = [0.0] * 768
    vector[position] = 1.0
    return vector


def _cache_settings(settings):
    return settings.model_copy(
        update={
            "hierarchical_retrieval_enabled": False,
            "query_embedding_cache_enabled": True,
            "query_planning_enabled": False,
            "retrieval_cache_enabled": True,
            "retrieval_mode": RetrievalMode.VECTOR,
        }
    )


async def _retrieve(
    db_session,
    cache: DerivedDataCache,
    settings,
    knowledge_base_id: uuid.UUID,
    *,
    index_generation: int | None = None,
) -> tuple[list[Evidence], bool, RetrievalMetrics]:
    metrics = RetrievalMetrics()
    evidence, sufficient = await retrieve_evidence(
        db_session,
        knowledge_base_id,
        "When is the Melody Harbor deployment window?",
        _vector(0),
        settings,
        mode=RetrievalMode.VECTOR,
        filters=RetrievalFilters.empty(),
        metrics=metrics,
        cache=cache,
        index_generation=index_generation,
    )
    return evidence, sufficient, metrics


def _retrieval_key(settings, knowledge_base_id: uuid.UUID, generation: int) -> str:
    return cache_key(
        "retrieval-result",
        knowledge_base_id=knowledge_base_id,
        index_generation=generation,
        query_text="When is the Melody Harbor deployment window?",
        filters=RetrievalFilters.empty(),
        settings=settings,
        mode=RetrievalMode.VECTOR,
    )


async def test_cold_and_warm_retrieval_are_equivalent_with_real_cache_hit(
    db_session,
    derived_cache,
    settings,
    corpus,
) -> None:
    active_settings = _cache_settings(settings)

    cold, cold_sufficient, cold_metrics = await _retrieve(
        db_session, derived_cache, active_settings, corpus.primary_kb.id
    )
    warm, warm_sufficient, warm_metrics = await _retrieve(
        db_session, derived_cache, active_settings, corpus.primary_kb.id
    )

    assert cold and cold == warm
    assert cold_sufficient is warm_sufficient is True
    assert cold_metrics.retrieval_cache_status == "miss"
    assert warm_metrics.retrieval_cache_status == "hit"
    assert {item.document_id for item in warm} == {corpus.primary_document.id}


async def test_ready_to_deleting_invalidates_immediately_and_rejects_stale_generation(
    db_session,
    derived_cache,
    settings,
    corpus,
) -> None:
    active_settings = _cache_settings(settings)
    old_generation = corpus.primary_kb.index_generation
    cached, sufficient, _ = await _retrieve(
        db_session,
        derived_cache,
        active_settings,
        corpus.primary_kb.id,
        index_generation=old_generation,
    )
    assert cached and sufficient
    old_key = _retrieval_key(active_settings, corpus.primary_kb.id, old_generation)
    assert (await derived_cache.get_json(old_key))[1] == "hit"

    corpus.primary_document.status = DocumentStatus.DELETING
    corpus.primary_kb.index_generation = old_generation + 1
    await db_session.commit()

    current, current_sufficient, current_metrics = await _retrieve(
        db_session, derived_cache, active_settings, corpus.primary_kb.id
    )
    assert current == []
    assert current_sufficient is False
    assert current_metrics.retrieval_cache_status == "miss"

    # Even a caller holding the old generation cannot revive cached evidence:
    # cached IDs are always re-authorized against Ready Documents in PostgreSQL.
    stale, stale_sufficient, _ = await _retrieve(
        db_session,
        derived_cache,
        active_settings,
        corpus.primary_kb.id,
        index_generation=old_generation,
    )
    assert stale == []
    assert stale_sufficient is False


async def test_generation_increment_makes_pre_reindex_cache_entry_unreachable(
    db_session,
    derived_cache,
    settings,
    corpus,
) -> None:
    active_settings = _cache_settings(settings)
    before, before_sufficient, before_metrics = await _retrieve(
        db_session, derived_cache, active_settings, corpus.primary_kb.id
    )
    assert before and before_sufficient
    assert before_metrics.retrieval_cache_status == "miss"

    # A successful re-index bumps this authoritative generation after replacing
    # chunks. The bump alone is the cache-invalidation operation.
    corpus.primary_kb.index_generation += 1
    await db_session.commit()

    after, after_sufficient, after_metrics = await _retrieve(
        db_session, derived_cache, active_settings, corpus.primary_kb.id
    )
    warm, warm_sufficient, warm_metrics = await _retrieve(
        db_session, derived_cache, active_settings, corpus.primary_kb.id
    )
    assert after == warm == before
    assert after_sufficient is warm_sufficient is True
    assert after_metrics.retrieval_cache_status == "miss"
    assert warm_metrics.retrieval_cache_status == "hit"


async def test_foreign_cache_payload_cannot_cross_knowledge_base_boundary(
    db_session,
    derived_cache,
    settings,
    corpus,
) -> None:
    active_settings = _cache_settings(settings)
    primary, _, _ = await _retrieve(
        db_session, derived_cache, active_settings, corpus.primary_kb.id
    )
    assert {item.document_id for item in primary} == {corpus.primary_document.id}

    primary_key = _retrieval_key(
        active_settings,
        corpus.primary_kb.id,
        corpus.primary_kb.index_generation,
    )
    other_key = _retrieval_key(
        active_settings,
        corpus.other_kb.id,
        corpus.other_kb.index_generation,
    )
    assert primary_key != other_key
    foreign_payload, status = await derived_cache.get_json(primary_key)
    assert status == "hit"

    # Simulate a poisoned/misrouted Redis value under the other KB's valid key.
    assert await derived_cache.set_json(other_key, foreign_payload) == "written"
    assert (await derived_cache.get_json(other_key))[1] == "hit"

    isolated, isolated_sufficient, isolated_metrics = await _retrieve(
        db_session, derived_cache, active_settings, corpus.other_kb.id
    )
    assert isolated and isolated_sufficient
    assert {item.document_id for item in isolated} == {corpus.other_document.id}
    assert all(item.chunk_id != corpus.primary_chunk.id for item in isolated)
    assert isolated_metrics.retrieval_cache_status == "miss"

    repaired_payload, repaired_status = await derived_cache.get_json(other_key)
    assert repaired_status == "hit"
    assert isinstance(repaired_payload, dict)
    assert {
        uuid.UUID(item["chunk_id"])
        for item in repaired_payload["candidates"]
    } == {corpus.other_chunk.id}


async def test_full_grounded_turn_completes_when_redis_is_unreachable(
    db_session,
    settings,
    corpus,
    turn_records,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_settings = _cache_settings(settings).model_copy(
        update={"redis_url": "redis://127.0.0.1:1/15"}
    )
    unavailable_redis = Redis.from_url(
        active_settings.redis_url,
        socket_connect_timeout=0.1,
        socket_timeout=0.1,
    )
    unavailable_cache = DerivedDataCache(
        unavailable_redis,
        ttl_seconds=active_settings.rag_cache_ttl_seconds,
        max_entry_bytes=active_settings.rag_cache_max_entry_bytes,
    )

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        assert texts == [turn_records.user_message.content]
        return [_vector(0)]

    async def fake_generation(_messages: list[dict[str, str]]) -> GenerationResult:
        return GenerationResult(
            payload={
                "status": "answer",
                "claims": [
                    {
                        "text": "The deployment window is Tuesday from 09:00 to 11:00.",
                        "evidence_ids": ["E1"],
                    }
                ],
            },
            usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        )

    monkeypatch.setattr(service, "get_settings", lambda: active_settings)
    monkeypatch.setattr(service, "get_derived_cache", lambda _settings: unavailable_cache)
    monkeypatch.setattr(service, "embed_texts", fake_embed)
    monkeypatch.setattr(service, "stream_json", fake_generation)

    try:
        events = [
            event
            async for event in service.turn_events(
                turn_records.conversation.id,
                turn_records.user_message.id,
                turn_records.assistant_message.id,
            )
        ]
    finally:
        await unavailable_cache.close()

    assert not any(event.startswith("event: error") for event in events)
    assert json.loads(events[-1].split("data: ", 1)[1]) == {
        "outcome": "grounded_answer"
    }

    await db_session.refresh(turn_records.assistant_message)
    assert turn_records.assistant_message.status == MessageStatus.COMPLETE
    assert turn_records.assistant_message.content.endswith("[1]")
    assert turn_records.assistant_message.model_metadata["outcome"] == "grounded_answer"
    assert turn_records.assistant_message.model_metadata["measurement"]["cache"] == {
        "query_embedding": "error",
        "retrieval": "error",
    }
    citations = list(
        await db_session.scalars(
            select(Citation).where(
                Citation.message_id == turn_records.assistant_message.id
            )
        )
    )
    assert len(citations) == 1
    assert citations[0].document_id == corpus.primary_document.id
