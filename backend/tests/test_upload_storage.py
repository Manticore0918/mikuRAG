import hashlib
import os
import time
import uuid

import pytest

from app.uploads.storage import (
    UploadCheckpointError,
    append_part,
    hash_file,
    orphan_temporary_keys,
    storage_keys,
)


@pytest.mark.asyncio
async def test_parts_append_from_confirmed_offsets_and_produce_whole_digest(tmp_path) -> None:
    temporary_key, final_key = storage_keys(uuid.uuid4())
    assert temporary_key.startswith("upload-sessions/")
    assert final_key.startswith("documents/")

    await append_part(tmp_path, temporary_key, 0, b"grounded ")
    await append_part(tmp_path, temporary_key, 9, b"answer")

    size, digest = await hash_file(tmp_path, temporary_key)
    assert size == 15
    assert digest == hashlib.sha256(b"grounded answer").hexdigest()


@pytest.mark.asyncio
async def test_retry_after_uncommitted_write_truncates_back_to_checkpoint(tmp_path) -> None:
    temporary_key, _ = storage_keys(uuid.uuid4())
    await append_part(tmp_path, temporary_key, 0, b"first-corrupt-tail")

    await append_part(tmp_path, temporary_key, 0, b"first")
    await append_part(tmp_path, temporary_key, 5, b"-second")

    size, digest = await hash_file(tmp_path, temporary_key)
    assert size == 12
    assert digest == hashlib.sha256(b"first-second").hexdigest()


@pytest.mark.asyncio
async def test_part_cannot_skip_past_confirmed_checkpoint(tmp_path) -> None:
    temporary_key, _ = storage_keys(uuid.uuid4())
    with pytest.raises(UploadCheckpointError, match="shorter"):
        await append_part(tmp_path, temporary_key, 5, b"gap")


@pytest.mark.asyncio
async def test_orphan_scan_gives_new_writes_a_grace_period(tmp_path) -> None:
    temporary_key, _ = storage_keys(uuid.uuid4())
    await append_part(tmp_path, temporary_key, 0, b"uncommitted")

    assert await orphan_temporary_keys(tmp_path, set()) == []

    path = tmp_path / temporary_key
    old_time = time.time() - 301
    os.utime(path, (old_time, old_time))
    assert await orphan_temporary_keys(tmp_path, set()) == [temporary_key]
