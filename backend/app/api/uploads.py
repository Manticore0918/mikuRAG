import asyncio
import hashlib
import hmac
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response, status
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_knowledge_base
from app.config import Settings, get_settings
from app.dependencies import Administrator, DatabaseSession
from app.ingestion.dispatch import enqueue_ingestion
from app.ingestion.errors import UploadValidationError
from app.ingestion.storage import (
    ALLOWED_SUFFIXES,
    language_for_suffix,
    safe_original_name,
    source_kind_for_suffix,
    storage_path,
    supported_formats_message,
    validate_file_format,
)
from app.models import (
    Document,
    DocumentStatus,
    UploadPartReceipt,
    UploadSession,
    UploadSessionStatus,
    User,
)
from app.schemas import (
    DocumentRead,
    UploadPartRead,
    UploadSessionCreate,
    UploadSessionRead,
)
from app.security import require_csrf
from app.uploads.cleanup import reconcile_upload_sessions
from app.uploads.storage import (
    UploadCheckpointError,
    append_part,
    hash_file,
    move_file,
    remove_file,
    storage_keys,
)

router = APIRouter(
    prefix="/admin/knowledge-bases",
    tags=["resumable document uploads"],
)
CsrfCheck = Annotated[None, Depends(require_csrf)]
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
UPLOAD_CAPACITY_LOCK = 6_837_727_246


def _expiry(settings: Settings) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=settings.upload_session_ttl_seconds)


def _read_upload(upload: UploadSession, username: str | None) -> UploadSessionRead:
    return UploadSessionRead(
        id=upload.id,
        knowledge_base_id=upload.knowledge_base_id,
        initiated_by_id=upload.initiated_by_id,
        initiated_by_username=username,
        original_name=upload.original_name,
        source_kind=upload.source_kind or source_kind_for_suffix(upload.suffix),
        language=upload.language,
        tags=list(upload.tags or []),
        source_uri=upload.source_uri,
        source_path=upload.source_path,
        source_metadata=dict(upload.source_metadata or {}),
        declared_sha256=upload.declared_sha256,
        total_bytes=upload.total_bytes,
        received_bytes=upload.received_bytes,
        part_size_bytes=upload.part_size_bytes,
        status=upload.status,
        safe_error=upload.safe_error,
        resulting_document_id=upload.resulting_document_id,
        expires_at=upload.expires_at,
        created_at=upload.created_at,
        updated_at=upload.updated_at,
    )


async def _username(session: AsyncSession, upload: UploadSession) -> str | None:
    if upload.initiated_by_id is None:
        return None
    return await session.scalar(select(User.username).where(User.id == upload.initiated_by_id))


async def _require_upload(
    session: AsyncSession,
    knowledge_base_id: uuid.UUID,
    upload_id: uuid.UUID,
    *,
    lock: bool = False,
) -> UploadSession:
    statement = select(UploadSession).where(
        UploadSession.id == upload_id,
        UploadSession.knowledge_base_id == knowledge_base_id,
    )
    if lock:
        statement = statement.with_for_update()
    upload = await session.scalar(statement)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload Session not found",
        )
    return upload


async def _delete_expired_upload(
    session: AsyncSession, upload: UploadSession, settings: Settings
) -> None:
    await remove_file(settings.upload_dir, upload.temporary_storage_key)
    if upload.status != UploadSessionStatus.COMPLETED:
        await remove_file(settings.upload_dir, upload.final_storage_key)
    await session.delete(upload)
    await session.commit()
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="This Upload Session expired. Start the upload again.",
    )


async def _require_open(
    session: AsyncSession, upload: UploadSession, settings: Settings
) -> None:
    if upload.expires_at <= datetime.now(UTC):
        await _delete_expired_upload(session, upload, settings)
    if upload.status != UploadSessionStatus.OPEN:
        detail = upload.safe_error or "This Upload Session is no longer open"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


async def _terminal_failure(
    session: AsyncSession,
    upload: UploadSession,
    settings: Settings,
    detail: str,
) -> None:
    await remove_file(settings.upload_dir, upload.temporary_storage_key)
    await remove_file(settings.upload_dir, upload.final_storage_key)
    await session.execute(
        delete(UploadPartReceipt).where(UploadPartReceipt.upload_session_id == upload.id)
    )
    upload.status = UploadSessionStatus.FAILED
    upload.safe_error = detail
    upload.expires_at = _expiry(settings)
    await session.commit()


