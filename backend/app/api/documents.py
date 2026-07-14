import logging
import uuid
from typing import Annotated

from celery.exceptions import CeleryError
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.admin import require_knowledge_base
from app.config import get_settings
from app.dependencies import Administrator, DatabaseSession
from app.ingestion.errors import UploadValidationError
from app.ingestion.storage import persist_upload, remove_stored_file
from app.ingestion.tasks import ingest_document, purge_document
from app.models import Document, DocumentStatus
from app.schemas import DocumentRead
from app.security import require_csrf

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/knowledge-bases", tags=["document administration"])
CsrfCheck = Annotated[None, Depends(require_csrf)]


async def require_document(
    session: DatabaseSession,
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
) -> Document:
    document = await session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.knowledge_base_id == knowledge_base_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def _enqueue_ingestion(document: Document) -> bool:
    try:
        ingest_document.delay(str(document.id))
        return True
    except (CeleryError, KombuOperationalError, RedisError, OSError):
        logger.error("Could not enqueue Document ingestion for %s", document.id)
        return False


@router.get("/{knowledge_base_id}/documents", response_model=list[DocumentRead])
async def list_documents(
    knowledge_base_id: uuid.UUID,
    _: Administrator,
    session: DatabaseSession,
) -> list[Document]:
    await require_knowledge_base(session, knowledge_base_id)
    result = await session.scalars(
        select(Document)
        .where(Document.knowledge_base_id == knowledge_base_id)
        .order_by(Document.created_at.desc())
    )
    return list(result)


@router.post(
    "/{knowledge_base_id}/documents",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    knowledge_base_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
    file: Annotated[UploadFile, File(description="PDF, DOCX, TXT, or Markdown")],
) -> Document:
    settings = get_settings()
    await require_knowledge_base(session, knowledge_base_id)
    document_count = await session.scalar(select(func.count()).select_from(Document))
    if document_count is not None and document_count >= 10_000:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Installation has reached the 10,000 Document MVP limit",
        )
    try:
        stored = await persist_upload(file, settings.upload_dir, settings.max_upload_bytes)
    except UploadValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=error.safe_message,
        ) from error

    document = Document(
        knowledge_base_id=knowledge_base_id,
        original_name=stored.original_name,
        storage_key=stored.storage_key,
        sha256=stored.sha256,
        media_type=stored.media_type,
        size_bytes=stored.size_bytes,
        status=DocumentStatus.PENDING,
    )
    session.add(document)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        await remove_stored_file(settings.upload_dir, stored.storage_key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Document already exists in the selected Knowledge Base",
        ) from error
    except SQLAlchemyError:
        await session.rollback()
        await remove_stored_file(settings.upload_dir, stored.storage_key)
        raise
    await session.refresh(document)
    if not _enqueue_ingestion(document):
        document.status = DocumentStatus.FAILED
        document.safe_error = "The ingestion queue is unavailable. Retry this Document later."
        await session.commit()
        await session.refresh(document)
    return document


@router.post(
    "/{knowledge_base_id}/documents/{document_id}/retry",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_document(
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> Document:
    document = await require_document(session, knowledge_base_id, document_id)
    if document.status != DocumentStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only Failed Documents can be retried",
        )
    document.status = DocumentStatus.PENDING
    document.safe_error = None
    await session.commit()
    await session.refresh(document)
    if not _enqueue_ingestion(document):
        document.status = DocumentStatus.FAILED
        document.safe_error = "The ingestion queue is unavailable. Retry this Document later."
        await session.commit()
        await session.refresh(document)
    return document


@router.delete(
    "/{knowledge_base_id}/documents/{document_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_document(
    knowledge_base_id: uuid.UUID,
    document_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> Response:
    document = await require_document(session, knowledge_base_id, document_id)
    if document.status != DocumentStatus.DELETING:
        document.status = DocumentStatus.DELETING
        document.safe_error = None
        await session.commit()
    try:
        purge_document.delay(str(document.id))
    except (CeleryError, KombuOperationalError, RedisError, OSError) as error:
        logger.error("Could not enqueue Document purge for %s", document.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The deletion queue is unavailable. Try again later.",
        ) from error
    return Response(status_code=status.HTTP_202_ACCEPTED)
