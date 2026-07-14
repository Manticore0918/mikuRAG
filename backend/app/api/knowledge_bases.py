import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.dependencies import CurrentUser, DatabaseSession, ensure_knowledge_base_access
from app.models import KnowledgeBase, KnowledgeBaseAccess
from app.schemas import KnowledgeBaseRead

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
