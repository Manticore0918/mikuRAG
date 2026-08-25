import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.api import uploads as upload_api
from app.config import Settings
from app.models import (
    Document,
    UploadPartReceipt,
    UploadSession,
    UploadSessionStatus,
    User,
)
from app.uploads.storage import append_part, file_size, storage_keys


class FakeUploadDatabase:
    def __init__(self, scalar_values):
        self.scalar_values = list(scalar_values)
        self.receipts: dict[tuple[uuid.UUID, int], UploadPartReceipt] = {}
        self.documents: dict[uuid.UUID, Document] = {}
        self.commit_count = 0

    async def scalar(self, _statement):
        return self.scalar_values.pop(0)

    async def get(self, model, identity):
        if model is UploadPartReceipt:
            return self.receipts.get(
                (identity["upload_session_id"], identity["offset_bytes"])
            )
        if model is Document:
            return self.documents.get(identity)
        return None

    async def execute(self, _statement, _parameters=None):
        return None

    def add(self, value):
        if isinstance(value, UploadPartReceipt):
            self.receipts[(value.upload_session_id, value.offset_bytes)] = value
        if isinstance(value, Document):
            self.documents[value.id] = value

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        return None

    async def refresh(self, value):
        now = datetime.now(UTC)
        if getattr(value, "created_at", None) is None:
            value.created_at = now
        if getattr(value, "updated_at", None) is None:
            value.updated_at = now

    async def delete(self, _value):
        return None


def make_settings(tmp_path) -> Settings:
    return Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        upload_dir=tmp_path,
    )


def make_upload(tmp_path, *, total_bytes: int) -> UploadSession:
    upload_id = uuid.uuid4()
    temporary_key, final_key = storage_keys(upload_id)
    now = datetime.now(UTC)
    return UploadSession(
        id=upload_id,
        knowledge_base_id=uuid.uuid4(),
        initiated_by_id=uuid.uuid4(),
        original_name="notes.txt",
        suffix=".txt",
        source_kind="text",
        language=None,
        tags=["operations"],
        source_uri="https://docs.example.test/notes",
        source_path="docs/notes.txt",
        source_metadata={"title": "Notes", "owner_email": "private@example.test"},
        declared_sha256=hashlib.sha256(b"test"[:total_bytes]).hexdigest(),
        total_bytes=total_bytes,
        received_bytes=0,
        part_size_bytes=total_bytes,
        temporary_storage_key=temporary_key,
        final_storage_key=final_key,
        status=UploadSessionStatus.OPEN,
        expires_at=now + timedelta(hours=24),
        created_at=now,
        updated_at=now,
    )


def make_administrator() -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        username="admin",
        password_hash="unused",
        is_administrator=True,
        is_enabled=True,
        session_version=1,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_identical_part_retry_is_acknowledged_without_duplicate_bytes(
    tmp_path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    upload = make_upload(tmp_path, total_bytes=4)
    database = FakeUploadDatabase([upload, upload])
    monkeypatch.setattr(upload_api, "get_settings", lambda: settings)
    checksum = hashlib.sha256(b"test").hexdigest()

    first = await upload_api.put_upload_part(
        upload.knowledge_base_id,
        upload.id,
        b"test",
        None,
        make_administrator(),
        database,  # type: ignore[arg-type]
        0,
        4,
        checksum,
    )
    first_expiry = first.expires_at
    second = await upload_api.put_upload_part(
        upload.knowledge_base_id,
        upload.id,
        b"test",
        None,
        make_administrator(),
        database,  # type: ignore[arg-type]
        0,
        4,
        checksum,
    )

    assert first.next_offset == second.next_offset == 4
    assert await file_size(tmp_path, upload.temporary_storage_key) == 4
    assert len(database.receipts) == 1
    assert second.expires_at >= first_expiry
    assert database.commit_count == 2


@pytest.mark.asyncio
async def test_conflicting_retry_at_confirmed_offset_is_rejected(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    upload = make_upload(tmp_path, total_bytes=4)
    database = FakeUploadDatabase([upload, upload])
    monkeypatch.setattr(upload_api, "get_settings", lambda: settings)
    checksum = hashlib.sha256(b"test").hexdigest()
    await upload_api.put_upload_part(
        upload.knowledge_base_id,
        upload.id,
        b"test",
        None,
        make_administrator(),
        database,  # type: ignore[arg-type]
        0,
        4,
        checksum,
    )

    with pytest.raises(HTTPException, match="already confirmed") as error:
        await upload_api.put_upload_part(
            upload.knowledge_base_id,
            upload.id,
            b"evil",
            None,
            make_administrator(),
            database,  # type: ignore[arg-type]
            0,
            4,
            hashlib.sha256(b"evil").hexdigest(),
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_completion_creates_one_document_and_is_idempotent(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    upload = make_upload(tmp_path, total_bytes=4)
    upload.received_bytes = 4
    database = FakeUploadDatabase([upload, 0, None, upload])
    monkeypatch.setattr(upload_api, "get_settings", lambda: settings)
    enqueued: list[uuid.UUID] = []
    monkeypatch.setattr(
        upload_api,
        "enqueue_ingestion",
        lambda document_id: enqueued.append(document_id) is None or True,
    )
    await append_part(tmp_path, upload.temporary_storage_key, 0, b"test")

    first = await upload_api.complete_upload_session(
        upload.knowledge_base_id,
        upload.id,
        None,
        make_administrator(),
        database,  # type: ignore[arg-type]
    )
    second = await upload_api.complete_upload_session(
        upload.knowledge_base_id,
        upload.id,
        None,
        make_administrator(),
        database,  # type: ignore[arg-type]
    )

    assert first.id == second.id == upload.resulting_document_id
    assert upload.status == UploadSessionStatus.COMPLETED
    assert await file_size(tmp_path, upload.temporary_storage_key) is None
    assert await file_size(tmp_path, upload.final_storage_key) == 4
    assert enqueued == [first.id]
    assert first.source_kind == "text"
    assert first.source_path == "docs/notes.txt"
    assert first.tags == ["operations"]
    assert first.source_metadata["title"] == "Notes"
    assert first.ingestion_stage == "queued"
    assert first.ingestion_progress == 0
