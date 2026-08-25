import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import rollout as rollout_api
from app.config import Settings
from app.ingestion.reindexing import retryable_reindex_failure
from app.ingestion.tasks import _hierarchical_config
from app.models import (
    Document,
    DocumentStatus,
    ReindexItem,
    ReindexJob,
    ReindexJobStatus,
)
from app.rollout import (
    RolloutPhase,
    configured_rollout_phase,
    select_canary_documents,
)
from app.schemas import ReindexJobCreate


def settings(**overrides) -> Settings:
    return Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        **overrides,
    )


def document(identity: int, knowledge_base_id: uuid.UUID) -> Document:
    now = datetime.now(UTC)
    return Document(
        id=uuid.UUID(int=identity),
        knowledge_base_id=knowledge_base_id,
        original_name=f"{identity}.txt",
        storage_key=f"{identity}.txt",
        sha256=f"{identity:064x}",
        media_type="text/plain",
        size_bytes=10,
        status="ready",
        created_at=now,
        updated_at=now,
    )


def test_rollout_phase_tracks_feature_flags_and_active_reindexing() -> None:
    assert configured_rollout_phase(
        settings(),
        active_reindex_job=False,
    ) == RolloutPhase.BASELINE
    assert configured_rollout_phase(
        settings(chunking_version="hierarchical_v1"),
        active_reindex_job=False,
    ) == RolloutPhase.STRUCTURED_CHUNKING
    assert configured_rollout_phase(
        settings(
            chunking_version="hierarchical_v1",
            hierarchical_retrieval_enabled=True,
        ),
        active_reindex_job=False,
    ) == RolloutPhase.RETRIEVAL_EXPANSION
    assert configured_rollout_phase(
        settings(
            chunking_version="hierarchical_v1",
            hierarchical_retrieval_enabled=True,
            summary_generation_enabled=True,
        ),
        active_reindex_job=False,
    ) == RolloutPhase.BROAD_QUESTIONS
    assert configured_rollout_phase(
        settings(),
        active_reindex_job=True,
    ) == RolloutPhase.REINDEXING


def test_canary_selection_is_stable_bounded_and_nonempty() -> None:
    first_knowledge_base = uuid.uuid4()
    second_knowledge_base = uuid.uuid4()
    documents = [
        document(
            index,
            first_knowledge_base if index <= 10 else second_knowledge_base,
        )
        for index in range(1, 21)
    ]

    first = select_canary_documents(documents, 10)
    second = select_canary_documents(list(reversed(documents)), 10)

    assert [item.id for item in first] == [item.id for item in second]
    assert len(first) == 2
    assert len(select_canary_documents(documents[:3], 1)) == 1
    assert len(select_canary_documents(documents, 100)) == 20


def test_reindex_request_requires_explicit_safe_scope() -> None:
    canary = ReindexJobCreate(
        target_chunking_version="hierarchical_v1",
        selection_mode="canary",
        canary_percentage=5,
        batch_size=3,
    )

    assert canary.canary_percentage == 5
    with pytest.raises(ValidationError, match="requires canary_percentage=100"):
        ReindexJobCreate(
            target_chunking_version="hierarchical_v1",
            selection_mode="all",
            canary_percentage=10,
        )


def test_ingestion_override_builds_requested_chunk_version() -> None:
    active_settings = settings(chunking_version="legacy")

    config = _hierarchical_config(active_settings, "hierarchical_v1")

    assert config.chunking_version == "hierarchical_v1"


def test_transient_reindex_failures_retry_only_within_the_cap() -> None:
    failed = document(1, uuid.uuid4())
    failed.status = DocumentStatus.FAILED
    failed.safe_error = "embed: provider unavailable"

    assert retryable_reindex_failure(
        failed,
        attempts=1,
        maximum_attempts=3,
    )
    assert not retryable_reindex_failure(
        failed,
        attempts=3,
        maximum_attempts=3,
    )
    failed.safe_error = "validate: omitted source content"
    assert not retryable_reindex_failure(
        failed,
        attempts=1,
        maximum_attempts=3,
    )


@pytest.mark.asyncio
async def test_job_creation_is_durable_and_pauses_when_queue_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Session:
        def __init__(self) -> None:
            self.values = []
            self.active_job_id = None

        async def scalar(self, statement):
            return self.active_job_id

        def add(self, value) -> None:
            if isinstance(value, ReindexJob):
                value.id = uuid.uuid4()
                value.created_at = datetime.now(UTC)
                value.updated_at = datetime.now(UTC)
            self.values.append(value)

        def add_all(self, values) -> None:
            self.values.extend(values)

        async def flush(self) -> None:
            pass

        async def commit(self) -> None:
            pass

        async def refresh(self, value) -> None:
            value.updated_at = datetime.now(UTC)

    database = Session()
    knowledge_base_id = uuid.uuid4()
    documents = [
        document(1, knowledge_base_id),
        document(2, knowledge_base_id),
    ]
    monkeypatch.setattr(rollout_api, "enqueue_reindex_batch", lambda _: False)

    job = await rollout_api._create_job(  # type: ignore[arg-type]
        database,
        documents=documents,
        target_version="hierarchical_v1",
        selection_mode="canary",
        canary_percentage=10,
        batch_size=2,
        knowledge_base_id=knowledge_base_id,
        created_by_id=uuid.uuid4(),
    )

    assert job.status == ReindexJobStatus.PAUSED
    assert job.total_documents == 2
    assert "queue is unavailable" in (job.last_error or "")
    items = [value for value in database.values if isinstance(value, ReindexItem)]
    assert {item.document_id for item in items} == {item.id for item in documents}

    database.active_job_id = job.id
    with pytest.raises(HTTPException, match="active re-index job"):
        await rollout_api._create_job(  # type: ignore[arg-type]
            database,
            documents=documents,
            target_version="legacy",
            selection_mode="all",
            canary_percentage=100,
            batch_size=2,
            knowledge_base_id=knowledge_base_id,
            created_by_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_job_remains_queued_until_a_worker_claims_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Session:
        def __init__(self) -> None:
            self.values = []

        async def scalar(self, statement):
            return None

        def add(self, value) -> None:
            if isinstance(value, ReindexJob):
                value.id = uuid.uuid4()
                value.created_at = datetime.now(UTC)
                value.updated_at = datetime.now(UTC)
            self.values.append(value)

        def add_all(self, values) -> None:
            self.values.extend(values)

        async def flush(self) -> None:
            pass

        async def commit(self) -> None:
            pass

        async def refresh(self, value) -> None:
            value.updated_at = datetime.now(UTC)

    database = Session()
    knowledge_base_id = uuid.uuid4()
    monkeypatch.setattr(rollout_api, "enqueue_reindex_batch", lambda _: True)

    job = await rollout_api._create_job(  # type: ignore[arg-type]
        database,
        documents=[document(1, knowledge_base_id)],
        target_version="hierarchical_v1",
        selection_mode="canary",
        canary_percentage=10,
        batch_size=1,
        knowledge_base_id=knowledge_base_id,
        created_by_id=uuid.uuid4(),
    )

    assert job.status == ReindexJobStatus.QUEUED