@router.get(
    "/{knowledge_base_id}/document-uploads",
    response_model=list[UploadSessionRead],
)
async def list_upload_sessions(
    knowledge_base_id: uuid.UUID,
    _: Administrator,
    session: DatabaseSession,
) -> list[UploadSessionRead]:
    await require_knowledge_base(session, knowledge_base_id)
    rows = await session.execute(
        select(UploadSession, User.username)
        .outerjoin(User, User.id == UploadSession.initiated_by_id)
        .where(
            UploadSession.knowledge_base_id == knowledge_base_id,
            UploadSession.status != UploadSessionStatus.COMPLETED,
            UploadSession.expires_at > datetime.now(UTC),
        )
        .order_by(UploadSession.created_at.desc())
    )
    return [_read_upload(upload, username) for upload, username in rows.all()]


@router.get(
    "/{knowledge_base_id}/document-uploads/{upload_id}",
    response_model=UploadSessionRead,
)
async def get_upload_session(
    knowledge_base_id: uuid.UUID,
    upload_id: uuid.UUID,
    _: Administrator,
    session: DatabaseSession,
) -> UploadSessionRead:
    upload = await _require_upload(session, knowledge_base_id, upload_id)
    return _read_upload(upload, await _username(session, upload))


@router.post(
    "/{knowledge_base_id}/document-uploads",
    response_model=UploadSessionRead,
)
async def create_upload_session(
    knowledge_base_id: uuid.UUID,
    payload: UploadSessionCreate,
    _csrf: CsrfCheck,
    administrator: Administrator,
    session: DatabaseSession,
) -> UploadSessionRead:
    settings = get_settings()
    await require_knowledge_base(session, knowledge_base_id)
    await reconcile_upload_sessions(session, settings, remove_orphans=False)
    suffix = Path(payload.original_name.lower()).suffix
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=supported_formats_message(),
        )
    if payload.size_bytes > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Documents cannot exceed 50 MB",
        )

    existing_document = await session.scalar(
        select(Document.id).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.sha256 == payload.sha256,
        )
    )
    if existing_document is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Document already exists in the selected Knowledge Base",
        )

    existing = await session.scalar(
        select(UploadSession).where(
            UploadSession.knowledge_base_id == knowledge_base_id,
            UploadSession.declared_sha256 == payload.sha256,
            UploadSession.status == UploadSessionStatus.OPEN,
        )
    )
    if existing is not None:
        if existing.total_bytes != payload.size_bytes:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The selected file does not match the existing Upload Session",
            )
        if (
            existing.language != (language_for_suffix(suffix) or payload.language)
            or existing.tags != payload.tags
            or existing.source_uri != payload.source_uri
            or existing.source_path != _source_path(payload, suffix)
            or existing.source_metadata != payload.metadata
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This file already has an open Upload Session with different source metadata"
                ),
            )
        existing.expires_at = _expiry(settings)
        await session.commit()
        await session.refresh(existing)
        return _read_upload(existing, await _username(session, existing))

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": UPLOAD_CAPACITY_LOCK},
    )
    active_count = await session.scalar(
        select(func.count())
        .select_from(UploadSession)
        .where(UploadSession.status == UploadSessionStatus.OPEN)
    )
    if active_count is not None and active_count >= settings.max_active_upload_sessions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The Installation already has "
                f"{settings.max_active_upload_sessions} active Upload Sessions"
            ),
        )

    upload_id = uuid.uuid4()
    temporary_key, final_key = storage_keys(upload_id)
    upload = UploadSession(
        id=upload_id,
        knowledge_base_id=knowledge_base_id,
        initiated_by_id=administrator.id,
        original_name=safe_original_name(payload.original_name, suffix),
        suffix=suffix,
        source_kind=source_kind_for_suffix(suffix),
        language=language_for_suffix(suffix) or payload.language,
        tags=payload.tags,
        source_uri=payload.source_uri,
        source_path=_source_path(payload, suffix),
        source_metadata=payload.metadata,
        declared_sha256=payload.sha256,
        total_bytes=payload.size_bytes,
        received_bytes=0,
        part_size_bytes=settings.upload_part_bytes,
        temporary_storage_key=temporary_key,
        final_storage_key=final_key,
        status=UploadSessionStatus.OPEN,
        expires_at=_expiry(settings),
    )
    session.add(upload)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        existing = await session.scalar(
            select(UploadSession).where(
                UploadSession.knowledge_base_id == knowledge_base_id,
                UploadSession.declared_sha256 == payload.sha256,
                UploadSession.status == UploadSessionStatus.OPEN,
            )
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An Upload Session conflict occurred. Try again.",
            ) from error
        return _read_upload(existing, await _username(session, existing))
    await session.refresh(upload)
    return _read_upload(upload, administrator.username)


