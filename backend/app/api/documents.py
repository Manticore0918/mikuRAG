import logging
import uuid
from typing import Annotated

from celery.exceptions import CeleryError
from fastapi import APIRouter, Depends, HTTPException, Response, status
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError
from sqlalchemy import select, update

from app.api.admin import require_knowledge_base
from app.dependencies import Administrator, DatabaseSession
from app.ingestion.dispatch import enqueue_ingestion
from app.ingestion.tasks import purge_document
from app.models import Document, DocumentStatus, KnowledgeBase
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
    document.ingestion_stage = "queued"
    document.ingestion_progress = 0
    document.ingestion_warnings = []
    await session.commit()
    await session.refresh(document)
    if not enqueue_ingestion(document.id):
        document.status = DocumentStatus.FAILED
        document.ingestion_stage = "failed"
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
        document.ingestion_stage = "deleting"
        document.safe_error = None
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == knowledge_base_id)
            .values(index_generation=KnowledgeBase.index_generation + 1)
        )
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
