import asyncio
import hashlib
import os
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import aiofiles
import aiofiles.os
from fastapi import UploadFile

from app.ingestion.errors import UploadValidationError

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".htm": "text/html",
    ".html": "text/html",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".jsx": "text/jsx",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".ts": "text/typescript",
    ".tsx": "text/tsx",
}
ALLOWED_SUFFIXES = frozenset(MEDIA_TYPES)
SOURCE_KINDS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".htm": "html",
    ".html": "html",
    ".py": "code",
    ".js": "code",
    ".jsx": "code",
    ".mjs": "code",
    ".cjs": "code",
    ".ts": "code",
    ".tsx": "code",
}
DEFAULT_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}
MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class StoredUpload:
    original_name: str
    storage_key: str
    media_type: str
    size_bytes: int
    sha256: str


def safe_original_name(filename: str | None, suffix: str) -> str:
    candidate = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    candidate = "".join(character for character in candidate if character.isprintable()).strip()
    if not candidate:
        candidate = f"document{suffix}"
    return candidate[:255]


def storage_path(upload_dir: Path, storage_key: str) -> Path:
    root = upload_dir.resolve()
    candidate = (root / storage_key).resolve()
    if not candidate.is_relative_to(root):
        raise UploadValidationError("Stored Document path is invalid")
    return candidate


def validate_file_format(path: Path, suffix: str) -> str:
    if suffix not in ALLOWED_SUFFIXES:
        raise UploadValidationError("This Document format is not supported")
    if suffix == ".pdf":
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise UploadValidationError("The uploaded file is not a valid PDF")
    elif suffix == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                expanded_size = sum(item.file_size for item in archive.infolist())
                if expanded_size > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise UploadValidationError("The DOCX Document expands beyond the safe limit")
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise UploadValidationError("The uploaded file is not a valid DOCX document")
        except zipfile.BadZipFile as error:
            raise UploadValidationError(
                "The uploaded file is not a valid DOCX document"
            ) from error
    else:
        content = path.read_bytes()
        if b"\x00" in content:
            raise UploadValidationError("Text Documents cannot contain binary data")
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise UploadValidationError("Text Documents must use UTF-8 encoding") from error
    return MEDIA_TYPES[suffix]


def source_kind_for_suffix(suffix: str) -> str:
    try:
        return SOURCE_KINDS[suffix]
    except KeyError as error:
        raise UploadValidationError("This Document format is not supported") from error


def language_for_suffix(suffix: str) -> str | None:
    return DEFAULT_LANGUAGES.get(suffix)


def supported_formats_message() -> str:
    return (
        "Supported formats are PDF, DOCX, TXT, Markdown, HTML, Python, TypeScript, "
        "and JavaScript"
    )


async def persist_upload(
    upload: UploadFile,
    upload_dir: Path,
    max_bytes: int,
) -> StoredUpload:
    suffix = Path((upload.filename or "").lower()).suffix
    if suffix not in ALLOWED_SUFFIXES:
        raise UploadValidationError(supported_formats_message())

    root = await asyncio.to_thread(upload_dir.resolve)
    temporary_dir = root / ".tmp"
    await aiofiles.os.makedirs(temporary_dir, exist_ok=True)
    identity = uuid.uuid4().hex
    temporary_path = temporary_dir / f"{identity}.upload"
    storage_key = f"documents/{identity[:2]}/{identity}"
    final_path = storage_path(root, storage_key)
    digest = hashlib.sha256()
    size = 0

    try:
        async with aiofiles.open(temporary_path, "xb") as destination:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadValidationError("Documents cannot exceed 50 MB")
                digest.update(chunk)
                await destination.write(chunk)
        if size == 0:
            raise UploadValidationError("The uploaded Document is empty")
        media_type = await asyncio.to_thread(validate_file_format, temporary_path, suffix)
        await aiofiles.os.makedirs(final_path.parent, exist_ok=True)
        await aiofiles.os.replace(temporary_path, final_path)
    except Exception:
        try:
            await aiofiles.os.remove(temporary_path)
        except FileNotFoundError:
            pass
        raise
    finally:
        await upload.close()

    return StoredUpload(
        original_name=safe_original_name(upload.filename, suffix),
        storage_key=storage_key,
        media_type=media_type,
        size_bytes=size,
        sha256=digest.hexdigest(),
    )


async def remove_stored_file(upload_dir: Path, storage_key: str) -> None:
    path = storage_path(upload_dir, storage_key)
    try:
        await aiofiles.os.remove(path)
    except FileNotFoundError:
        return


def remove_stored_file_sync(upload_dir: Path, storage_key: str) -> None:
    path = storage_path(upload_dir, storage_key)
    try:
        os.remove(path)
    except FileNotFoundError:
        return
