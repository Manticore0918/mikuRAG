import asyncio
import hashlib
import os
import time
import uuid
from pathlib import Path

from app.ingestion.storage import storage_path


class UploadCheckpointError(RuntimeError):
    pass


def storage_keys(upload_id: uuid.UUID) -> tuple[str, str]:
    identity = upload_id.hex
    return (
        f"upload-sessions/{identity}.upload",
        f"documents/{identity[:2]}/{identity}",
    )


def _append_part(path: Path, offset: int, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "r+b" if path.exists() else "w+b"
    with path.open(mode) as destination:
        destination.seek(0, os.SEEK_END)
        current_size = destination.tell()
        if current_size > offset:
            destination.truncate(offset)
        elif current_size < offset:
            raise UploadCheckpointError(
                "The temporary upload is shorter than its confirmed checkpoint"
            )
        destination.seek(offset)
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())


async def append_part(upload_dir: Path, storage_key: str, offset: int, content: bytes) -> None:
    path = storage_path(upload_dir, storage_key)
    await asyncio.to_thread(_append_part, path, offset, content)


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


async def hash_file(upload_dir: Path, storage_key: str) -> tuple[int, str]:
    return await asyncio.to_thread(_hash_file, storage_path(upload_dir, storage_key))


def _move(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


async def move_file(upload_dir: Path, source_key: str, destination_key: str) -> None:
    await asyncio.to_thread(
        _move,
        storage_path(upload_dir, source_key),
        storage_path(upload_dir, destination_key),
    )


async def remove_file(upload_dir: Path, storage_key: str) -> None:
    path = storage_path(upload_dir, storage_key)
    try:
        await asyncio.to_thread(path.unlink)
    except FileNotFoundError:
        pass


async def file_size(upload_dir: Path, storage_key: str) -> int | None:
    path = storage_path(upload_dir, storage_key)
    try:
        return await asyncio.to_thread(lambda: path.stat().st_size)
    except FileNotFoundError:
        return None


def _truncate(path: Path, size: int) -> None:
    with path.open("r+b") as handle:
        handle.truncate(size)
        handle.flush()
        os.fsync(handle.fileno())


async def truncate_file(upload_dir: Path, storage_key: str, size: int) -> None:
    await asyncio.to_thread(_truncate, storage_path(upload_dir, storage_key), size)


async def orphan_temporary_keys(
    upload_dir: Path,
    known_keys: set[str],
    *,
    minimum_age_seconds: int = 300,
) -> list[str]:
    root = storage_path(upload_dir, "upload-sessions")

    def scan() -> list[str]:
        if not root.exists():
            return []
        resolved_upload_dir = upload_dir.resolve()
        oldest_allowed_mtime = time.time() - minimum_age_seconds
        orphans: list[str] = []
        for path in root.glob("*.upload"):
            try:
                storage_key = path.relative_to(resolved_upload_dir).as_posix()
                modified_at = path.stat().st_mtime
            except FileNotFoundError:
                continue
            if storage_key not in known_keys and modified_at <= oldest_allowed_mtime:
                orphans.append(storage_key)
        return orphans

    return await asyncio.to_thread(scan)