@router.post(
    "/{knowledge_base_id}/document-uploads/{upload_id}/resume",
    response_model=UploadSessionRead,
)
async def resume_upload_session(
    knowledge_base_id: uuid.UUID,
    upload_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> UploadSessionRead:
    settings = get_settings()
    upload = await _require_upload(session, knowledge_base_id, upload_id, lock=True)
    await _require_open(session, upload, settings)
    upload.expires_at = _expiry(settings)
    await session.commit()
    await session.refresh(upload)
    return _read_upload(upload, await _username(session, upload))


@router.put(
    "/{knowledge_base_id}/document-uploads/{upload_id}/parts",
    response_model=UploadPartRead,
)
async def put_upload_part(
    knowledge_base_id: uuid.UUID,
    upload_id: uuid.UUID,
    content: Annotated[bytes, Body(media_type="application/octet-stream")],
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
    upload_offset: Annotated[int, Header(alias="X-Upload-Offset", ge=0)],
    upload_length: Annotated[int, Header(alias="X-Upload-Length", gt=0)],
    upload_sha256: Annotated[str, Header(alias="X-Upload-SHA256")],
) -> UploadPartRead:
    settings = get_settings()
    checksum = upload_sha256.lower()
    if SHA256_PATTERN.fullmatch(checksum) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Upload Part SHA-256 is invalid",
        )
    upload = await _require_upload(session, knowledge_base_id, upload_id, lock=True)
    await _require_open(session, upload, settings)

    receipt = await session.get(
        UploadPartReceipt,
        {"upload_session_id": upload.id, "offset_bytes": upload_offset},
    )
    if receipt is not None:
        if receipt.length_bytes != upload_length or not hmac.compare_digest(
            receipt.sha256, checksum
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Different bytes were already confirmed at this Upload Part offset",
            )
        upload.expires_at = _expiry(settings)
        await session.commit()
        return UploadPartRead(next_offset=upload.received_bytes, expires_at=upload.expires_at)

    if upload_offset != upload.received_bytes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The next confirmed Upload Part offset is {upload.received_bytes}",
        )
    expected_length = min(upload.part_size_bytes, upload.total_bytes - upload_offset)
    if upload_length != expected_length or len(content) != expected_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"This Upload Part must contain exactly {expected_length} bytes",
        )
    actual_checksum = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(actual_checksum, checksum):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Upload Part checksum did not match its bytes",
        )

    try:
        await append_part(
            settings.upload_dir,
            upload.temporary_storage_key,
            upload_offset,
            content,
        )
    except UploadCheckpointError as error:
        await _terminal_failure(session, upload, settings, str(error))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    receipt = UploadPartReceipt(
        upload_session_id=upload.id,
        offset_bytes=upload_offset,
        length_bytes=upload_length,
        sha256=checksum,
    )
    session.add(receipt)
    upload.received_bytes += upload_length
    upload.expires_at = _expiry(settings)
    try:
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        raise
    return UploadPartRead(next_offset=upload.received_bytes, expires_at=upload.expires_at)


