from io import BytesIO

import pytest
from fastapi import UploadFile

from app.ingestion.errors import UploadValidationError
from app.ingestion.storage import persist_upload, storage_path


@pytest.mark.asyncio
async def test_upload_uses_opaque_storage_key_and_preserves_safe_display_name(tmp_path) -> None:
    upload = UploadFile(file=BytesIO(b"Grounded text"), filename="../../private/notes.txt")
    stored = await persist_upload(upload, tmp_path, 1024)

    assert stored.original_name == "notes.txt"
    assert "notes.txt" not in stored.storage_key
    assert storage_path(tmp_path, stored.storage_key).read_text() == "Grounded text"


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file_and_removes_temporary_bytes(tmp_path) -> None:
    upload = UploadFile(file=BytesIO(b"too large"), filename="notes.txt")
    with pytest.raises(UploadValidationError, match="50 MB"):
        await persist_upload(upload, tmp_path, 4)

    assert not list((tmp_path / ".tmp").glob("*.upload"))


@pytest.mark.asyncio
async def test_upload_rejects_binary_text(tmp_path) -> None:
    upload = UploadFile(file=BytesIO(b"text\x00binary"), filename="notes.md")
    with pytest.raises(UploadValidationError, match="binary"):
        await persist_upload(upload, tmp_path, 1024)


def test_storage_path_rejects_traversal(tmp_path) -> None:
    with pytest.raises(UploadValidationError):
        storage_path(tmp_path, "../outside")
