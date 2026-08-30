import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Delete, Update

from app.api import documents as document_api
from app.config import Settings
from app.ingestion import tasks
from app.ingestion.errors import IngestionError
from app.models import Document, DocumentStatus


def _document(status: DocumentStatus) -> Document:
    now = datetime.now(UTC)
    return Document(
        id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        original_name="lifecycle.txt",
        storage_key="documents/lifecycle.txt",
        sha256="a" * 64,
        media_type="text/plain",
        size_bytes=32,
        status=status,
        safe_error="previous failure" if status == DocumentStatus.FAILED else None,
        source_kind="text",
        source_metadata={},
        ingestion_stage="failed" if status == DocumentStatus.FAILED else "queued",
        ingestion_progress=80 if status == DocumentStatus.FAILED else 0,
        ingestion_attempts=2,
        ingestion_warnings=[{"code": "previous_warning"}],
        created_at=now,
        updated_at=now,
    )


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Engine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _RetrySession:
    def __init__(self, document: Document) -> None:
        self.document = document
        self.commit_count = 0
        self.refresh_count = 0

    async def scalar(self, _statement):
        return self.document

    async def commit(self) -> None:
        self.commit_count += 1

    async def refresh(self, value: Document) -> None:
        assert value is self.document
        self.refresh_count += 1


@pytest.mark.asyncio
async def test_retry_document_resets_failure_and_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document(DocumentStatus.FAILED)
    session = _RetrySession(document)
    enqueued: list[uuid.UUID] = []
    monkeypatch.setattr(
        document_api,
        "enqueue_ingestion",
        lambda document_id: enqueued.append(document_id) is None or True,
    )

    result = await document_api.retry_document(
        document.knowledge_base_id,
        document.id,
        None,
        SimpleNamespace(),
        session,  # type: ignore[arg-type]
    )

    assert result is document
    assert document.status == DocumentStatus.PENDING
    assert document.safe_error is None
    assert document.ingestion_stage == "queued"
    assert document.ingestion_progress == 0
    assert document.ingestion_warnings == []
    assert enqueued == [document.id]
    assert session.commit_count == session.refresh_count == 1


@pytest.mark.asyncio
async def test_retry_document_records_enqueue_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document(DocumentStatus.FAILED)
    session = _RetrySession(document)
    monkeypatch.setattr(document_api, "enqueue_ingestion", lambda _document_id: False)

    result = await document_api.retry_document(
        document.knowledge_base_id,
        document.id,
        None,
        SimpleNamespace(),
        session,  # type: ignore[arg-type]
    )

    assert result.status == DocumentStatus.FAILED
    assert result.ingestion_stage == "failed"
    assert result.safe_error == (
        "The ingestion queue is unavailable. Retry this Document later."
    )
    assert session.commit_count == session.refresh_count == 2


@pytest.mark.asyncio
async def test_retry_document_rejects_non_failed_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document(DocumentStatus.READY)
    session = _RetrySession(document)
    enqueue_called = False

    def enqueue(_document_id: uuid.UUID) -> bool:
        nonlocal enqueue_called
        enqueue_called = True
        return True

    monkeypatch.setattr(document_api, "enqueue_ingestion", enqueue)

    with pytest.raises(HTTPException) as error:
        await document_api.retry_document(
            document.knowledge_base_id,
            document.id,
            None,
            SimpleNamespace(),
            session,  # type: ignore[arg-type]
        )

    assert error.value.status_code == 409
    assert not enqueue_called
    assert session.commit_count == 0


class _IngestionSession:
    def __init__(self, state: SimpleNamespace, sequence: int) -> None:
        self.state = state
        self.sequence = sequence

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def execute(self, statement):
        assert self.sequence == 0
        assert isinstance(statement, Update)
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        stale_before = compiled.params["updated_at_1"]
        self.state.claim_sql = sql
        self.state.stale_before = stale_before
        eligible = (
            self.state.document.status == DocumentStatus.PROCESSING
            and self.state.document.updated_at <= stale_before
        )
        if eligible:
            self.state.document.status = DocumentStatus.PROCESSING
            self.state.document.safe_error = None
            self.state.document.ingestion_stage = "extract"
            self.state.document.ingestion_progress = 5
            self.state.document.ingestion_attempts += 1
            self.state.document.updated_at = datetime.now(UTC)
        return _Result(int(eligible))

    async def get(self, model, identity):
        assert self.sequence == 1
        assert model is Document
        return self.state.document if identity == self.state.document.id else None

    async def scalar(self, _statement):
        assert self.sequence == 2
        return self.state.document

    async def commit(self) -> None:
        return None