@router.post(
    "/{knowledge_base_id}/document-uploads/{upload_id}/complete",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_upload_session(
    knowledge_base_id: uuid.UUID,
    upload_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> Document:
    settings = get_settings()
    upload = await _require_upload(session, knowledge_base_id, upload_id, lock=True)
    if upload.status == UploadSessionStatus.COMPLETED:
        if upload.resulting_document_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Completed Upload Session has no resulting Document",
            )
        document = await session.get(Document, upload.resulting_document_id)
        if document is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resulting Document not found",
            )
        return document
    await _require_open(session, upload, settings)
    if upload.received_bytes != upload.total_bytes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Upload is incomplete; next confirmed offset is {upload.received_bytes}",
        )

    try:
        actual_size, actual_sha256 = await hash_file(
            settings.upload_dir, upload.temporary_storage_key
        )
    except FileNotFoundError as error:
        detail = "The temporary upload could not be found. Start this upload again."
        await _terminal_failure(session, upload, settings, detail)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from error
    if actual_size != upload.total_bytes or not hmac.compare_digest(
        actual_sha256, upload.declared_sha256
    ):
        detail = "Completed upload size or checksum did not match the selected file"
        await _terminal_failure(session, upload, settings, detail)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )
    try:
        media_type = await asyncio.to_thread(
            validate_file_format,
            storage_path(settings.upload_dir, upload.temporary_storage_key),
            upload.suffix,
        )
    except UploadValidationError as error:
        await _terminal_failure(session, upload, settings, error.safe_message)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=error.safe_message,
        ) from error

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": UPLOAD_CAPACITY_LOCK + 1},
    )
    document_count = await session.scalar(select(func.count()).select_from(Document))
    if document_count is not None and document_count >= 10_000:
        detail = "The Installation has reached the 10,000 Document MVP limit"
        await _terminal_failure(session, upload, settings, detail)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    duplicate = await session.scalar(
        select(Document.id).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.sha256 == upload.declared_sha256,
        )
    )
    if duplicate is not None:
        detail = "This Document already exists in the selected Knowledge Base"
        await _terminal_failure(session, upload, settings, detail)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    await move_file(
        settings.upload_dir,
        upload.temporary_storage_key,
        upload.final_storage_key,
    )
    document = Document(
        id=uuid.uuid4(),
        knowledge_base_id=knowledge_base_id,
        original_name=upload.original_name,
        storage_key=upload.final_storage_key,
        sha256=upload.declared_sha256,
        media_type=media_type,
        size_bytes=upload.total_bytes,
        status=DocumentStatus.PENDING,
        source_kind=upload.source_kind or source_kind_for_suffix(upload.suffix),
        language=upload.language,
        tags=list(upload.tags or []),
        source_uri=upload.source_uri,
        source_path=upload.source_path,
        source_metadata=dict(upload.source_metadata or {}),
        ingestion_stage="queued",
        ingestion_progress=0,
        ingestion_attempts=0,
        ingestion_warnings=[],
    )
    session.add(document)
    upload.status = UploadSessionStatus.COMPLETED
    upload.safe_error = None
    upload.resulting_document_id = document.id
    upload.expires_at = _expiry(settings)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        try:
            await move_file(
                settings.upload_dir,
                upload.final_storage_key,
                upload.temporary_storage_key,
            )
        except FileNotFoundError:
            pass
        upload = await _require_upload(session, knowledge_base_id, upload_id, lock=True)
        detail = "This Document already exists in the selected Knowledge Base"
        await _terminal_failure(session, upload, settings, detail)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from error
    except SQLAlchemyError:
        await session.rollback()
        try:
            await move_file(
                settings.upload_dir,
                upload.final_storage_key,
                upload.temporary_storage_key,
            )
        except FileNotFoundError:
            pass
        raise
    await session.refresh(document)

    if not enqueue_ingestion(document.id):
        document.status = DocumentStatus.FAILED
        document.ingestion_stage = "failed"
        document.safe_error = "The ingestion queue is unavailable. Retry this Document later."
        await session.commit()
        await session.refresh(document)
    return document


def _source_path(payload: UploadSessionCreate, suffix: str) -> str | None:
    if payload.source_path:
        return payload.source_path
    if source_kind_for_suffix(suffix) == "code":
        return safe_original_name(payload.original_name, suffix)
    return None


@router.delete(
    "/{knowledge_base_id}/document-uploads/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_upload_session(
    knowledge_base_id: uuid.UUID,
    upload_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> Response:
    settings = get_settings()
    upload = await _require_upload(session, knowledge_base_id, upload_id, lock=True)
    if upload.status == UploadSessionStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A completed Upload Session cannot be cancelled",
        )
    await remove_file(settings.upload_dir, upload.temporary_storage_key)
    await remove_file(settings.upload_dir, upload.final_storage_key)
    await session.delete(upload)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
