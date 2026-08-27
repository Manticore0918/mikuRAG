import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.dependencies import CurrentUser, DatabaseSession, ensure_knowledge_base_access
from app.models import Document, DocumentStatus, KnowledgeBase, KnowledgeBaseAccess
from app.schemas import KnowledgeBaseRead, RetrievalDocumentRead

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge bases"])


@router.get("", response_model=list[KnowledgeBaseRead])
async def list_accessible_knowledge_bases(
    current_user: CurrentUser,
    session: DatabaseSession,
) -> list[KnowledgeBase]:
    statement = select(KnowledgeBase)
    if not current_user.is_administrator:
        statement = statement.join(KnowledgeBaseAccess).where(
            KnowledgeBaseAccess.user_id == current_user.id
        )
    result = await session.scalars(statement.order_by(KnowledgeBase.name))
    return list(result)


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
async def get_knowledge_base(
    knowledge_base_id: uuid.UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> KnowledgeBase:
    await ensure_knowledge_base_access(session, current_user, knowledge_base_id)
    knowledge_base = await session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge Base not found"
        )
    return knowledge_base


@router.get(
    "/{knowledge_base_id}/retrieval-documents",
    response_model=list[RetrievalDocumentRead],
)
async def list_retrieval_documents(
    knowledge_base_id: uuid.UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> list[RetrievalDocumentRead]:
    """List only safe, Ready Document metadata for the per-turn filter UI."""
    await ensure_knowledge_base_access(session, current_user, knowledge_base_id)
    documents = await session.scalars(
        select(Document)
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == DocumentStatus.READY,
        )
        .order_by(Document.original_name, Document.id)
    )
    return [
        RetrievalDocumentRead(
            id=document.id,
            original_name=document.original_name,
            source_kind=document.source_kind,
            language=document.language,
            tags=list(document.tags),
            ingested_at=document.created_at,
        )
        for document in documents
    ]