class _IngestionSessions:
    def __init__(self, state: SimpleNamespace) -> None:
        self.state = state
        self.sequence = 0

    def __call__(self) -> _IngestionSession:
        session = _IngestionSession(self.state, self.sequence)
        self.sequence += 1
        return session


@pytest.mark.asyncio
async def test_run_ingestion_reclaims_stale_processing_document(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_seconds = 60
    document = _document(DocumentStatus.PROCESSING)
    document.updated_at = datetime.now(UTC) - timedelta(seconds=stale_seconds + 5)
    previous_attempts = document.ingestion_attempts
    state = SimpleNamespace(document=document, claim_sql=None, stale_before=None)
    sessions = _IngestionSessions(state)
    engine = _Engine()
    settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        upload_dir=tmp_path,
        ingestion_stale_after_seconds=stale_seconds,
    )
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(
        tasks,
        "async_sessionmaker",
        lambda *_args, **_kwargs: sessions,
    )
    monkeypatch.setattr(
        tasks,
        "extract_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IngestionError("controlled extraction failure")
        ),
    )

    outcome = await tasks.run_ingestion(document.id)

    assert outcome == "failed"
    assert "documents.status" in state.claim_sql
    assert "documents.updated_at <=" in state.claim_sql
    assert state.stale_before >= datetime.now(UTC) - timedelta(seconds=stale_seconds + 2)
    assert document.ingestion_attempts == previous_attempts + 1
    assert document.status == DocumentStatus.FAILED
    assert document.safe_error == "extract: controlled extraction failure"
    assert engine.disposed


class _PurgeSession:
    def __init__(self, state: SimpleNamespace, sequence: int) -> None:
        self.state = state
        self.sequence = sequence

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def get(self, model, identity):
        assert self.sequence == 0
        assert model is Document
        return self.state.document if identity == self.state.document.id else None

    async def execute(self, statement):
        assert self.sequence == 1
        self.state.statements.append(statement)
        return _Result(1)

    async def commit(self) -> None:
        self.state.commits += 1


class _PurgeSessions:
    def __init__(self, state: SimpleNamespace) -> None:
        self.state = state
        self.sequence = 0

    def __call__(self) -> _PurgeSession:
        session = _PurgeSession(self.state, self.sequence)
        self.sequence += 1
        return session


@pytest.mark.asyncio
async def test_run_purge_removes_document_and_advances_index_generation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _document(DocumentStatus.DELETING)
    state = SimpleNamespace(document=document, statements=[], commits=0)
    sessions = _PurgeSessions(state)
    engine = _Engine()
    settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        upload_dir=tmp_path,
    )
    removed: list[tuple[object, str]] = []
    maintenance_engines: list[object] = []

    async def repair(active_engine):
        maintenance_engines.append(active_engine)
        return {"status": "ok", "detail": None}

    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(
        tasks,
        "async_sessionmaker",
        lambda *_args, **_kwargs: sessions,
    )
    monkeypatch.setattr(
        tasks,
        "remove_stored_file_sync",
        lambda upload_dir, storage_key: removed.append((upload_dir, storage_key)),
    )
    monkeypatch.setattr(tasks, "repair_bm25_after_deletion", repair)

    await tasks.run_purge(document.id)

    assert removed == [(tmp_path, document.storage_key)]
    assert len(state.statements) == 2
    assert isinstance(state.statements[0], Delete)
    assert isinstance(state.statements[1], Update)
    generation_sql = str(state.statements[1].compile(dialect=postgresql.dialect()))
    assert "index_generation=(knowledge_bases.index_generation +" in generation_sql
    assert state.commits == 1
    assert maintenance_engines == [engine]
    assert engine.disposed
